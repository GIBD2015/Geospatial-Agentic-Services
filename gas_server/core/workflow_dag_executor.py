"""Autonomous Multi-Agent DAG Execution Engine & Self-Healing CRS/Schema Adapter.

Executes multi-step Geospatial Agentic Services (GAS 1.0.0) workflow plans as a
Directed Acyclic Graph (DAG) with:
- Topological wave scheduling & cycle detection via NetworkX
- Automatic upstream-to-downstream artifact propagation (`$step_id.artifacts[0]`)
- Self-healing Coordinate Reference System (CRS) inspection & automatic metric
  reprojection when downstream operations require projected units (meters/feet)
  on geographic (`EPSG:4326`) vector inputs.
"""

from __future__ import annotations

import base64
import copy
import json
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import networkx as nx


METRIC_INSTRUCTION_PATTERN = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(m|meter|meters|metre|metres|km|kilometer|kilometers|ft|feet|foot)\b"
    r"|\b(buffer|distance\s+matrix|nearest\s+neighbor|area\s+in\s+square\s+meters|rasterize.*meter)\b",
    re.IGNORECASE,
)

DATASET_DEPENDENT_AGENTS = {
    "exploratory_spatial_data_analysis_agent",
    "geospatial_data_inspection_agent",
    "web_mapping_app_agent",
    "mapping_agent",
    "map_projection_agent",
    "raster_agent",
    "spatial_analysis_agent",
    "spatial_statistics_agent",
    "vector_analysis_agent",
}


class WorkflowDagError(ValueError):
    """Raised when a workflow DAG is structurally invalid or contains cycles."""


def _infer_optimal_utm_epsg(lon: float, lat: float) -> int:
    """Compute the WGS84 UTM EPSG code from a centroid longitude and latitude."""
    zone = int((lon + 180.0) // 6.0) + 1
    zone = max(1, min(60, zone))
    return (32600 + zone) if lat >= 0 else (32700 + zone)


def inspect_geojson_crs_and_bbox(file_path: str | Path) -> dict[str, Any]:
    """Inspect a local GeoJSON or vector file to determine its CRS and bounding box."""
    path = Path(file_path)
    info: dict[str, Any] = {
        "path": str(path),
        "crs": "EPSG:4326",
        "is_geographic": True,
        "bbox": None,
        "feature_count": 0,
    }
    if not path.exists():
        return info

    try:
        import geopandas as gpd

        gdf = gpd.read_file(path)
        info["feature_count"] = int(len(gdf))
        if gdf.crs is not None:
            epsg = gdf.crs.to_epsg()
            info["crs"] = f"EPSG:{epsg}" if epsg else str(gdf.crs)
            info["is_geographic"] = bool(gdf.crs.is_geographic)
        if not gdf.empty and gdf.total_bounds is not None:
            bounds = [float(x) for x in gdf.total_bounds]
            if len(bounds) == 4:
                info["bbox"] = bounds
        return info
    except Exception:
        pass

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        features = raw.get("features") if isinstance(raw, dict) else []
        if isinstance(features, list):
            info["feature_count"] = len(features)
        crs_obj = raw.get("crs") if isinstance(raw, dict) else None
        if isinstance(crs_obj, dict):
            crs_name = str(crs_obj.get("properties", {}).get("name", ""))
            if "EPSG" in crs_name.upper():
                match = re.search(r"(\d{4,5})", crs_name)
                if match:
                    code = int(match.group(1))
                    info["crs"] = f"EPSG:{code}"
                    info["is_geographic"] = code in {4326, 4269, 4267}
    except Exception:
        pass

    return info


def auto_reproject_vector_dataset(
    input_path: str | Path,
    target_epsg: int | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Deterministically reproject a geographic vector dataset to a metric CRS (UTM or EPSG:5070)."""
    import geopandas as gpd

    src_path = Path(input_path)
    gdf = gpd.read_file(src_path)
    source_crs = f"EPSG:{gdf.crs.to_epsg()}" if (gdf.crs and gdf.crs.to_epsg()) else str(gdf.crs or "EPSG:4326")
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")

    if target_epsg is None:
        bounds = gdf.total_bounds if not gdf.empty else [-77.0, 40.0, -76.0, 41.0]
        lon_c = float((bounds[0] + bounds[2]) / 2.0)
        lat_c = float((bounds[1] + bounds[3]) / 2.0)
        if -125.0 <= lon_c <= -66.0 and 24.0 <= lat_c <= 50.0:
            target_epsg = 5070  # CONUS Albers Equal Area
        else:
            target_epsg = _infer_optimal_utm_epsg(lon_c, lat_c)

    reprojected = gdf.to_crs(epsg=target_epsg)
    dest_dir = Path(output_dir) if output_dir else src_path.parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_path = dest_dir / f"{src_path.stem}_epsg{target_epsg}.geojson"
    reprojected.to_file(out_path, driver="GeoJSON")

    return {
        "original_path": str(src_path),
        "reprojected_path": str(out_path),
        "source_crs": source_crs,
        "target_crs": f"EPSG:{target_epsg}",
        "feature_count": int(len(reprojected)),
    }


class GasWorkflowDagExecutor:
    """Validates, heals, and executes multi-agent GAS 1.0.0 workflow DAGs."""

    def __init__(
        self,
        *,
        step_runner: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        auto_heal_crs: bool = True,
        work_dir: str | Path | None = None,
    ) -> None:
        self.step_runner = step_runner
        self.auto_heal_crs = auto_heal_crs
        self.work_dir = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="gas_dag_"))
        self.work_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def normalize_workflow_plan(plan: dict[str, Any]) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
        """Normalize a workflow plan from either `steps` or Canvas `nodes` + `edges` format."""
        if not isinstance(plan, dict):
            raise WorkflowDagError("Workflow plan must be a JSON object.")

        raw_steps = plan.get("steps") or plan.get("nodes") or []
        if not isinstance(raw_steps, list) or not raw_steps:
            raise WorkflowDagError("Workflow plan must contain a non-empty 'steps' or 'nodes' list.")

        steps: list[dict[str, Any]] = []
        edges: list[tuple[str, str]] = []
        seen_ids: set[str] = set()

        for idx, item in enumerate(raw_steps, start=1):
            if not isinstance(item, dict):
                raise WorkflowDagError(f"Step at index {idx - 1} must be an object.")
            step_id = str(
                item.get("step_id")
                or item.get("id")
                or item.get("node_id")
                or f"step_{idx}"
            ).strip()
            if step_id in seen_ids:
                raise WorkflowDagError(f"Duplicate step_id '{step_id}' in workflow plan.")
            seen_ids.add(step_id)

            data_block = item.get("data") if isinstance(item.get("data"), dict) else {}
            agent_id = str(
                item.get("agent_id")
                or item.get("agentId")
                or data_block.get("agentId")
                or data_block.get("agent_id")
                or ""
            ).strip()
            if not agent_id:
                raise WorkflowDagError(f"Step '{step_id}' is missing 'agent_id'.")

            instructions = str(
                item.get("instructions")
                or item.get("query")
                or item.get("prompt")
                or data_block.get("instructions")
                or item.get("task", {}).get("instructions", "")
            ).strip()
            if not instructions:
                raise WorkflowDagError(f"Step '{step_id}' is missing task 'instructions'.")

            depends_on = (
                item.get("depends_on")
                or item.get("dependencies")
                or item.get("inputs_from")
                or []
            )
            if isinstance(depends_on, str):
                depends_on = [depends_on]

            normalized_step = {
                "step_id": step_id,
                "agent_id": agent_id,
                "instructions": instructions,
                "depends_on": [str(dep).strip() for dep in depends_on if str(dep).strip()],
                "input_datasets": list(
                    item.get("input_datasets")
                    or item.get("inputs", {}).get("input_datasets", [])
                    or data_block.get("inputDatasets", [])
                    or []
                ),
                "parameters": dict(item.get("parameters") or data_block.get("parameters") or {}),
                "credentials": dict(item.get("credentials") or data_block.get("credentials") or {}),
                "mode": str(item.get("mode") or item.get("task", {}).get("mode") or "sync"),
            }
            steps.append(normalized_step)
            for dep in normalized_step["depends_on"]:
                edges.append((dep, step_id))

        raw_edges = plan.get("edges") or []
        if isinstance(raw_edges, list):
            for edge in raw_edges:
                if isinstance(edge, dict):
                    src = str(edge.get("source") or edge.get("from") or "").strip()
                    dst = str(edge.get("target") or edge.get("to") or "").strip()
                    if src and dst and (src, dst) not in edges:
                        edges.append((src, dst))
                        for step in steps:
                            if step["step_id"] == dst and src not in step["depends_on"]:
                                step["depends_on"].append(src)

        return steps, edges

    @classmethod
    def build_and_validate_dag(cls, plan: dict[str, Any]) -> dict[str, Any]:
        """Build a NetworkX DiGraph, verify acyclicity, and compute topological execution waves."""
        steps, edges = cls.normalize_workflow_plan(plan)
        graph = nx.DiGraph()
        step_map = {step["step_id"]: step for step in steps}

        for step in steps:
            graph.add_node(step["step_id"], **step)

        for src, dst in edges:
            if src not in step_map:
                raise WorkflowDagError(f"Step '{dst}' depends on unknown upstream step '{src}'.")
            if dst not in step_map:
                raise WorkflowDagError(f"Edge references unknown target step '{dst}'.")
            graph.add_edge(src, dst)

        if not nx.is_directed_acyclic_graph(graph):
            cycle = nx.find_cycle(graph)
            raise WorkflowDagError(f"Workflow plan contains a dependency cycle: {cycle}")

        waves = [sorted(list(generation)) for generation in nx.topological_generations(graph)]
        topological_order = [node_id for wave in waves for node_id in wave]

        return {
            "valid": True,
            "node_count": len(steps),
            "edge_count": len(edges),
            "execution_waves": waves,
            "topological_order": topological_order,
            "steps": steps,
            "edges": [{"source": u, "target": v} for u, v in edges],
        }

    def _should_auto_heal_crs(self, step: dict[str, Any], dataset_path: str) -> bool:
        """Check if a step requires metric units while its input vector dataset is in geographic degrees."""
        if not self.auto_heal_crs:
            return False
        agent_id = step["agent_id"]
        if agent_id == "map_projection_agent":
            return False
        if agent_id not in {
            "spatial_analysis_agent",
            "vector_analysis_agent",
            "raster_agent",
            "spatial_statistics_agent",
        }:
            return False
        if not METRIC_INSTRUCTION_PATTERN.search(step["instructions"]):
            return False

        info = inspect_geojson_crs_and_bbox(dataset_path)
        return bool(info.get("is_geographic", False))

    def _extract_step_artifact_paths(self, step_id: str, response_payload: dict[str, Any]) -> list[str]:
        """Extract local or materialized artifact paths from a completed step response."""
        paths: list[str] = []
        outputs = response_payload.get("outputs") if isinstance(response_payload.get("outputs"), dict) else {}

        # 1. Direct local paths returned by internal GeoAgent.run()
        for key in ("dataset_paths", "output_files"):
            items = outputs.get(key)
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, str) and item not in paths:
                        paths.append(item)
        for key in ("dataset_path", "output_file"):
            val = outputs.get(key)
            if isinstance(val, str) and val not in paths:
                paths.append(val)

        # 2. Standard GAS 1.0.0 normalized artifacts array
        artifacts = outputs.get("artifacts")
        if isinstance(artifacts, list):
            for idx, art in enumerate(artifacts):
                if not isinstance(art, dict):
                    continue
                if isinstance(art.get("local_path"), str) and Path(art["local_path"]).exists():
                    if art["local_path"] not in paths:
                        paths.append(art["local_path"])
                    continue
                if art.get("encoding") == "base64" and isinstance(art.get("data"), str):
                    fname = art.get("filename") or f"{step_id}_artifact_{idx}.geojson"
                    dest = self.work_dir / fname
                    dest.write_bytes(base64.b64decode(art["data"]))
                    paths.append(str(dest))
                elif isinstance(art.get("url"), str):
                    paths.append(art["url"])

        return paths

    def _default_internal_step_runner(self, step_request: dict[str, Any]) -> dict[str, Any]:
        """Execute a step directly against the local GAS `SERVICE_REGISTRY`."""
        from gas_server.core.service_registry import SERVICE_REGISTRY
        from gas_server.core.service_core import _build_task_payload

        agent_id = step_request["agent_id"]
        if agent_id not in SERVICE_REGISTRY:
            raise WorkflowDagError(f"Agent '{agent_id}' is not registered in SERVICE_REGISTRY.")

        registration = SERVICE_REGISTRY[agent_id]
        credentials = step_request.get("credentials") or {}
        api_key = credentials.get("OPENAI_API_KEY") or credentials.get("GIBD_API_KEY")
        model = step_request.get("parameters", {}).get("model")

        agent_cls = registration.load_agent_class()
        agent = agent_cls(api_key=api_key, model=model)
        if hasattr(agent, "output_dir"):
            agent.output_dir = self.work_dir / agent_id
            Path(agent.output_dir).mkdir(parents=True, exist_ok=True)
        if hasattr(agent, "set_request_parameters"):
            merged_params = dict(step_request.get("parameters") or {})
            if credentials:
                merged_params.update(credentials)
            agent.set_request_parameters(merged_params)

        raw_result = registration.run_agent(
            agent,
            step_request["instructions"],
            step_request.get("input_datasets") or [],
        )

        normalized = _build_task_payload(
            task_id=f"dag-{step_request['step_id']}",
            agent_id=agent_id,
            agent_name=raw_result.get("agent_name", registration.agent_id),
            agent_version=raw_result.get("agent_version", "1.0.0"),
            state="TASK_STATE_COMPLETED",
            query=step_request["instructions"],
            requested_skill=None,
            result=raw_result,
            error_message=None,
            agent_id_for_artifacts=agent_id,
            output_delivery="url",
            public_base_url="https://www.geospatial-agentic-services.online",
        )
        # Preserve raw local dataset_paths for downstream steps in the same process
        raw_outputs = raw_result.get("outputs") if isinstance(raw_result.get("outputs"), dict) else {}
        local_paths = []
        if isinstance(raw_outputs.get("dataset_paths"), list):
            local_paths.extend(raw_outputs["dataset_paths"])
        for key in ("dataset_path", "output_file"):
            if isinstance(raw_outputs.get(key), str):
                local_paths.append(raw_outputs[key])
        if local_paths:
            normalized["outputs"]["dataset_paths"] = local_paths
        return normalized

    def execute(
        self,
        plan: dict[str, Any],
        *,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Validate and execute a complete multi-agent workflow DAG."""
        start_time = time.time()
        validation = self.build_and_validate_dag(plan)
        steps_by_id = {step["step_id"]: copy.deepcopy(step) for step in validation["steps"]}
        runner = self.step_runner or self._default_internal_step_runner

        step_results: dict[str, dict[str, Any]] = {}
        step_artifacts: dict[str, list[str]] = {}
        self_healing_actions: list[dict[str, Any]] = []

        if progress_callback:
            progress_callback(
                {
                    "event": "dag_started",
                    "node_count": validation["node_count"],
                    "execution_waves": validation["execution_waves"],
                }
            )

        for wave_index, wave in enumerate(validation["execution_waves"], start=1):
            for step_id in wave:
                step = steps_by_id[step_id]
                resolved_inputs = list(step.get("input_datasets") or [])

                # Automatically inherit artifacts from upstream dependencies
                for dep_id in step["depends_on"]:
                    for artifact_ref in step_artifacts.get(dep_id, []):
                        if artifact_ref not in resolved_inputs:
                            resolved_inputs.append(artifact_ref)

                # Resolve explicit `$step_id.artifacts[0]` placeholders
                final_inputs: list[str] = []
                for item in resolved_inputs:
                    if isinstance(item, str) and item.startswith("$"):
                        match = re.match(r"^\$([A-Za-z0-9_\-]+)(?:\.artifacts\[(\d+)\])?$", item.strip())
                        if match:
                            ref_step = match.group(1)
                            ref_idx = int(match.group(2) or 0)
                            candidates = step_artifacts.get(ref_step, [])
                            if ref_idx < len(candidates):
                                final_inputs.append(candidates[ref_idx])
                            continue
                    final_inputs.append(item)

                # Self-healing CRS inspection & automatic metric projection
                healed_inputs: list[str] = []
                for dataset_item in final_inputs:
                    if (
                        isinstance(dataset_item, str)
                        and Path(dataset_item).exists()
                        and self._should_auto_heal_crs(step, dataset_item)
                    ):
                        heal_info = auto_reproject_vector_dataset(
                            dataset_item,
                            output_dir=self.work_dir,
                        )
                        healed_inputs.append(heal_info["reprojected_path"])
                        action_record = {
                            "step_id": step_id,
                            "agent_id": step["agent_id"],
                            "action": "auto_crs_reprojection",
                            "reason": "Downstream instructions requested metric distance/area on geographic CRS input.",
                            **heal_info,
                        }
                        self_healing_actions.append(action_record)
                        if progress_callback:
                            progress_callback({"event": "self_healing_applied", **action_record})
                    else:
                        healed_inputs.append(dataset_item)

                step["input_datasets"] = healed_inputs
                if progress_callback:
                    progress_callback(
                        {
                            "event": "step_started",
                            "wave": wave_index,
                            "step_id": step_id,
                            "agent_id": step["agent_id"],
                            "input_count": len(healed_inputs),
                        }
                    )

                result_payload = runner(step)
                step_results[step_id] = result_payload
                extracted_paths = self._extract_step_artifact_paths(step_id, result_payload)
                step_artifacts[step_id] = extracted_paths

                if progress_callback:
                    progress_callback(
                        {
                            "event": "step_completed",
                            "wave": wave_index,
                            "step_id": step_id,
                            "agent_id": step["agent_id"],
                            "artifacts": extracted_paths,
                        }
                    )

        terminal_step_id = validation["topological_order"][-1]
        duration = round(time.time() - start_time, 4)

        summary_payload = {
            "status": "successful",
            "dag_validation": validation,
            "duration_seconds": duration,
            "step_results": step_results,
            "step_artifacts": step_artifacts,
            "final_artifacts": step_artifacts.get(terminal_step_id, []),
            "diagnostics": {
                "self_healing_enabled": self.auto_heal_crs,
                "self_healing_count": len(self_healing_actions),
                "self_healing_actions": self_healing_actions,
            },
        }
        if progress_callback:
            progress_callback({"event": "dag_completed", "status": "successful", "duration_seconds": duration})
        return summary_payload
