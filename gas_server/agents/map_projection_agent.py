import os
import sys
import json
import time
import re
import geopandas as gpd
from pyproj import CRS
import logging
from gas_server.core.file_naming import build_output_filename
from gas_server.core.geo_agent import GeoAgent
from gas_server.core.llm_client import build_llm_client, format_service_name
from gas_server.core.config import DATA_DIR, ensure_runtime_dirs

logging.getLogger().setLevel(logging.INFO)

ensure_runtime_dirs()

class MapProjectionAgent(GeoAgent):
    agent_id = "map_projection_agent"
    agent_name = "Map Projection Agent"
    agent_version = "2.0.0"
    agent_description = "Searches for a suitable CRS and reprojects vector datasets."
    requires_input_datasets = True
    requires_model_credentials = False

    def __init__(self, api_key: str | None = None, model: str | None = None):
        # The projection agent is intentionally deterministic. It does not need
        # a model API key or a third-party CRS lookup service for normal use.
        super().__init__(api_key=api_key, model=model, output_dir=DATA_DIR / self.agent_id)

        # State tracking
        self.input_gdfs: list[gpd.GeoDataFrame] = []
        self.transformed_gdfs: list[gpd.GeoDataFrame] = []
        self.selected_crs: str | None = None
        self.report: str = ""
        self.output_dataset_path: str | None = None
        self.output_dataset_paths: list[str] = []
        self.service_name = format_service_name("MapProjectionAgent")
        self.client = build_llm_client(
            service_name=self.service_name,
            openai_api_key=self.api_key,
        )
        
        # Detailed provenance tracking (lightweight, no performance impact)
        self.llm_calls_log = []
        self.tool_calls_log = []
        self.lineage_steps = []   # high-level steps for provenance
        
    def _extract_explicit_crs(self, query: str) -> str | None:
        text = query or ""
        authority_match = re.search(r"\b(EPSG|ESRI|IGNF|OGC)[:\s-]*(\d{3,6})\b", text, flags=re.IGNORECASE)
        if authority_match:
            return f"{authority_match.group(1).upper()}:{authority_match.group(2)}"

        match = re.search(r"\b(?:EPSG[:\s-]*)?(\d{4,6})\b", text, flags=re.IGNORECASE)
        if not match:
            return None
        code = match.group(1)
        if re.search(rf"\b(EPSG|CRS|projection|project|reproject|coordinate|transform)[^\n]{{0,80}}{code}\b", text, flags=re.IGNORECASE):
            return f"EPSG:{code}"
        if re.search(rf"\b{code}\b[^\n]{{0,80}}\b(EPSG|CRS|projection|project|reproject|coordinate|transform)\b", text, flags=re.IGNORECASE):
            return f"EPSG:{code}"
        return None

    def _combined_wgs84_bounds(self, gdfs: list[gpd.GeoDataFrame]) -> tuple[float, float, float, float] | None:
        bounds = []
        for gdf in gdfs:
            if gdf.empty:
                continue
            try:
                working = gdf
                if working.crs is None:
                    working = working.set_crs(epsg=4326, allow_override=True)
                if CRS.from_user_input(working.crs).to_epsg() != 4326:
                    working = working.to_crs(epsg=4326)
                minx, miny, maxx, maxy = working.total_bounds
                bounds.append((float(minx), float(miny), float(maxx), float(maxy)))
            except Exception:
                continue
        if not bounds:
            return None
        return (
            min(item[0] for item in bounds),
            min(item[1] for item in bounds),
            max(item[2] for item in bounds),
            max(item[3] for item in bounds),
        )

    def _estimate_utm_crs(self, gdfs: list[gpd.GeoDataFrame]) -> CRS | None:
        for gdf in gdfs:
            if gdf.empty:
                continue
            try:
                working = gdf
                if working.crs is None:
                    working = working.set_crs(epsg=4326, allow_override=True)
                return working.estimate_utm_crs()
            except Exception:
                continue
        return None

    def _local_crs_candidates(self, gdfs: list[gpd.GeoDataFrame]) -> list[dict]:
        candidates = [
            {
                "crs_code": "EPSG:4326",
                "name": "WGS 84",
                "best_for": "Interchange, longitude/latitude storage, GPS-style coordinates.",
            },
            {
                "crs_code": "EPSG:3857",
                "name": "WGS 84 / Pseudo-Mercator",
                "best_for": "Web map display and compatibility with common web tile basemaps.",
            },
            {
                "crs_code": "EPSG:5070",
                "name": "NAD83 / Conus Albers",
                "best_for": "Equal-area analysis and thematic mapping for the contiguous United States.",
            },
            {
                "crs_code": "ESRI:102004",
                "name": "USA Contiguous Lambert Conformal Conic",
                "best_for": "Conformal regional or national maps of the contiguous United States.",
            },
        ]
        estimated_utm = self._estimate_utm_crs(gdfs)
        if estimated_utm:
            candidates.append(
                {
                    "crs_code": estimated_utm.to_string(),
                    "name": estimated_utm.name,
                    "best_for": "Local distance, buffer, nearest-neighbor, and area workflows near the dataset extent.",
                }
            )
        for gdf in gdfs:
            if gdf.crs:
                try:
                    crs = CRS.from_user_input(gdf.crs)
                    candidates.append(
                        {
                            "crs_code": crs.to_string(),
                            "name": crs.name,
                            "best_for": "Preserving the input dataset's current CRS.",
                        }
                    )
                except Exception:
                    pass
        unique = {}
        for candidate in candidates:
            unique.setdefault(candidate["crs_code"], candidate)
        return list(unique.values())

    def _parse_llm_json(self, content: str) -> dict | None:
        if not content:
            return None
        text = content.strip()
        fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
        if fenced:
            text = fenced.group(1).strip()
        try:
            return json.loads(text)
        except Exception:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if not match:
                return None
            try:
                return json.loads(match.group(0))
            except Exception:
                return None

    def _llm_select_target_crs(self, query: str, gdfs: list[gpd.GeoDataFrame], candidates: list[dict]) -> tuple[str, str] | None:
        client = getattr(self, "client", None)
        if client is None:
            return None

        bounds = self._combined_wgs84_bounds(gdfs)
        prompt = {
            "request": query,
            "dataset_wgs84_bbox": bounds,
            "allowed_candidates": candidates,
            "instructions": (
                "Choose exactly one CRS from allowed_candidates for the user's geospatial reprojection task. "
                "Return only JSON with keys crs_code and justification. Do not invent a CRS outside allowed_candidates."
            ),
        }
        response = client.chat.completions.create(
            model=getattr(self, "model", None) or "gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": "You are a GIS projection advisor. Select a CRS only from the provided local candidate list.",
                },
                {"role": "user", "content": json.dumps(prompt)},
            ],
            temperature=0,
        )
        usage = getattr(response, "usage", None)
        content = response.choices[0].message.content if response and response.choices else ""
        payload = self._parse_llm_json(content)
        self.llm_calls_log.append(
            {
                "model": getattr(self, "model", None) or "gpt-4o",
                "finish_reason": getattr(response.choices[0], "finish_reason", None) if response and response.choices else None,
                "input_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
                "output_tokens": getattr(usage, "completion_tokens", None) if usage else None,
            }
        )
        if not payload:
            return None

        allowed_codes = {candidate["crs_code"] for candidate in candidates}
        crs_code = payload.get("crs_code")
        if crs_code not in allowed_codes:
            return None
        justification = payload.get("justification") or "The model selected this CRS from the local candidate list."
        return crs_code, justification

    def _select_target_crs(self, query: str, gdfs: list[gpd.GeoDataFrame]) -> tuple[str, str]:
        text = (query or "").lower()
        explicit_crs = self._extract_explicit_crs(query)
        if explicit_crs:
            target = CRS.from_user_input(explicit_crs)
            return target.to_string(), f"The request explicitly specified {explicit_crs}."

        common_name_map = [
            (("web mercator", "pseudo mercator", "slippy map", "tile map"), "EPSG:3857", "The request asked for Web Mercator, which is the standard CRS for web map tiles."),
            (("wgs84", "wgs 84", "longitude latitude", "lat lon", "latitude longitude"), "EPSG:4326", "The request asked for WGS 84 / geographic longitude-latitude coordinates."),
            (("conus albers", "usa contiguous albers", "contiguous albers"), "EPSG:5070", "The request asked for a contiguous United States Albers equal-area CRS."),
            (("lambert conformal conic", "lcc"), "ESRI:102004", "The request asked for a Lambert Conformal Conic projection suitable for the contiguous United States."),
        ]
        for keywords, crs_code, reason in common_name_map:
            if any(keyword in text for keyword in keywords):
                target = CRS.from_user_input(crs_code)
                return target.to_string(), reason

        bounds = self._combined_wgs84_bounds(gdfs)
        is_us_context = any(
            term in text
            for term in (
                "united states",
                "usa",
                "u.s.",
                "us ",
                "conus",
                "contiguous",
                "continental",
                "pennsylvania",
                " pa ",
            )
        )
        wants_equal_area = any(term in text for term in ("equal area", "area", "density", "choropleth", "population"))
        wants_distance = any(term in text for term in ("distance", "buffer", "nearest", "length", "miles", "kilometers", "metres", "meters"))

        if bounds:
            minx, miny, maxx, maxy = bounds
            width = maxx - minx
            covers_us_scale = width > 12 or (minx < -125 and maxx > -67)
            if wants_equal_area and (is_us_context or covers_us_scale):
                return "EPSG:5070", "The task involves area/density-style analysis over a United States extent, so NAD83 / Conus Albers is a suitable equal-area CRS."
            if is_us_context and covers_us_scale:
                return "EPSG:5070", "The data cover a broad United States extent, so NAD83 / Conus Albers provides a stable projected CRS for national or multi-state analysis."

        if "utm" in text or wants_distance:
            estimated = self._estimate_utm_crs(gdfs)
            if estimated:
                return estimated.to_string(), "The task benefits from local distance-preserving coordinates, so I estimated the appropriate UTM CRS from the dataset extent."

        if "pennsylvania" in text or re.search(r"\bpa\b", text):
            return "EPSG:5070", "The request is Pennsylvania-focused and no specific CRS was provided, so NAD83 / Conus Albers is used as a robust regional projected CRS."

        ambiguous_choice = any(term in text for term in ("best", "suitable", "appropriate", "recommend", "choose", "optimal"))
        if ambiguous_choice:
            candidates = self._local_crs_candidates(gdfs)
            llm_choice = self._llm_select_target_crs(query, gdfs, candidates)
            if llm_choice:
                return llm_choice

        estimated = self._estimate_utm_crs(gdfs)
        if estimated:
            return estimated.to_string(), "No explicit target CRS was provided, so I estimated a local UTM CRS from the dataset extent."

        return "EPSG:4326", "No explicit target CRS or usable local projected CRS could be inferred, so WGS 84 is used as a conservative fallback."

    def _preferred_vector_output(self, query: str) -> tuple[str, str, str]:
        request = (query or "").lower()
        if "geojson" in request and not any(term in request for term in ("geopackage", "gpkg", ".gpkg")):
            return ".geojson", "GeoJSON", "GeoJSON"
        return ".gpkg", "GPKG", "GeoPackage"

    def _artifact_output_dir(self) -> str:
        out_dir = getattr(self, "output_dir", str(DATA_DIR / self.agent_id))
        os.makedirs(out_dir, exist_ok=True)
        return out_dir

    def _load_dataset(self, dataset_path: str) -> gpd.GeoDataFrame:
        """Loads a spatial dataset from a file path."""
        is_url = dataset_path.lower().startswith(("http://", "https://"))
        if not is_url and not os.path.exists(dataset_path):
            raise FileNotFoundError(f"Dataset not found at path: {dataset_path}")
        if dataset_path.lower().endswith(('.parquet', '.pq')):
            gdf = gpd.read_parquet(dataset_path)
        else:
            gdf = gpd.read_file(dataset_path)
        return gdf

    def apply_transformation(self, crs_code: str, justification: str) -> str:
        """Uses pyproj and geopandas to transform the datasets."""
        logging.info("MapProjectionAgent applying transformation to %s", crs_code)
        try:
            # Use pyproj to define the target coordinate reference system
            target_crs = CRS.from_string(crs_code)
            temp_gdfs = []
            
            for i, gdf in enumerate(self.input_gdfs):
                # Failsafe: if the input data is missing a CRS entirely, assume WGS84 so pyproj doesn't crash
                if gdf.crs is None:
                    bounds = gdf.total_bounds
                    # Check if bounds are far outside the standard WGS84 range
                    if bounds[0] < -180.1 or bounds[1] < -90.1 or bounds[2] > 180.1 or bounds[3] > 90.1:
                        raise ValueError(f"Dataset {i} lacks a CRS and bounds {bounds} are outside WGS84 range. Cannot safely assume EPSG:4326.")
                    
                    logging.info("Dataset %s CRS missing. Assuming EPSG:4326 fallback.", i)
                    gdf = gdf.set_crs(epsg=4326, allow_override=True)
                
                # Perform the transformation
                logging.info("Transforming from %s to %s", gdf.crs, crs_code)
                transformed_gdf = gdf.to_crs(target_crs)
                temp_gdfs.append(transformed_gdf)
                
            # Atomic assignment: Only overwrite state if transformation is successful
            self.transformed_gdfs = temp_gdfs
            
            # Save the agent's logic state
            self.selected_crs = crs_code
            self.report = justification
            return json.dumps({
                "status": "success",
                "message": f"Successfully transformed {len(self.input_gdfs)} dataset(s) to {crs_code}."
            })
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    # --- Main Agent Loop ---
    def run(self, query: str, input_dataset_paths: list[str] | str | None = None, progress_callback=None, max_iterations: int = 5) -> dict:
        text = query
        dataset_paths = self.normalize_dataset_paths(input_dataset_paths)
        logging.info("MapProjectionAgent evaluating optimal CRS for: %s", text)
        start_time = time.time()
        tool_calls = 0
        final_summary = "Projection task did not complete."

        # Reset per-run state.
        self.llm_calls_log = []
        self.tool_calls_log = []
        self.lineage_steps = []
        self.output_dataset_paths = []
        self.input_gdfs = []
        self.transformed_gdfs = []
        self.selected_crs = None
        self.report = ""
        self.output_dataset_path = None

        self._emit_progress(
            progress_callback,
            stage="start",
            message=f"I will inspect {len(dataset_paths)} dataset reference(s), identify their current CRS, and choose an appropriate target projection for the request.",
            data={"dataset_count": len(dataset_paths), "selection_method": "local_pyproj_geopandas"},
        )

        agent_data = {
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "model": None,
            "duration": None,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "inputs": {
                "text": text,
                "dataset_path": dataset_paths,
            },
            "outputs": {
                "text": None,
                "dataset_path": None,
                "dataset_paths": [],
                "dataset_size": {
                    "type": None,
                    "dimensions": None,
                    "feature_count": None,
                },
            },
            "metrics": {
                "llm_calls": 0,
                "tool_calls": 0,
                "number_of_artifacts": 0,
            },
            "environment": {
                "python_version": sys.version.split(" ")[0],
                "domain-specific libraries": [
                    "geopandas",
                    "pyproj",
                ],
            },
            "script": None,
            "complementary": {
                "Execution": {"Inputs": {}, "Outputs": {}},
                "Provenance": {"Lineage": {}, "Tool Calls": {}, "LLM Calls": {}},
                "Artifacts and Logs": {"Inline Artifacts": {}, "Persisted Artifacts": {}},
            },
        }

        try:
            if not dataset_paths:
                raise ValueError("No dataset paths were provided.")

            for dataset_path in dataset_paths:
                self._emit_progress(
                    progress_callback,
                    stage="input_inspection",
                    message="I am loading an input dataset so I can inspect its current coordinate reference system.",
                    data={"dataset_path": dataset_path},
                )
                gdf = self._load_dataset(dataset_path)
                if gdf.empty:
                    raise ValueError(f"Dataset is empty: {dataset_path}")
                self.input_gdfs.append(gdf)
                logging.info("Loaded dataset CRS %s from %s", gdf.crs, dataset_path)
                self.lineage_steps.append(f"Loaded dataset from {dataset_path}, CRS: {gdf.crs}")

            self._emit_progress(
                progress_callback,
                stage="method_selection",
                message="I am selecting a target CRS from the request text and dataset extent using local pyproj/geopandas logic.",
                data={
                    "uses_external_crs_api": False,
                    "llm_fallback_available": getattr(self, "client", None) is not None,
                },
            )
            target_crs, justification = self._select_target_crs(text, self.input_gdfs)
            self._emit_progress(
                progress_callback,
                stage="analysis_execution",
                message=f"I selected {target_crs} and will transform the dataset into that coordinate system.",
                data={"target_crs": target_crs, "justification": justification},
            )

            tool_calls += 1
            result_str = self.apply_transformation(target_crs, justification)
            result_data = json.loads(result_str)
            result_status = result_data.get("status", "unknown")
            self.tool_calls_log.append({
                "function": "apply_transformation",
                "arguments": {"crs_code": target_crs, "justification": justification},
                "result_status": result_status,
                "timestamp": time.time(),
            })
            if result_status != "success":
                raise RuntimeError(result_data.get("message", "CRS transformation failed."))

            final_summary = result_data.get("message", f"Transformed dataset(s) to {target_crs}.")
            self.lineage_steps.append(f"Selected target CRS {target_crs}: {justification}")
            self.lineage_steps.append(f"Applied target CRS {target_crs}")

            out_dir = self._artifact_output_dir()
            ext, driver, output_label = self._preferred_vector_output(text)
            self._emit_progress(
                progress_callback,
                stage="artifact_generation",
                message=f"The projection transformation is complete, so I will save the transformed dataset artifact as {output_label}.",
                data={"format": output_label, "output_count": len(self.transformed_gdfs)},
            )

            for i, transformed_gdf in enumerate(self.transformed_gdfs):
                output_path = os.path.join(
                    out_dir,
                    build_output_filename(
                        f"{text} dataset {i + 1}",
                        extension=ext,
                        fallback="crs_transformed",
                    ),
                )
                transformed_gdf.to_file(output_path, driver=driver)
                self.output_dataset_paths.append(output_path)
                self.lineage_steps.append(f"Saved transformed dataset {i + 1} to {output_path}")

            self.output_dataset_path = self.output_dataset_paths[0] if self.output_dataset_paths else None
            first_output = self.transformed_gdfs[0]
            agent_data["outputs"]["text"] = f"{final_summary}\n\nTechnical Report:\n{self.report}"
            agent_data["outputs"]["dataset_path"] = self.output_dataset_path
            agent_data["outputs"]["dataset_paths"] = self.output_dataset_paths
            agent_data["outputs"]["dataset_size"]["type"] = type(first_output).__name__
            agent_data["outputs"]["dataset_size"]["feature_count"] = len(first_output)
            agent_data["metrics"]["number_of_artifacts"] = len(self.output_dataset_paths)

            self._emit_progress(
                progress_callback,
                stage="complete",
                message="The projection workflow is complete. I am packaging the transformed dataset, CRS selection, and technical report.",
                data={"output_count": len(self.output_dataset_paths), "selected_crs": self.selected_crs},
            )

        except Exception as e:
            logging.exception("MapProjectionAgent failed")
            self._emit_progress(
                progress_callback,
                stage="error",
                message=f"The projection workflow hit an error, so I will return the failure details: {e}",
            )
            agent_data["outputs"]["text"] = f"Status: failed. Error: {str(e)}"
            self.lineage_steps.append(f"ERROR: {str(e)}")

        input_tokens = 0
        output_tokens = 0
        for call in self.llm_calls_log:
            if isinstance(call.get("input_tokens"), int):
                input_tokens += call["input_tokens"]
            if isinstance(call.get("output_tokens"), int):
                output_tokens += call["output_tokens"]

        agent_data["duration"] = f"{time.time() - start_time:.2f}s"
        agent_data["total_input_tokens"] = input_tokens
        agent_data["total_output_tokens"] = output_tokens
        agent_data["metrics"]["llm_calls"] = len(self.llm_calls_log)
        agent_data["metrics"]["tool_calls"] = tool_calls
        agent_data["complementary"]["Execution"]["Inputs"] = {
            "text_query": text,
            "input_dataset_path": dataset_paths,
            "input_crs": str(self.input_gdfs[0].crs) if self.input_gdfs else None,
            "feature_count": len(self.input_gdfs[0]) if self.input_gdfs else None,
        }
        agent_data["complementary"]["Execution"]["Outputs"] = {
            "final_summary": final_summary,
            "selected_crs": self.selected_crs,
            "technical_report": self.report,
            "output_dataset_path": self.output_dataset_path,
            "output_dataset_paths": self.output_dataset_paths,
        }
        agent_data["complementary"]["Provenance"]["Lineage"] = {
            "steps": self.lineage_steps,
            "count": len(self.lineage_steps),
        }
        agent_data["complementary"]["Provenance"]["Tool Calls"] = {
            "total": len(self.tool_calls_log),
            "details": self.tool_calls_log,
        }
        agent_data["complementary"]["Provenance"]["LLM Calls"] = {
            "total": len(self.llm_calls_log),
            "details": self.llm_calls_log,
        }
        if self.transformed_gdfs:
            gdf = self.transformed_gdfs[0]
            agent_data["complementary"]["Artifacts and Logs"]["Inline Artifacts"] = {
                "transformed_crs": str(gdf.crs),
                "geometry_types": list(gdf.geometry.type.unique()),
                "feature_count": len(gdf),
                "bounds": gdf.total_bounds.tolist(),
            }
        agent_data["complementary"]["Artifacts and Logs"]["Persisted Artifacts"] = {
            "output_files": self.output_dataset_paths,
        }
        return agent_data
