import os
import io
import sys
import re
import json
import time
import math
import random
import traceback
import warnings
import platform
from typing import Any, Dict, List, Optional, Tuple, Union
import pandas as pd
import geopandas as gpd
import numpy as np

import logging
logging.getLogger().setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("openai").setLevel(logging.ERROR)

from gas_server.core.file_naming import build_output_filename
from gas_server.core.geo_agent import GeoAgent
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

from gas_server.core.llm_client import build_llm_client, format_service_name
from dotenv import load_dotenv
load_dotenv()
from gas_server.core.config import DATA_DIR, PROJECT_ROOT, ensure_runtime_dirs

ensure_runtime_dirs()

BASE_DIR = str(PROJECT_ROOT)


class VectorAnalysisAgent(GeoAgent):
    """
    An adaptive, code-centric spatial analysis agent.
    It uses Python execution as its primary tool for loading, inspection, and analysis.
    Now with persistent registry visibility and explicit reuse guidance.
    """

    agent_id = "vector_analysis_agent"
    agent_name = "Vector Analysis Agent"
    agent_version = "2.1.0"
    agent_description = "Runs vector GIS analysis and transformation workflows."
    requires_input_datasets = True

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str | None = None,
        graph_path: str = os.path.join(BASE_DIR, "gas_server", "agents", "graph.json"),
        documents_path: str = os.path.join(BASE_DIR, "gas_server", "agents", "documents.json"),
        triples_path: str = os.path.join(BASE_DIR, "gas_server", "agents", "triples.txt"),
        debug: bool = True,
    ):
        if OpenAI is None:
            raise ImportError("Please install the 'openai' package.")

        super().__init__(api_key=api_key, model=model or "gpt-5.4", output_dir=DATA_DIR / self.agent_id)
        self.service_name = format_service_name(self.agent_name)
        self.client = build_llm_client(
            service_name=self.service_name,
            openai_api_key=self.api_key,
        )
        self.debug = debug

        # --- Internal State / Runtime Memory ---
        self.runtime_memory = {
            "datasets": {},        # Metadata about loaded sets
            "facts": [],           # Discovered truths during runtime
            "errors": [],          # Historical errors and their resolutions
            "plan_status": "init",
            "assumptions": []
        }

        # --- Artifact Storage (persistent across tool calls) ---
        self.registry: Dict[str, Any] = {}  # Actual DataFrames held in memory
        self.final_artifact_key: Optional[str] = None
        self.final_artifact_keys: List[str] = []

        # --- Metrics ---
        self.code_executions = 0
        self.kb_searches = 0

        # Knowledge base paths
        self.graph_path = graph_path
        self.documents_path = documents_path
        self.triples_path = triples_path
        self.kb_loaded = False
        self._knowledge_index = []

        self._setup_system_prompt()
        self._define_tools()

    def _setup_system_prompt(self):
        self.system_prompt = {
            "role": "system",
            "content": (
                "You are a Senior Geospatial Systems Architect and Data Analyst.\n"
                "Your objective is to solve spatial tasks by writing and executing Python code.\n\n"

                "IMPORTANT – PERSISTENT STATE & REUSE:\n"
                "- The environment has a `registry` dictionary that persists across all `execute_script` calls.\n"
                "- Any GeoDataFrame or DataFrame you assign to a variable and also store in `registry` (e.g., `registry['my_data'] = my_data`) will be available in future calls.\n"
                "- **Do NOT re‑import libraries** – `geopandas as gpd`, `pandas as pd`, `numpy as np` are already imported and available.\n"
                "- **Do NOT re‑read files** if the data is already in `registry`. First check `registry.keys()` (you can write a small script to inspect it).\n"
                "- Use `registry['varname']` to retrieve previously loaded data.\n"
                "- The helper function `list_registry()` returns a readable summary of all cached objects.\n\n"
                "- ALWAYS keep the geometry column in spatial outputs.\n"
                "- Save vector outputs as GeoPackage by default unless the user explicitly asks for GeoJSON.\n"
                "- If you are going to do a join, first try a non-spatial attribute join using stable identifiers such as GEOID/FIPS. If no reliable attribute key exists, then try a spatial join.\n"
                "- If your task is finding radom points within a polygon, write the python code to generate random points within the polygon.\n"
                "- If your task is calculating distance from a set of objects to another set of objects, make sure your resutls are in geojson for each ojbect.\n"
                " - If your task is calculating distance in meter or kilometer, make sure to project your data to a projected CRS before calculating distance and then reproject back to original CRS if needed.\n\n"

                "ADAPTIVE BEHAVIOR:\n"
                "1. DYNAMIC LOADING: Write code to try different loaders (gpd.read_file, pd.read_csv, etc.) based on file extensions.\n"
                "2. ADAPTIVE INSPECTION: Use code to check CRS, column types, null counts, and geometry validity. "
                "When inspecting sample values, truncate any string over 40 characters with '...'.\n"
                "3. RUNTIME MEMORY: Maintain a mental log of what you've learned. If an error occurs, analyze the traceback, "
                "run diagnostic code if needed, and adapt your plan.\n"
                "4. CRS HANDLING: Reason explicitly about CRS. If you are overlaying or joining two spatial sets, "
                "check their CRS through code and reproject if necessary.\n"
                "5. KB SEARCH: Only use `search_knowledge_base` if you are genuinely uncertain about a specific GeoPandas function "
                "or geospatial concept. Prefer solving from your own knowledge first.\n\n"

                "FINALIZING:\n"
                "- When the task is complete, you MUST call `register_final_artifact` with the variable name of the result, or provide `variable_names` if you need to save several final artifacts (all must exist in `registry`).\n"
                "- The result can be a GeoDataFrame (for spatial), DataFrame (for tabular), or a list/tuple containing several final artifacts.\n\n"

                "EFFICIENCY:\n"
                "- Before loading a file, write a short script that prints `registry.keys()` to see if it's already loaded.\n"
                "- Always reuse cached objects. Avoid duplicate imports and file reads.\n"
            )
        }

    def _define_tools(self):
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": "execute_script",
                    "description": "Execute a Python script. Use this for loading files, inspecting data, and performing analysis. "
                                   "The environment already has `gpd`, `pd`, `np` imported, and a persistent `registry` dict. "
                                   "After execution, you will receive STDOUT/ERROR plus a summary of all objects currently in `registry`.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "script": {"type": "string", "description": "The full Python code to run."},
                            "purpose": {"type": "string", "description": "Short explanation of why you are running this code (e.g., 'loading counties file')."}
                        },
                        "required": ["script"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_knowledge_base",
                    "description": "Search the GeoPandas handbook for help with specific spatial methods or errors.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"}
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "register_final_artifact",
                    "description": "Mark one or more variables from the registry as the final result(s) for storage.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "variable_name": {"type": "string", "description": "The name of the variable (GeoDataFrame or DataFrame) to save. It must be a key in `registry`."},
                            "variable_names": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Optional list of registry variables to save as several output artifacts."
                            },
                            "summary": {"type": "string", "description": "A brief description of what this final result represents."}
                        },
                        "required": [],
                    },
                },
            }
        ]

    # --------------------------
    # Sandbox & Execution
    # --------------------------

    def _get_prelude(self) -> str:
        """Utility helpers injected into every execution, plus standard imports."""
        return """
import pandas as pd
import geopandas as gpd
import numpy as np
import json
import warnings
from shapely.geometry import shape, mapping

warnings.filterwarnings('ignore')

# Helper to inspect a DataFrame/GeoDataFrame
def inspect_df(name, df, sample_count=1):
    info = {
        "name": name,
        "type": str(type(df)),
        "rows": len(df),
        "columns": list(df.columns),
        "dtypes": {k: str(v) for k, v in df.dtypes.items()}
    }
    if isinstance(df, gpd.GeoDataFrame):
        info["crs"] = str(df.crs)
        info["geometry_type"] = df.geometry.geom_type.unique().tolist() if not df.empty else []
    
    samples = {}
    if not df.empty:
        s_df = df.head(sample_count)
        for col in df.columns:
            val = s_df[col].iloc[0]
            val_str = str(val)
            if len(val_str) > 40:
                val_str = val_str[:40] + "..."
            samples[col] = val_str
    info["samples"] = samples
    # print(f"--- INSPECTION: {name} ---")
    # print(json.dumps(info, indent=2))
    # print("--------------------------")

# Helper to list current registry contents
def list_registry():
    # print("--- CURRENT REGISTRY ---")
    for key, obj in registry.items():
        obj_type = "GeoDataFrame" if isinstance(obj, gpd.GeoDataFrame) else "DataFrame" if isinstance(obj, pd.DataFrame) else type(obj).__name__
        rows = len(obj) if hasattr(obj, '__len__') else 'N/A'
    #     print(f"  {key}: {obj_type}, rows={rows}")
    # print("------------------------")

"""

    def _execute_in_sandbox(self, script: str) -> Dict[str, Any]:
        self.code_executions += 1
        full_code = self._get_prelude() + "\n" + script

        stdout_capture = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = stdout_capture

        # Local context for exec
        exec_locals = {"registry": self.registry}

        error = None
        try:
            exec(full_code, {}, exec_locals)
            # Update registry with any new DataFrames/GeoDataFrames created in the script
            for k, v in exec_locals.items():
                if isinstance(v, (pd.DataFrame, gpd.GeoDataFrame)):
                    self.registry[k] = v
        except Exception:
            error = traceback.format_exc()
        finally:
            sys.stdout = old_stdout

        # Build registry summary to return as part of the tool response
        registry_summary = []
        for key, obj in self.registry.items():
            if isinstance(obj, (pd.DataFrame, gpd.GeoDataFrame)):
                typ = "GeoDataFrame" if isinstance(obj, gpd.GeoDataFrame) else "DataFrame"
                rows = len(obj)
                registry_summary.append(f"  {key}: {typ}, {rows} rows")
            else:
                registry_summary.append(f"  {key}: {type(obj).__name__}")
        registry_text = "\n".join(registry_summary) if registry_summary else "  (empty)"

        output = stdout_capture.getvalue()
        if error:
            result = f"ERROR:\n{error}\n\n--- REGISTRY AFTER EXECUTION ---\n{registry_text}\n--- END REGISTRY ---"
        else:
            result = f"STDOUT:\n{output}\n\n--- REGISTRY AFTER EXECUTION ---\n{registry_text}\n--- END REGISTRY ---"

        return {
            "stdout": output,
            "error": error,
            "full_response": result,   # This will be sent back to the LLM
            "registry_summary": registry_text
        }

    # --------------------------
    # Knowledge Base (Lazy Load)
    # --------------------------

    def _load_kb(self):
        if self.kb_loaded:
            return
        try:
            if os.path.exists(self.graph_path):
                with open(self.graph_path, 'r') as f:
                    data = json.load(f)
                    nodes = data.get("nodes", []) or data.get("value", {}).get("nodes", [])
                    for n in nodes:
                        self._knowledge_index.append({"text": f"{n.get('id')} {n.get('description')}", "meta": n})
            self.kb_loaded = True
        except Exception as e:
            if self.debug:
                logging.warning("KB load failed: %s", e)

    def _search_kb(self, query: str) -> str:
        self.kb_searches += 1
        self._load_kb()
        if not self._knowledge_index:
            return "Knowledge base is empty or could not be loaded."

        q_words = set(re.findall(r'\w+', query.lower()))
        scored = []
        for item in self._knowledge_index:
            score = len(q_words.intersection(set(re.findall(r'\w+', item['text'].lower()))))
            if score > 0:
                scored.append((score, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [s[1]['text'] for s in scored[:5]]
        return "\n".join(results) if results else "No relevant entries found."

    # --------------------------
    # Persistence & Utilities
    # --------------------------

    def _environment_info(self) -> Dict[str, Any]:
        return {
            "python_version": platform.python_version(),
            "domain-specific libraries": ["geopandas", "pandas", "numpy", "shapely"]
        }

    def _generate_filename(self, task: str) -> str:
        return build_output_filename(
            task,
            extension="",
            fallback="analysis_result",
        )

    def _output_dir(self) -> str:
        out_dir = getattr(self, "output_dir", str(DATA_DIR / self.agent_id))
        os.makedirs(out_dir, exist_ok=True)
        return out_dir

    def _read_dataset(self, path: str) -> Union[pd.DataFrame, gpd.GeoDataFrame]:
        ext = os.path.splitext(str(path).split("?", 1)[0])[1].lower()
        if ext in {".csv", ".tsv"}:
            sep = "\t" if ext == ".tsv" else ","
            return pd.read_csv(path, sep=sep)
        try:
            data = gpd.read_file(path)
            return self._repair_degree_like_projected_crs(data)
        except Exception:
            return pd.read_csv(path)

    def _repair_degree_like_projected_crs(self, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        if not isinstance(gdf, gpd.GeoDataFrame) or gdf.empty or "geometry" not in gdf:
            return gdf
        try:
            minx, miny, maxx, maxy = [float(value) for value in gdf.total_bounds]
        except Exception:
            return gdf

        bounds_look_geographic = -180 <= minx <= 180 and -180 <= maxx <= 180 and -90 <= miny <= 90 and -90 <= maxy <= 90
        if not bounds_look_geographic:
            return gdf

        if gdf.crs is None:
            return gdf.set_crs("EPSG:4326")

        try:
            if gdf.crs.is_projected:
                return gdf.set_crs("EPSG:4326", allow_override=True)
        except Exception:
            pass
        return gdf

    def _load_input_datasets(self, dataset_paths: List[str]) -> List[Dict[str, Any]]:
        datasets = []
        for index, path in enumerate(dataset_paths, start=1):
            data = self._read_dataset(path)
            datasets.append(
                {
                    "index": index,
                    "path": path,
                    "data": data,
                    "is_geo": isinstance(data, gpd.GeoDataFrame),
                    "columns": list(data.columns),
                    "rows": len(data),
                }
            )
        return datasets

    def _normalize_join_series(self, series: pd.Series, width: Optional[int] = None) -> pd.Series:
        values = series.astype("string").str.strip()
        values = values.str.replace(r"\.0$", "", regex=True)
        values = values.str.replace(r"[^0-9A-Za-z]", "", regex=True)
        if width:
            numeric_mask = values.str.fullmatch(r"\d+", na=False)
            values = values.where(~numeric_mask, values.str.zfill(width))
        return values

    def _column_score(self, column: str) -> int:
        name = column.lower().replace("_", "")
        scores = {
            "geoid": 100,
            "countyfips": 95,
            "countyfp": 90,
            "fips": 85,
            "geographyid": 80,
            "locationid": 75,
            "tractce": 60,
            "statefp": 40,
        }
        return max((score for token, score in scores.items() if token in name), default=0)

    def _candidate_join_columns(self, df: pd.DataFrame) -> List[str]:
        geometry_column = df.geometry.name if isinstance(df, gpd.GeoDataFrame) else None
        candidates = [column for column in df.columns if column != geometry_column]
        candidates = [column for column in candidates if self._column_score(str(column)) > 0]
        return sorted(candidates, key=lambda column: self._column_score(str(column)), reverse=True)

    def _best_attribute_join(self, left: pd.DataFrame, right: pd.DataFrame) -> Optional[Dict[str, Any]]:
        left_candidates = self._candidate_join_columns(left)
        right_candidates = self._candidate_join_columns(right)
        best = None

        for left_col in left_candidates:
            for right_col in right_candidates:
                width = 5 if any(token in f"{left_col} {right_col}".lower() for token in ("geoid", "fips", "county")) else None
                left_key = self._normalize_join_series(left[left_col], width=width)
                right_key = self._normalize_join_series(right[right_col], width=width)
                left_non_null = set(left_key.dropna()) - {""}
                right_non_null = set(right_key.dropna()) - {""}
                if not left_non_null or not right_non_null:
                    continue
                overlap = left_non_null.intersection(right_non_null)
                match_ratio = len(overlap) / max(1, min(len(left_non_null), len(right_non_null)))
                score = match_ratio * 100 + self._column_score(str(left_col)) + self._column_score(str(right_col))
                if overlap and (best is None or score > best["score"]):
                    best = {
                        "left_column": left_col,
                        "right_column": right_col,
                        "width": width,
                        "overlap_count": len(overlap),
                        "match_ratio": match_ratio,
                        "score": score,
                    }
        if best and best["match_ratio"] >= 0.2:
            return best
        return None

    def _parse_distance_meters(self, query: str) -> Optional[float]:
        match = re.search(r"(\d+(?:\.\d+)?)\s*[- ]?\s*(miles|mile|mi|kilometers|kilometer|km|meters|meter|m)\b", query.lower())
        if not match:
            return None
        value = float(match.group(1))
        unit = match.group(2)
        if unit in {"mile", "miles", "mi"}:
            return value * 1609.344
        if unit in {"kilometer", "kilometers", "km"}:
            return value * 1000
        return value

    def _save_artifact_direct(self, obj: Union[pd.DataFrame, gpd.GeoDataFrame], task: str) -> Tuple[str, Dict[str, Any], str]:
        out_dir = self._output_dir()
        base_name = self._generate_filename(task)
        if isinstance(obj, gpd.GeoDataFrame):
            ext, driver, label = self._preferred_vector_output(task)
            path = os.path.join(out_dir, f"{base_name}{ext}")
            obj.to_file(path, driver=driver)
            metadata = {"type": "vector", "dimensions": list(obj.shape), "feature_count": len(obj)}
            return path, metadata, label
        path = os.path.join(out_dir, f"{base_name}.csv")
        obj.to_csv(path, index=False)
        metadata = {"type": "table", "dimensions": list(obj.shape), "feature_count": len(obj)}
        return path, metadata, "CSV"

    def _build_direct_response(
        self,
        start_time: float,
        query: str,
        dataset_paths: List[str],
        output_path: str,
        metadata: Dict[str, Any],
        summary: str,
        script: str,
        progress_callback=None,
    ) -> Dict[str, Any]:
        self.number_of_artifacts = 1 if output_path else 0
        self._emit_progress(
            progress_callback,
            stage="artifact_generation",
            message=f"I saved the deterministic vector analysis result with {metadata.get('feature_count')} record(s).",
            data={"output_path": output_path, "metadata": metadata},
        )
        return {
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "model": "deterministic",
            "duration": f"{time.time() - start_time:.2f}s",
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "inputs": {"text": query, "dataset_path": dataset_paths},
            "outputs": {
                "text": summary,
                "dataset_path": output_path,
                "dataset_paths": [output_path] if output_path else [],
                "dataset_size": metadata,
            },
            "metrics": {
                "llm_calls": 0,
                "tool_calls": 0,
                "number_of_artifacts": self.number_of_artifacts,
            },
            "environment": self._environment_info(),
            "script": script,
            "complementary": {
                "Execution": {
                    "Inputs": {"text": query, "dataset_path": dataset_paths},
                    "Outputs": {
                        "dataset_path": output_path,
                        "dataset_paths": [output_path] if output_path else [],
                        "dataset_size": metadata,
                    },
                },
                "Provenance": {
                    "Lineage": {"steps": ["deterministic vector workflow", "result validation", "artifact save"]},
                    "Tool Calls": {"execute_script_count": 0, "search_knowledge_base_count": 0},
                    "LLM Calls": {"total": 0},
                },
                "Artifacts and Logs": {
                    "Inline Artifacts": {"generated_script": script},
                    "Persisted Artifacts": {"paths": [output_path] if output_path else []},
                },
            },
        }

    def _deterministic_result_validation_errors(self, query: str, result: Dict[str, Any]) -> List[str]:
        request = (query or "").lower()
        output_path = (result.get("outputs") or {}).get("dataset_path")
        if not output_path or not os.path.exists(output_path):
            return ["The deterministic workflow did not create a readable output artifact."]

        try:
            output = gpd.read_file(output_path)
        except Exception:
            try:
                output = pd.read_csv(output_path)
            except Exception as exc:
                return [f"The deterministic output artifact could not be read for validation: {exc}"]

        columns = {str(column) for column in getattr(output, "columns", [])}
        normalized_columns = {column.lower(): column for column in columns}
        script_text = str(result.get("script") or "").lower()
        summary_text = str((result.get("outputs") or {}).get("text") or "").lower()
        evidence_text = f"{script_text}\n{summary_text}"
        errors: List[str] = []

        required_columns = self._requested_output_columns_for_validation(query)
        missing_columns = [
            column for column in required_columns
            if column.lower() not in normalized_columns
        ]
        if missing_columns:
            errors.append(f"Missing requested output field(s): {', '.join(sorted(missing_columns))}.")

        operation_requirements = {
            "buffer": ("buffer",),
            "dissolve": ("dissolve", "union"),
            "intersect": ("intersect", "intersection", "overlay"),
        }
        for requested_term, evidence_terms in operation_requirements.items():
            if self._has_any_term(request, (requested_term,)):
                if not any(term in evidence_text for term in evidence_terms):
                    errors.append(f"The deterministic workflow did not show evidence of the requested {requested_term} operation.")

        if self._has_any_term(request, ("coverage",)) and "coverage_pct" not in normalized_columns:
            errors.append("The request asked for coverage analysis but the output lacks coverage_pct.")

        if self._has_any_term(request, ("count", "counts", "number of", "how many")):
            count_like_columns = [column for column in columns if str(column).lower().endswith("_count")]
            if not count_like_columns and "feature_count" not in normalized_columns:
                errors.append("The request asked for counts but the output lacks a count field.")

        if self._has_any_term(request, ("centroid",)):
            if "centroid_lon" not in normalized_columns or "centroid_lat" not in normalized_columns:
                errors.append("The request asked for centroid fields but the output lacks centroid_lon/centroid_lat.")

        if self._has_any_term(request, ("length",)):
            if "length_m" not in normalized_columns and "length_km" not in normalized_columns:
                errors.append("The request asked for length measurements but the output lacks length fields.")

        area_only_request = self._has_any_term(request, ("area",)) and not self._has_any_term(
            request,
            ("coverage", "intersect", "intersection", "buffer", "dissolve"),
        )
        if area_only_request and "area_sq_m" not in normalized_columns and "area_sq_km" not in normalized_columns:
            errors.append("The request asked for area measurements but the output lacks area fields.")

        return errors

    def _requested_output_columns_for_validation(self, query: str) -> set[str]:
        request = query or ""
        columns: set[str] = set()
        reserved_words = {
            "as",
            "to",
            "the",
            "new",
            "field",
            "fields",
            "column",
            "columns",
            "value",
            "rate",
            "result",
        }

        explicit_field_patterns = (
            r"\b(?:new\s+numeric\s+)?field\s+([A-Za-z_][A-Za-z0-9_]*)\b",
            r"\b(?:new\s+numeric\s+)?column\s+([A-Za-z_][A-Za-z0-9_]*)\b",
            r"\b(?:with|include|return)\s+(?:the\s+)?(?:new\s+)?([A-Za-z_][A-Za-z0-9_]*)\s+field\b",
            r"\b(?:with|include|return)\s+(?:the\s+)?(?:new\s+)?([A-Za-z_][A-Za-z0-9_]*)\s+column\b",
        )
        for pattern in explicit_field_patterns:
            for match in re.finditer(pattern, request, flags=re.IGNORECASE):
                candidate = match.group(1)
                if candidate.lower() not in reserved_words:
                    columns.add(candidate)

        for token in re.findall(r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]*\b", request):
            columns.add(token)

        if re.search(r"\bcoverage_pct\b", request, flags=re.IGNORECASE):
            columns.add("coverage_pct")
        if re.search(r"\bcovered_area_m2\b", request, flags=re.IGNORECASE):
            columns.add("covered_area_m2")
        if re.search(r"\bcounty_area_m2\b", request, flags=re.IGNORECASE):
            columns.add("county_area_m2")

        return columns

    def _try_deterministic_workflow(self, query: str, dataset_paths: List[str], start_time: float, progress_callback=None):
        request = (query or "").lower()
        if not dataset_paths:
            return None

        if self._explicitly_requests_model_workflow(request):
            self._emit_progress(
                progress_callback,
                stage="planning",
                message="The request explicitly asks for a model-driven vector workflow, so I will skip the deterministic GeoPandas fast path.",
                data={"dataset_count": len(dataset_paths)},
            )
            return None

        common_terms = (
            "join",
            "merge",
            "buffer",
            "clip",
            "intersect",
            "intersection",
            "spatial join",
            "count",
            "counts",
            "number of",
            "how many",
            "dissolve",
            "aggregate",
            "group by",
            "area",
            "length",
            "centroid",
            "nearest",
            "closest",
            "distance to",
            "filter",
            "select",
            "where",
            "convert",
            "export",
            "save as",
            "format conversion",
            "repair",
            "fix",
            "make valid",
            "valid geometry",
            "geometry validation",
        )
        if not self._has_any_term(request, common_terms):
            return None

        self._emit_progress(
            progress_callback,
            stage="input_inspection",
            message="I detected a common vector operation and will first try a deterministic GeoPandas workflow.",
            data={"dataset_count": len(dataset_paths)},
        )
        datasets = self._load_input_datasets(dataset_paths)
        geo_datasets = [item for item in datasets if item["is_geo"]]
        table_datasets = [item for item in datasets if not item["is_geo"]]

        if self._has_any_term(request, ("convert", "export", "save as", "format conversion")) and datasets:
            result = self._try_format_conversion(query, datasets[0], start_time, progress_callback)
            if result:
                return result

        if self._has_any_term(request, ("repair", "fix", "make valid", "valid geometry", "geometry validation")) and geo_datasets:
            result = self._try_geometry_repair(query, geo_datasets[0], start_time, progress_callback)
            if result:
                return result

        if self._looks_like_buffered_point_coverage(request) and len(geo_datasets) >= 2:
            result = self._try_buffered_point_coverage(query, geo_datasets, start_time, progress_callback)
            if result:
                return result

        if self._has_any_term(request, ("dissolve", "aggregate", "group by")) and geo_datasets:
            result = self._try_dissolve(query, geo_datasets[0], start_time, progress_callback)
            if result:
                return result

        if self._has_any_term(request, ("nearest", "closest", "distance to")) and len(geo_datasets) >= 2:
            result = self._try_nearest_distance(query, geo_datasets, start_time, progress_callback)
            if result:
                return result

        if self._has_any_term(request, ("filter", "select", "where")) and datasets:
            result = self._try_attribute_filter(query, datasets[0], start_time, progress_callback)
            if result:
                return result

        if self._has_any_term(request, ("count", "counts", "number of", "how many")) and len(geo_datasets) >= 2:
            result = self._try_point_counts_by_polygon(query, geo_datasets, start_time, progress_callback)
            if result:
                return result

        if self._has_any_term(request, ("join", "merge")) and len(datasets) >= 2:
            result = self._try_attribute_join(query, datasets, geo_datasets, table_datasets, start_time, progress_callback)
            if result:
                return result
            if "spatial" in request and len(geo_datasets) >= 2:
                return self._try_spatial_join(query, geo_datasets, start_time, progress_callback)

        if self._has_any_term(request, ("buffer",)) and geo_datasets:
            return self._try_buffer(query, geo_datasets[0], start_time, progress_callback)

        if self._has_any_term(request, ("clip", "intersect", "intersection")) and len(geo_datasets) >= 2:
            return self._try_overlay(query, geo_datasets, start_time, progress_callback)

        if self._has_any_term(request, ("area", "length", "centroid")) and geo_datasets:
            result = self._try_add_geometry_measurements(query, geo_datasets[0], start_time, progress_callback)
            if result:
                return result

        return None

    def _has_any_term(self, request: str, terms: Tuple[str, ...]) -> bool:
        for term in terms:
            escaped = re.escape(term.lower())
            if re.search(rf"\b{escaped}\b", request):
                return True
        return False

    def _explicitly_requests_model_workflow(self, request: str) -> bool:
        return self._has_any_term(
            request,
            (
                "use llm",
                "use the llm",
                "llm workflow",
                "model-driven",
                "model driven",
                "use model",
                "use the model",
                "model-backed",
                "model backed",
                "not deterministic",
                "avoid deterministic",
                "do not use deterministic",
                "skip deterministic",
            ),
        )

    def _looks_like_buffered_point_coverage(self, request: str) -> bool:
        return (
            self._has_any_term(request, ("buffer",))
            and self._has_any_term(request, ("intersect", "intersection", "coverage"))
            and (
                "coverage_pct" in request
                or "covered_area" in request
                or "coverage percent" in request
                or "coverage percentage" in request
                or "coverage polygon" in request
            )
        )

    def _requested_epsg_crs(self, query: str) -> Optional[str]:
        match = re.search(r"\bepsg\s*:\s*(\d{4,6})\b", query or "", flags=re.IGNORECASE)
        if match:
            return f"EPSG:{match.group(1)}"
        return None

    def _numeric_work_crs(self, gdf: gpd.GeoDataFrame):
        if gdf.crs is None:
            return "EPSG:3857"
        try:
            if gdf.crs.is_projected:
                return gdf.crs
        except Exception:
            pass
        try:
            return gdf.estimate_utm_crs() or "EPSG:3857"
        except Exception:
            return "EPSG:3857"

    def _query_column(self, query: str, columns: List[str]) -> Optional[str]:
        request = (query or "").lower()
        normalized_columns = {
            re.sub(r"[^a-z0-9]", "", str(column).lower()): column
            for column in columns
        }

        for pattern in (r"\bby\s+([A-Za-z_][A-Za-z0-9_]*)", r"\bwhere\s+([A-Za-z_][A-Za-z0-9_]*)"):
            match = re.search(pattern, query or "", flags=re.IGNORECASE)
            if match:
                candidate = re.sub(r"[^a-z0-9]", "", match.group(1).lower())
                if candidate in normalized_columns:
                    return normalized_columns[candidate]

        for normalized, column in normalized_columns.items():
            if normalized and re.search(rf"\b{re.escape(str(column).lower())}\b", request):
                return column
            if normalized and normalized in re.sub(r"[^a-z0-9]", "", request):
                return column
        return None

    def _requested_join_output_column(self, query: str) -> Optional[str]:
        patterns = (
            r"\b(?:rename|name|call)\s+(?:the\s+)?(?:joined\s+)?(?:\w+\s+){0,5}(?:column|field)\s+(?:to|as)\s+([A-Za-z_][A-Za-z0-9_]*)\b",
            r"\b(?:rename|name|call)\s+(?:the\s+)?(?:joined\s+)?(?:\w+\s+){0,5}(?:value|rate|prevalence)\s+(?:column|field)?\s*(?:to|as)\s+([A-Za-z_][A-Za-z0-9_]*)\b",
            r"\b(?:output|joined)\s+(?:value\s+)?(?:column|field)\s+(?:named|called)\s+([A-Za-z_][A-Za-z0-9_]*)\b",
        )
        for pattern in patterns:
            match = re.search(pattern, query or "", flags=re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def _rename_requested_join_value_column(
        self,
        joined: pd.DataFrame,
        joined_columns: List[str],
        query: str,
    ) -> Tuple[pd.DataFrame, List[str], Optional[Tuple[str, str]]]:
        target = self._requested_join_output_column(query)
        if not target or target in joined.columns:
            return joined, joined_columns, None

        candidates = [column for column in joined_columns if column in joined.columns]
        if not candidates:
            return joined, joined_columns, None

        normalized_query = re.sub(r"[^a-z0-9]+", " ", (query or "").lower())
        preferred_tokens = ("obesity", "prevalence", "rate", "value", "estimate", "data_value")

        def score(column: str) -> Tuple[int, int, int]:
            text = str(column).lower()
            token_score = sum(1 for token in preferred_tokens if token in text or token.replace("_", " ") in normalized_query)
            numeric_score = int(pd.api.types.is_numeric_dtype(joined[column]))
            non_null_score = int(joined[column].notna().any())
            return (token_score, numeric_score, non_null_score)

        source = max(candidates, key=score)
        if score(source) == (0, 0, 0):
            return joined, joined_columns, None

        renamed = joined.rename(columns={source: target})
        renamed_columns = [target if column == source else column for column in joined_columns]
        return renamed, renamed_columns, (source, target)

    def _try_format_conversion(self, query, dataset_item, start_time, progress_callback=None):
        data = dataset_item["data"].copy()
        request = (query or "").lower()
        if dataset_item["is_geo"]:
            result = data
        elif "geojson" in request or "geopackage" in request or "gpkg" in request:
            return None
        else:
            result = data

        output_path, metadata, label = self._save_artifact_direct(result, query)
        summary = f"Converted the input dataset to {label} and saved {metadata.get('feature_count')} record(s)."
        script = "# Loaded the input dataset and saved it in the requested output format.\n"
        self._emit_progress(
            progress_callback,
            stage="artifact_generation",
            message=summary,
            data={"operation": "format_conversion", "output_format": label, "output_path": output_path},
        )
        return self._build_direct_response(
            start_time,
            query,
            [dataset_item["path"]],
            output_path,
            metadata,
            summary,
            script,
            progress_callback,
        )

    def _try_geometry_repair(self, query, geo_item, start_time, progress_callback=None):
        gdf = geo_item["data"].copy()
        if gdf.empty:
            return None
        invalid_before = int((~gdf.geometry.is_valid).sum())
        if hasattr(gdf.geometry, "make_valid"):
            gdf["geometry"] = gdf.geometry.make_valid()
        else:
            gdf["geometry"] = gdf.geometry.buffer(0)
        gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()].copy()
        invalid_after = int((~gdf.geometry.is_valid).sum())
        output_path, metadata, label = self._save_artifact_direct(gdf, query)
        summary = (
            f"Checked and repaired geometries. Invalid geometries changed from {invalid_before} "
            f"to {invalid_after}; saved {len(gdf)} feature(s) as {label}."
        )
        script = "# Loaded vector data, repaired invalid geometries with make_valid/buffer(0), and saved the result.\n"
        self._emit_progress(
            progress_callback,
            stage="data_validation",
            message=summary,
            data={"operation": "geometry_repair", "invalid_before": invalid_before, "invalid_after": invalid_after},
        )
        return self._build_direct_response(start_time, query, [geo_item["path"]], output_path, metadata, summary, script, progress_callback)

    def _try_add_geometry_measurements(self, query, geo_item, start_time, progress_callback=None):
        request = (query or "").lower()
        gdf = geo_item["data"].copy()
        if gdf.empty:
            return None
        work_crs = self._numeric_work_crs(gdf)
        work = gdf.to_crs(work_crs) if gdf.crs else gdf.set_crs("EPSG:4326").to_crs(work_crs)
        added_fields = []

        if self._has_any_term(request, ("area",)):
            gdf["area_sq_m"] = work.geometry.area
            gdf["area_sq_km"] = gdf["area_sq_m"] / 1_000_000
            added_fields.extend(["area_sq_m", "area_sq_km"])
        if self._has_any_term(request, ("length",)):
            gdf["length_m"] = work.geometry.length
            gdf["length_km"] = gdf["length_m"] / 1000
            added_fields.extend(["length_m", "length_km"])
        if self._has_any_term(request, ("centroid",)):
            centroids = work.geometry.centroid
            centroid_gdf = gpd.GeoSeries(centroids, crs=work.crs).to_crs("EPSG:4326")
            gdf["centroid_lon"] = centroid_gdf.x.values
            gdf["centroid_lat"] = centroid_gdf.y.values
            added_fields.extend(["centroid_lon", "centroid_lat"])

        if not added_fields:
            return None
        output_path, metadata, label = self._save_artifact_direct(gdf, query)
        summary = f"Added geometry measurement field(s): {', '.join(added_fields)} and saved {len(gdf)} feature(s) as {label}."
        script = "# Loaded vector data, projected to a measurement CRS, calculated requested geometry fields, and saved the result.\n"
        self._emit_progress(
            progress_callback,
            stage="analysis_execution",
            message=summary,
            data={"operation": "geometry_measurements", "added_fields": added_fields, "work_crs": str(work_crs)},
        )
        return self._build_direct_response(start_time, query, [geo_item["path"]], output_path, metadata, summary, script, progress_callback)

    def _try_dissolve(self, query, geo_item, start_time, progress_callback=None):
        gdf = geo_item["data"].copy()
        if gdf.empty:
            return None
        geometry_column = gdf.geometry.name
        dissolve_column = self._query_column(query, [column for column in gdf.columns if column != geometry_column])
        if not dissolve_column:
            return None
        numeric_columns = [
            column for column in gdf.select_dtypes(include=[np.number]).columns
            if column != dissolve_column
        ]
        aggfunc = {column: "sum" for column in numeric_columns}
        result = gdf.dissolve(by=dissolve_column, as_index=False, aggfunc=aggfunc or "first")
        if result.empty:
            return None
        output_path, metadata, label = self._save_artifact_direct(result, query)
        summary = f"Dissolved {len(gdf)} feature(s) by {dissolve_column} into {len(result)} feature(s) and saved the result as {label}."
        script = f"# Loaded vector data and ran gdf.dissolve(by={dissolve_column!r}).\n"
        self._emit_progress(
            progress_callback,
            stage="analysis_execution",
            message=summary,
            data={"operation": "dissolve", "dissolve_column": dissolve_column, "output_features": len(result)},
        )
        return self._build_direct_response(start_time, query, [geo_item["path"]], output_path, metadata, summary, script, progress_callback)

    def _try_nearest_distance(self, query, geo_datasets, start_time, progress_callback=None):
        left_item, right_item = geo_datasets[0], geo_datasets[1]
        left = left_item["data"].copy()
        right = right_item["data"].copy()
        if left.empty or right.empty:
            return None
        work_crs = self._numeric_work_crs(left)
        left_work = left.to_crs(work_crs) if left.crs else left.set_crs("EPSG:4326").to_crs(work_crs)
        if right.crs:
            right_work = right.to_crs(work_crs)
        else:
            right_work = right.set_crs(left.crs or "EPSG:4326").to_crs(work_crs)
        try:
            nearest = gpd.sjoin_nearest(
                left_work,
                right_work,
                how="left",
                distance_col="nearest_distance_m",
                lsuffix="left",
                rsuffix="right",
            )
        except Exception:
            return None
        nearest = nearest.drop(columns=["index_right"], errors="ignore")
        result = gpd.GeoDataFrame(nearest, geometry=left_work.geometry.name, crs=work_crs)
        if left.crs:
            result = result.to_crs(left.crs)
        output_path, metadata, label = self._save_artifact_direct(result, query)
        summary = f"Calculated nearest-feature distances for {len(result)} feature(s) and saved the result as {label}."
        script = "# Loaded two vector datasets, projected to a meter-based CRS, and ran gpd.sjoin_nearest(..., distance_col='nearest_distance_m').\n"
        self._emit_progress(
            progress_callback,
            stage="analysis_execution",
            message=summary,
            data={"operation": "nearest_distance", "output_features": len(result), "work_crs": str(work_crs)},
        )
        return self._build_direct_response(start_time, query, [left_item["path"], right_item["path"]], output_path, metadata, summary, script, progress_callback)

    def _parse_filter_expression(self, query: str, columns: List[str]):
        pattern = re.compile(
            r"\b(?:where|filter|select)\b.*?\b([A-Za-z_][A-Za-z0-9_]*)\s*(==|=|>=|<=|>|<)\s*['\"]?([^'\"\n]+?)['\"]?(?:\s|$|,|\.)",
            flags=re.IGNORECASE,
        )
        match = pattern.search(query or "")
        if not match:
            return None
        column_lookup = {str(column).lower(): column for column in columns}
        column = column_lookup.get(match.group(1).lower())
        if column is None:
            return None
        return column, match.group(2), match.group(3).strip()

    def _try_attribute_filter(self, query, dataset_item, start_time, progress_callback=None):
        data = dataset_item["data"].copy()
        expression = self._parse_filter_expression(query, list(data.columns))
        if not expression:
            return None
        column, operator, raw_value = expression
        series = data[column]
        if pd.api.types.is_numeric_dtype(series):
            try:
                value = float(raw_value)
            except ValueError:
                return None
        else:
            value = raw_value

        if operator in {"=", "=="}:
            mask = series.astype(str).str.lower() == str(value).lower() if not pd.api.types.is_numeric_dtype(series) else series == value
        elif operator == ">":
            mask = series > value
        elif operator == "<":
            mask = series < value
        elif operator == ">=":
            mask = series >= value
        elif operator == "<=":
            mask = series <= value
        else:
            return None
        result = data[mask].copy()
        if result.empty:
            return None
        output_path, metadata, label = self._save_artifact_direct(result, query)
        summary = f"Filtered {len(data)} record(s) where {column} {operator} {raw_value}; saved {len(result)} record(s) as {label}."
        script = f"# Loaded data and filtered rows where {column} {operator} {raw_value!r}.\n"
        self._emit_progress(
            progress_callback,
            stage="analysis_execution",
            message=summary,
            data={"operation": "attribute_filter", "column": column, "operator": operator, "output_features": len(result)},
        )
        return self._build_direct_response(start_time, query, [dataset_item["path"]], output_path, metadata, summary, script, progress_callback)

    def _try_attribute_join(self, query, datasets, geo_datasets, table_datasets, start_time, progress_callback=None):
        if not geo_datasets:
            return None
        left_item = geo_datasets[0]
        right_candidates = [item for item in datasets if item is not left_item]
        best_plan = None
        for right_item in right_candidates:
            plan = self._best_attribute_join(left_item["data"], right_item["data"])
            if plan and (best_plan is None or plan["score"] > best_plan["plan"]["score"]):
                best_plan = {"right_item": right_item, "plan": plan}
        if not best_plan:
            return None

        left = left_item["data"].copy()
        right = best_plan["right_item"]["data"].copy()
        plan = best_plan["plan"]
        width = plan["width"]
        left_key = "__gas_join_key"
        right_key = "__gas_join_key"
        left[left_key] = self._normalize_join_series(left[plan["left_column"]], width=width)
        right[right_key] = self._normalize_join_series(right[plan["right_column"]], width=width)

        right_columns = [column for column in right.columns if column != "geometry"]
        right_reduced = right[right_columns].drop_duplicates(subset=[right_key])
        joined = left.merge(right_reduced, on=left_key, how="left", suffixes=("", "_joined"))
        joined = gpd.GeoDataFrame(joined.drop(columns=[left_key]), geometry=left.geometry.name, crs=left.crs)
        if right_key in joined.columns:
            joined = joined.drop(columns=[right_key])

        joined_columns = [
            column for column in right_reduced.columns
            if column != right_key and column not in {plan["right_column"]}
        ]
        joined, joined_columns, rename_info = self._rename_requested_join_value_column(joined, joined_columns, query)
        matched_rows = 0
        if joined_columns:
            matched_rows = int(joined[joined_columns].notna().any(axis=1).sum())
        if matched_rows == 0:
            self._emit_progress(
                progress_callback,
                stage="warning",
                message="The deterministic attribute join found candidate keys but produced zero matched rows, so I will fall back to the model-driven workflow.",
                data=plan,
            )
            return None

        output_path, metadata, label = self._save_artifact_direct(joined, query)
        summary = (
            f"Joined {left_item['rows']} spatial features with {best_plan['right_item']['rows']} table/vector records "
            f"using {plan['left_column']} = {plan['right_column']}. "
            f"Matched {matched_rows} output feature(s) and saved the result as {label}."
        )
        if rename_info:
            summary += f" Renamed joined column {rename_info[0]} to {rename_info[1]}."
        script = (
            "import geopandas as gpd\nimport pandas as pd\n"
            f"left = gpd.read_file({left_item['path']!r})\n"
            f"right = pd.read_csv({best_plan['right_item']['path']!r})\n"
            f"# Normalize and join {plan['left_column']} to {plan['right_column']}.\n"
        )
        self._emit_progress(
            progress_callback,
            stage="analysis_execution",
            message=summary,
            data={
                "operation": "attribute_join",
                "left_column": plan["left_column"],
                "right_column": plan["right_column"],
                "matched_rows": matched_rows,
                "output_features": len(joined),
                "renamed_column": {"from": rename_info[0], "to": rename_info[1]} if rename_info else None,
            },
        )
        return self._build_direct_response(start_time, query, [item["path"] for item in datasets], output_path, metadata, summary, script, progress_callback)

    def _geometry_family(self, gdf: gpd.GeoDataFrame) -> set[str]:
        geometry_types = set(gdf.geometry.geom_type.dropna().str.lower())
        families = set()
        if any("point" in geom_type for geom_type in geometry_types):
            families.add("point")
        if any("polygon" in geom_type for geom_type in geometry_types):
            families.add("polygon")
        if any("line" in geom_type for geom_type in geometry_types):
            families.add("line")
        return families

    def _count_field_name(self, query: str) -> str:
        request = (query or "").lower()
        if "hospital" in request:
            return "hospital_count"
        if "restaurant" in request:
            return "restaurant_count"
        if "point" in request:
            return "point_count"
        return "feature_count"

    def _try_point_counts_by_polygon(self, query, geo_datasets, start_time, progress_callback=None):
        polygon_items = [
            item for item in geo_datasets
            if "polygon" in self._geometry_family(item["data"])
        ]
        point_items = [
            item for item in geo_datasets
            if "point" in self._geometry_family(item["data"])
        ]
        if not polygon_items or not point_items:
            return None

        polygon_item = polygon_items[0]
        point_item = point_items[0]
        polygons = polygon_item["data"].copy()
        points = point_item["data"].copy()
        if polygons.empty or points.empty:
            return None

        if polygons.crs and points.crs and str(polygons.crs) != str(points.crs):
            points = points.to_crs(polygons.crs)

        polygons = polygons.reset_index(drop=True)
        polygon_index_name = "__gas_polygon_index"
        polygons[polygon_index_name] = polygons.index

        joined = gpd.sjoin(
            points,
            polygons[[polygon_index_name, polygons.geometry.name]],
            how="left",
            predicate="within",
        )
        count_field = self._count_field_name(query)
        counts = joined.groupby(polygon_index_name).size()
        result = polygons.copy()
        result[count_field] = result[polygon_index_name].map(counts).fillna(0).astype(int)
        result = result.drop(columns=[polygon_index_name])

        output_path, metadata, label = self._save_artifact_direct(result, query)
        total_points = int(result[count_field].sum())
        summary = (
            f"Counted {total_points} point feature(s) within {len(result)} polygon feature(s) "
            f"and saved the county-level result as {label} with a {count_field} field."
        )
        script = (
            "import geopandas as gpd\n"
            "# Loaded polygon and point datasets, aligned CRS, ran gpd.sjoin(points, polygons, predicate='within'), "
            f"and counted matches into {count_field}.\n"
        )
        self._emit_progress(
            progress_callback,
            stage="analysis_execution",
            message=summary,
            data={
                "operation": "point_count_by_polygon",
                "polygon_features": len(result),
                "point_features": len(points),
                "count_field": count_field,
                "matched_points": total_points,
            },
        )
        return self._build_direct_response(
            start_time,
            query,
            [polygon_item["path"], point_item["path"]],
            output_path,
            metadata,
            summary,
            script,
            progress_callback,
        )

    def _try_buffered_point_coverage(self, query, geo_datasets, start_time, progress_callback=None):
        polygon_items = [
            item for item in geo_datasets
            if "polygon" in self._geometry_family(item["data"])
        ]
        point_items = [
            item for item in geo_datasets
            if "point" in self._geometry_family(item["data"])
        ]
        if not polygon_items or not point_items:
            return None

        distance_m = self._parse_distance_meters(query)
        if not distance_m:
            return None

        polygon_item = polygon_items[0]
        point_item = point_items[0]
        polygons = polygon_item["data"].copy()
        points = point_item["data"].copy()
        if polygons.empty or points.empty:
            return None

        if polygons.crs is None:
            polygons = polygons.set_crs("EPSG:4326")
        if points.crs is None:
            points = points.set_crs("EPSG:4326")

        work_crs = self._requested_epsg_crs(query) or "EPSG:5070"
        polygons_work = polygons.to_crs(work_crs).reset_index(drop=True)
        points_work = points.to_crs(work_crs)

        polygon_index_name = "__gas_polygon_index"
        polygons_work[polygon_index_name] = polygons_work.index
        polygons_work["county_area_m2"] = polygons_work.geometry.area

        buffers = points_work.geometry.buffer(distance_m)
        buffer_union = buffers.union_all() if hasattr(buffers, "union_all") else buffers.unary_union
        result_work = polygons_work.copy()
        result_work["covered_area_m2"] = 0.0
        if not buffer_union.is_empty:
            coverage = gpd.GeoDataFrame({"geometry": [buffer_union]}, crs=work_crs)
            intersections = gpd.overlay(
                polygons_work[[polygon_index_name, "geometry"]],
                coverage,
                how="intersection",
            )
            if not intersections.empty:
                covered = intersections.geometry.area.groupby(intersections[polygon_index_name]).sum()
                result_work["covered_area_m2"] = result_work[polygon_index_name].map(covered).fillna(0.0)

        with np.errstate(divide="ignore", invalid="ignore"):
            coverage_pct = result_work["covered_area_m2"] / result_work["county_area_m2"] * 100.0
        result_work["coverage_pct"] = coverage_pct.replace([np.inf, -np.inf], np.nan).fillna(0.0).clip(0, 100)
        result_work = result_work.drop(columns=[polygon_index_name])
        result = result_work.to_crs("EPSG:4326")

        output_path, metadata, label = self._save_artifact_direct(result, query)
        covered_counties = int((result["coverage_pct"] > 0).sum())
        summary = (
            f"Computed buffered point coverage for {len(result)} polygon feature(s) using "
            f"{len(points)} point feature(s), a {distance_m:,.0f}-meter buffer, and {work_crs}. "
            f"Saved {label} with covered_area_m2, county_area_m2, and coverage_pct fields; "
            f"{covered_counties} polygon feature(s) have nonzero coverage."
        )
        script = (
            "import geopandas as gpd\n"
            f"# Loaded polygon and point inputs, reprojected to {work_crs}, buffered points by {distance_m:.3f} meters, "
            "dissolved buffers, intersected coverage with polygons, calculated covered_area_m2, "
            "county_area_m2, and coverage_pct, then reprojected to EPSG:4326.\n"
        )
        self._emit_progress(
            progress_callback,
            stage="analysis_execution",
            message=summary,
            data={
                "operation": "buffered_point_coverage",
                "polygon_features": len(result),
                "point_features": len(points),
                "distance_m": distance_m,
                "work_crs": work_crs,
                "covered_polygons": covered_counties,
            },
        )
        return self._build_direct_response(
            start_time,
            query,
            [polygon_item["path"], point_item["path"]],
            output_path,
            metadata,
            summary,
            script,
            progress_callback,
        )

    def _try_spatial_join(self, query, geo_datasets, start_time, progress_callback=None):
        left_item, right_item = geo_datasets[0], geo_datasets[1]
        left = left_item["data"].copy()
        right = right_item["data"].copy()
        if left.crs and right.crs and str(left.crs) != str(right.crs):
            right = right.to_crs(left.crs)
        joined = gpd.sjoin(left, right.drop(columns=[]), how="left", predicate="intersects", lsuffix="left", rsuffix="right")
        if "index_right" in joined.columns:
            matched_rows = int(joined["index_right"].notna().sum())
        else:
            matched_rows = len(joined)
        if matched_rows == 0:
            return None
        output_path, metadata, label = self._save_artifact_direct(joined, query)
        summary = f"Performed a spatial join using intersects and saved {len(joined)} joined feature(s) as {label}."
        script = "import geopandas as gpd\n# Loaded two vector datasets, aligned CRS, and ran gpd.sjoin(..., predicate='intersects').\n"
        return self._build_direct_response(start_time, query, [item["path"] for item in geo_datasets], output_path, metadata, summary, script, progress_callback)

    def _try_buffer(self, query, geo_item, start_time, progress_callback=None):
        distance_m = self._parse_distance_meters(query)
        if not distance_m:
            return None
        gdf = geo_item["data"].copy()
        original_crs = gdf.crs
        projected_crs = None
        try:
            projected_crs = gdf.estimate_utm_crs()
        except Exception:
            projected_crs = None
        work = gdf.to_crs(projected_crs or "EPSG:3857") if original_crs else gdf.set_crs("EPSG:4326").to_crs("EPSG:3857")
        work["geometry"] = work.geometry.buffer(distance_m)
        result = work.to_crs(original_crs) if original_crs else work.to_crs("EPSG:4326")
        output_path, metadata, label = self._save_artifact_direct(result, query)
        summary = f"Created {distance_m:,.2f}-meter buffers for {len(result)} feature(s) and saved the result as {label}."
        script = "import geopandas as gpd\n# Loaded vector data, projected to a meter-based CRS, buffered, and reprojected back.\n"
        return self._build_direct_response(start_time, query, [geo_item["path"]], output_path, metadata, summary, script, progress_callback)

    def _try_overlay(self, query, geo_datasets, start_time, progress_callback=None):
        left_item, right_item = geo_datasets[0], geo_datasets[1]
        left = left_item["data"].copy()
        right = right_item["data"].copy()
        if left.crs and right.crs and str(left.crs) != str(right.crs):
            right = right.to_crs(left.crs)
        if "clip" in (query or "").lower():
            result = gpd.clip(left, right)
            operation = "clip"
        else:
            result = gpd.overlay(left, right, how="intersection")
            operation = "intersection"
        if result.empty:
            return None
        output_path, metadata, label = self._save_artifact_direct(result, query)
        summary = f"Completed a deterministic {operation} operation and saved {len(result)} feature(s) as {label}."
        script = f"import geopandas as gpd\n# Loaded two vector datasets, aligned CRS, and ran GeoPandas {operation}.\n"
        return self._build_direct_response(start_time, query, [item["path"] for item in geo_datasets], output_path, metadata, summary, script, progress_callback)

    def _preferred_vector_output(self, task: str) -> Tuple[str, str, str]:
        request = (task or "").lower()
        if re.search(r"\b(geojson|\.geojson)\b", request) and not re.search(r"\b(geopackage|gpkg|\.gpkg)\b", request):
            return ".geojson", "GeoJSON", "GeoJSON"
        return ".gpkg", "GPKG", "GeoPackage"

    def _save_result(self, task: str) -> Tuple[List[str], Dict[str, Any], List[Dict[str, Any]]]:
        registered_keys = self.final_artifact_keys or ([self.final_artifact_key] if self.final_artifact_key else [])
        if not registered_keys:
            return [], {
                "type": None,
                "dimensions": None,
                "feature_count": None
            }, []

        out_dir = self._output_dir()
        base_name = self._generate_filename(task)
        saved_paths: List[str] = []
        saved_artifacts: List[Dict[str, Any]] = []

        for key_index, key in enumerate(registered_keys, start=1):
            if key not in self.registry:
                raise KeyError(f"Registered artifact '{key}' was not found in the registry.")
            value = self.registry[key]
            items = list(value) if isinstance(value, (list, tuple)) else [value]
            for item_index, obj in enumerate(items, start=1):
                is_geo = isinstance(obj, gpd.GeoDataFrame)
                ext, driver, _ = self._preferred_vector_output(task) if is_geo else (".csv", None, "CSV")
                suffix = f"_{key_index}"
                if len(items) > 1:
                    suffix += f"_{item_index}"
                fname = f"{base_name}{suffix}{ext}"
                path = os.path.join(out_dir, fname)

                if self.debug:
                    logging.info("Saving final vector result to: %s", path)

                if is_geo:
                    obj.to_file(path, driver=driver)
                    metadata_type = "vector"
                elif isinstance(obj, pd.DataFrame):
                    obj.to_csv(path, index=False)
                    metadata_type = "table"
                else:
                    raise TypeError(f"Unsupported final artifact type: {type(obj)}")

                metadata = {
                    "type": metadata_type,
                    "dimensions": list(obj.shape),
                    "feature_count": len(obj)
                }
                saved_paths.append(path)
                saved_artifacts.append({"key": key, "path": path, "metadata": metadata})

        self.number_of_artifacts = len(saved_paths)
        primary_metadata = saved_artifacts[0]["metadata"] if saved_artifacts else {
            "type": None,
            "dimensions": None,
            "feature_count": None
        }
        return saved_paths, primary_metadata, saved_artifacts

    # --------------------------
    # Final Script Cleaning (Optional)
    # --------------------------
    def _clean_final_script(self, raw_script: str, user_query: str) -> str:
        """Ask the LLM to produce a consolidated, non-redundant version of the script."""
        if not raw_script.strip():
            return ""
        try:
            prompt = (
                "Below is a history of executed Python scripts (separated by '# --- Step ---'). "
                "Please produce a single, clean, and non-redundant script that accomplishes the original task. "
                "Remove duplicate imports, repeated file reads, and unnecessary intermediate steps. "
                "Keep only the essential code that loads data (once), performs analysis, and produces the final result. "
                f"Original task: {user_query}\n\n"
                f"Raw script history:\n{raw_script}\n\n"
                "Output only the cleaned Python code, no explanations."
            )
            res = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2
            )
            usage = getattr(res, "usage", None)
            if usage:
                self.input_tokens += usage.prompt_tokens or 0
                self.output_tokens += usage.completion_tokens or 0
            cleaned = res.choices[0].message.content.strip()
            # Remove markdown code fences if present
            cleaned = re.sub(r'^```python\n?', '', cleaned)
            cleaned = re.sub(r'\n```$', '', cleaned)
            return cleaned
        except Exception as e:
            if self.debug:
                logging.warning("Could not clean final script: %s", e)
            return raw_script

    # --------------------------
    # Main Loop
    # --------------------------

    def run(self, query: str, input_dataset_paths=None, progress_callback=None) -> Dict[str, Any]:
        start_time = time.time()
        user_query = query
        dataset_path = self.normalize_dataset_paths(input_dataset_paths)
        self._emit_progress(
            progress_callback,
            stage="start",
            message=f"I will load the requested vector/tabular inputs, run code-driven analysis, and save a final dataset artifact from {len(dataset_path)} dataset reference(s).",
            data={"dataset_count": len(dataset_path), "max_iterations": 40},
        )

        if self.debug:
            # print(f"\n[Agent] Starting Task: {user_query}")
            # print(f"[Agent] Input Datasets: {dataset_path}")
            pass

        # Reset metrics for new run
        self.llm_calls = 0
        self.code_executions = 0
        self.retries = 0
        self.kb_searches = 0
        self.number_of_artifacts = 0
        self.final_artifact_key = None
        self.final_artifact_keys = []
        self.input_tokens = 0
        self.output_tokens = 0
        self.registry.clear()  # start fresh

        self.runtime_memory["plan_status"] = "Starting task. Identifying initial loading steps."

        direct_result = self._try_deterministic_workflow(
            user_query,
            dataset_path,
            start_time,
            progress_callback=progress_callback,
        )
        if direct_result:
            validation_errors = self._deterministic_result_validation_errors(user_query, direct_result)
            if not validation_errors:
                return direct_result
            self._emit_progress(
                progress_callback,
                stage="validation",
                message=(
                    "The deterministic GeoPandas shortcut produced an artifact, but validation found it did not "
                    "satisfy the request. I will continue with the model-backed workflow instead."
                ),
                data={"validation_errors": validation_errors},
            )
            self.number_of_artifacts = 0

        # Inject paths and initial registry state into first user message
        user_content = f"Task: {user_query}\nFiles available: {dataset_path}\n\nInitial registry is empty."
        messages = [self.system_prompt, {"role": "user", "content": user_content}]

        final_text_response = ""

        for turn in range(40):  # Max iterations
            self._emit_progress(
                progress_callback,
                stage="planning",
                message=f"I am planning the next analysis step and deciding whether to execute code, search guidance, or register the final artifact. This is iteration {turn + 1}.",
                data={"iteration": turn + 1},
            )
            if self.debug:
                # print(f"\n--- Iteration {turn + 1} ---")
                pass

            self.llm_calls += 1

            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=self.tools,
                    tool_choice="auto"
                )
                usage = getattr(response, "usage", None)
                if usage:
                    self.input_tokens += usage.prompt_tokens or 0
                    self.output_tokens += usage.completion_tokens or 0
            except Exception as e:
                if self.debug:
                    logging.warning("LLM call failed: %s", e)
                self._emit_progress(
                    progress_callback,
                    stage="retry",
                    message=f"The model call failed before the analysis could continue, so I will return the failure details: {e}",
                )
                # Return error with empty complementary structure
                return {
                    "error": f"LLM Call failed: {e}",
                    "complementary": {
                        "Execution": {"Inputs": {}, "Outputs": {}},
                        "Provenance": {"Lineage": {}, "Tool Calls": {}, "LLM Calls": {}},
                        "Artifacts and Logs": {"Inline Artifacts": {}, "Persisted Artifacts": {}}
                    }
                }

            msg = response.choices[0].message
            messages.append(msg)

            if not msg.tool_calls:
                if self.debug:
                    # print("[Agent] Task completed. Preparing final response.")
                    pass
                self._emit_progress(
                    progress_callback,
                    stage="response_preparation",
                    message="The model finished its analysis loop and provided a final text response, so I will prepare the saved output dataset.",
                )
                final_text_response = msg.content
                break

            for tool_call in msg.tool_calls:
                fn_name = tool_call.function.name
                args = json.loads(tool_call.function.arguments)

                if self.debug:
                    # print(f"[Tool Call] {fn_name}: {args.get('purpose', args.get('variable_name', args.get('query', '')))}")
                    pass
                tool_messages = {
                    "execute_script": "I will execute Python analysis code in the sandbox to load, inspect, transform, or analyze the dataset.",
                    "search_knowledge_base": "I will search the knowledge base for implementation guidance before deciding the next code step.",
                    "register_final_artifact": "I will register the selected in-memory result as the final artifact to save.",
                }
                self._emit_progress(
                    progress_callback,
                    stage="analysis_execution",
                    message=tool_messages.get(fn_name, f"I will run the {fn_name} tool and inspect its result."),
                    data={"tool_name": fn_name},
                )

                result_content = ""

                if fn_name == "execute_script":
                    res = self._execute_in_sandbox(args["script"])
                    if res["error"]:
                        self.retries += 1
                        self._emit_progress(
                            progress_callback,
                            stage="retry",
                            message="The analysis code did not run successfully, so I will use the error feedback to revise the next step.",
                            data={"retry_count": self.retries},
                        )
                        if self.debug:
                            # print(f"[Sandbox] Execution Failed. Error length: {len(res['error'])}")
                            pass
                        result_content = res["full_response"]  # includes error + registry
                        self.runtime_memory["errors"].append({"script": args["script"], "error": res["error"]})
                    else:
                        self._emit_progress(
                            progress_callback,
                            stage="code_execution",
                            message="The analysis code ran successfully, so I will use the resulting variables and registry state for the next decision.",
                        )
                        if self.debug:
                            # print(f"[Sandbox] Execution Successful. Captured output length: {len(res['stdout'])}")
                            pass
                        result_content = res["full_response"]  # includes stdout + registry
                        self.runtime_memory["facts"].append(f"Executed script for: {args.get('purpose', 'unknown purpose')}")

                elif fn_name == "search_knowledge_base":
                    result_content = self._search_kb(args["query"])
                    if self.debug:
                        logging.info("KB search found results for: %s", args["query"])

                elif fn_name == "register_final_artifact":
                    requested_keys = args.get("variable_names") or []
                    if not requested_keys and args.get("variable_name"):
                        requested_keys = [args["variable_name"]]
                    missing_keys = [name for name in requested_keys if name not in self.registry]
                    if requested_keys and not missing_keys:
                        self.final_artifact_keys = requested_keys
                        self.final_artifact_key = requested_keys[0]
                        self._emit_progress(
                            progress_callback,
                            stage="artifact_generation",
                            message=f"I found {len(requested_keys)} registered final artifact variable(s) and will save them at the end.",
                            data={"variable_names": requested_keys},
                        )
                        if self.debug:
                            logging.info("Final artifacts registered: %s", requested_keys)
                        result_content = f"Artifacts {requested_keys} registered successfully. They will be saved at the end."
                    else:
                        self._emit_progress(
                            progress_callback,
                            stage="warning",
                            message="I could not find one or more requested final artifact variables, so I will ask the next step to create or register valid results.",
                            data={"requested_variable_names": requested_keys, "missing_variable_names": missing_keys},
                        )
                        if self.debug:
                            logging.error("Failed to register artifacts: missing %s", missing_keys or requested_keys)
                        # Provide current registry keys to help LLM
                        keys = list(self.registry.keys())
                        result_content = f"Error: Missing artifact variables {missing_keys or requested_keys}. Current registry keys: {keys}. Ensure your script creates the variables and stores them in the registry."

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": fn_name,
                    "content": result_content
                })

        # Save and Prepare Final Response
        self._emit_progress(
            progress_callback,
            stage="artifact_generation",
            message="I am saving the registered analysis result and collecting its output metadata.",
        )
        output_dataset_paths, output_dataset_size, saved_artifacts = self._save_result(user_query)
        output_dataset_path = output_dataset_paths[0] if output_dataset_paths else None
        duration_sec = time.time() - start_time

        if self.debug:
            # print(f"\n[Agent] Finished in {duration_sec:.2f}s")
            # print(f"[Agent] Total LLM Calls: {self.llm_calls}, Script Executions: {self.code_executions}")
            pass

        # Collect raw script history
        raw_script_parts = []
        for m in messages:
            if isinstance(m, dict) and m.get("tool_call_id"):
                continue
            if hasattr(m, "tool_calls") and m.tool_calls:
                for tc in m.tool_calls:
                    if tc.function.name == "execute_script":
                        raw_script_parts.append(json.loads(tc.function.arguments)['script'])
        raw_script = "\n# --- Step ---\n".join(raw_script_parts)

        # Optional: clean the final script
        cleaned_script = self._clean_final_script(raw_script, user_query) if raw_script else ""

        # --------------------------
        # Build the "complementary" dictionary
        # --------------------------
        complementary = {
            "Execution": {
                "Inputs": {
                    "text": user_query,
                    "dataset_path": dataset_path
                },
                "Outputs": {
                    "dataset_path": output_dataset_path,
                    "dataset_paths": output_dataset_paths,
                    "dataset_size": output_dataset_size
                }
            },
            "Provenance": {
                "Lineage": {},  # not collected in this version, left blank
                "Tool Calls": {
                    "execute_script_count": self.code_executions,
                    "search_knowledge_base_count": self.kb_searches,
                    "register_final_artifact_count": len(self.final_artifact_keys or ([self.final_artifact_key] if self.final_artifact_key else []))
                },
                "LLM Calls": {
                    "total": self.llm_calls
                }
            },
            "Artifacts and Logs": {
                "Inline Artifacts": {},  # not collected, left blank
                "Persisted Artifacts": {
                    "final_artifact_keys": self.final_artifact_keys or ([self.final_artifact_key] if self.final_artifact_key else []),
                    "paths": output_dataset_paths,
                    "items": saved_artifacts
                }
            }
        }

        return {
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "model": self.model,
            "duration": f"{duration_sec:.2f}s",
            "total_input_tokens": self.input_tokens,
            "toatal_output_tokens": self.output_tokens,
            "inputs": {
                "text": user_query,
                "dataset_path": dataset_path
            },
            "outputs": {
                "text": final_text_response,
                "dataset_path": output_dataset_path,
                "dataset_paths": output_dataset_paths,
                "dataset_size": output_dataset_size
            },
            "metrics": {
                "llm_calls": self.llm_calls,
                "tool_calls": self.code_executions + self.kb_searches,
                "number_of_artifacts": self.number_of_artifacts
            },
            "environment": self._environment_info(),
            "script": cleaned_script,
            "complementary": complementary
        }
