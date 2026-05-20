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
import shutil
from typing import Any, Dict, List, Optional, Tuple, Union
import pandas as pd
import geopandas as gpd
import numpy as np
try:
    import rasterio
    import rasterio.features
    import rasterio.mask
    import rasterio.transform
    from rasterio.io import MemoryFile
except ImportError:
    rasterio = None
try:
    import rioxarray
except ImportError:
    rioxarray = None
try:
    from osgeo import gdal
except ImportError:
    gdal = None

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
from gas_server.core.config import PROJECT_ROOT, ensure_runtime_dirs

ensure_runtime_dirs()

BASE_DIR = str(PROJECT_ROOT)

class RasterAgent(GeoAgent):
    """
    An adaptive, code-centric Raster analysis agent for both vector and raster data.
    It uses Python execution as its primary tool for loading, inspection, and analysis.
    Supports GeoDataFrames, DataFrames, and rasterio-based raster datasets.
    Persistent registry maintains state across tool calls.
    """

    agent_id = "raster_agent"
    agent_name = "Raster Agent"
    agent_version = "3.0.0"
    agent_description = "Runs raster and mixed raster-vector geospatial analysis workflows."
    requires_input_datasets = True

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str | None = None,
        debug: bool = True,
    ):
        if OpenAI is None:
            raise ImportError("Please install the 'openai' package.")

        super().__init__(api_key=api_key, model=model or "gpt-4o-2024-05-13")
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

        self._setup_system_prompt()
        self._define_tools()

    def _setup_system_prompt(self):
        self.system_prompt = {
            "role": "system",
            "content": (
                "You are a Senior Geospatial Systems Architect and Data Analyst specializing in both vector and raster analysis.\n"
                "Your objective is to solve spatial tasks (vector, raster, or mixed) by writing and executing Python code.\n\n"

                "SUPPORTED DATA TYPES:\n"
                "- VECTOR: GeoDataFrames, shapefiles, GeoJSON (via geopandas)\n"
                "- RASTER: GeoTIFFs, raster arrays (via rasterio, rioxarray)\n"
                "- TABULAR: DataFrames (via pandas)\n"
                "- MIXED: Combined vector-raster workflows\n\n"

                "IMPORTANT – PERSISTENT STATE & REUSE:\n"
                "- The environment has a `registry` dictionary that persists across all `execute_script` calls.\n"
                "- Store GeoDataFrames, DataFrames, raster datasets, or numpy arrays in registry for reuse.\n"
                "- **Do NOT re‑import libraries** – `geopandas as gpd`, `pandas as pd`, `numpy as np`, `rasterio`, `rioxarray` are pre-imported.\n"
                "- **Do NOT re‑read files** if the data is already in `registry`. Check `registry.keys()` first.\n"
                "- Use `registry['varname']` to retrieve previously loaded data.\n"
                "- NEVER save the outputs to .pkl files. Instead use .gpkg for vector data by default or .tif for raster data.\n"
                "- The helper function `list_registry()` returns a readable summary of all cached objects.\n\n"
                "- Prefer the injected toolkit helpers over hand-written file/path logic: "
                "`load_input`, `load_vector`, `load_raster`, `rasterize_vector`, "
                "`clip_raster_with_vector`, `register_georaster`, and `list_registry`.\n\n"


                "OUTPUT FORMATS:\n"
                "- For VECTOR results: Save as .gpkg by default and always keep geometry columns. Use GeoJSON only if the user explicitly requests it.\n"
                "- For RASTER results: Save as .tif with proper CRS, transform, and nodata. "
                "For generated raster arrays, store a dictionary in registry like "
                "`registry['result'] = {'array': raster_array, 'profile': raster_profile}` so the final GeoTIFF preserves georeferencing.\n"
                "- For TABULAR results: Save as .csv if non-spatial or mixed output.\n\n"

                "ADAPTIVE BEHAVIOR:\n"
                "1. DATA TYPE DETECTION: Auto-detect if files are raster (.tif, .jp2, .vrt) or vector (.shp, .geojson, .geoparquet).\n"
                "2. DYNAMIC LOADING: Prefer `load_input(index)` for user-provided datasets. It loads rasters, vectors, and tables from the prepared service paths.\n"
                "3. ADAPTIVE INSPECTION: Check CRS, shape/bands (raster), columns/geometry (vector), dtypes, and validity.\n"
                "4. RUNTIME MEMORY: Maintain mental log of what you've learned. Adapt plan if errors occur.\n"
                "5. CRS HANDLING: Align CRS when combining raster and vector. Reproject if necessary. For example, when combining a raster with a vector layer, ensure they are in the same coordinate reference system.\n"
                "6. RASTER OPERATIONS: Use rasterio for band math, resampling, clipping. Use numpy for computations.\n"
                "7. VECTOR-TO-RASTER: Use rasterio.features.rasterize() for vector-to-raster conversions.\n\n"

                "FINALIZING:\n"
                "- When task is complete, call `register_final_artifact` with the variable name of the result, or provide `variable_names` if you need to save several final artifacts.\n"
                "- Result can be GeoDataFrame, raster data, a georeferenced raster dictionary containing `array` and `profile`, an existing output file path, or a list/tuple containing several final artifacts already stored in `registry`.\n\n"

                "EFFICIENCY:\n"
                "- Before loading, check `registry.keys()` to see if data is cached.\n"
                "- Reuse cached objects to avoid duplicate I/O and computation.\n\n"

                """OUTPUT SAFETY RULES (STRICT):
                - NEVER include file paths, directory paths, or system locations in the final response text.
                - This includes (but is not limited to):
                - Absolute paths (e.g., /home/user/data/file.tif, C:\\data\\file.tif)
                - Relative paths (e.g., ./data/file.tif, ../outputs/result.geojson)
                - Generated output paths
                - The final response must be fully interpretable without exposing any filesystem details."""

            )
        }

    def _define_tools(self):
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": "execute_script",
                    "description": "Execute a Python script. Use this for loading files, inspecting data, and performing analysis. "
                                   "The environment already has `gpd`, `pd`, `np` imported, a persistent `registry` dict, "
                                   "and safe toolkit helpers including `load_input`, `load_vector`, `load_raster`, "
                                   "`rasterize_vector`, `clip_raster_with_vector`, and `register_georaster`. "
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
try:
    import rasterio
    import rasterio.features
    import rasterio.mask
    import rasterio.transform
    from rasterio.io import MemoryFile
except ImportError:
    rasterio = None
try:
    import rioxarray
except ImportError:
    rioxarray = None

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
    return info

# Helper to inspect raster data
def inspect_raster(src, name="raster"):
    #Inspect rasterio DatasetReader object.
    info = {
        "name": name,
        "type": "rasterio.DatasetReader",
        "width": src.width,
        "height": src.height,
        "count": src.count,
        "dtype": str(src.dtypes[0]) if src.dtypes else None,
        "crs": str(src.crs),
        "bounds": list(src.bounds),
        "transform": str(src.transform),
        "nodata": src.nodata
    }
    if src.count == 1:
        band = src.read(1)
        info["stats"] = {
            "min": float(np.nanmin(band[band != src.nodata])) if src.nodata is not None else float(np.nanmin(band)),
            "max": float(np.nanmax(band[band != src.nodata])) if src.nodata is not None else float(np.nanmax(band)),
            "mean": float(np.nanmean(band[band != src.nodata])) if src.nodata is not None else float(np.nanmean(band))
        }
    return info

# Helper to list current registry contents
def list_registry():
    #List all objects in registry with their types and sizes
    summary = {}
    for key, obj in registry.items():
        if isinstance(obj, gpd.GeoDataFrame):
            summary[key] = f"GeoDataFrame, {len(obj)} rows, crs={obj.crs}"
        elif isinstance(obj, pd.DataFrame):
            summary[key] = f"DataFrame, {len(obj)} rows"
        elif isinstance(obj, np.ndarray):
            summary[key] = f"numpy.ndarray, shape={obj.shape}, dtype={obj.dtype}"
        elif isinstance(obj, dict) and ("array" in obj or "data" in obj) and ("profile" in obj or "meta" in obj):
            arr = obj.get("array", obj.get("data"))
            profile = obj.get("profile", obj.get("meta", {}))
            shape = getattr(arr, "shape", None)
            summary[key] = f"georeferenced raster dict, shape={shape}, crs={profile.get('crs')}"
        elif hasattr(obj, 'shape') and hasattr(obj, 'crs'):  # rasterio-like
            summary[key] = f"raster, shape=({obj.height}x{obj.width}x{obj.count}), crs={obj.crs}"
        else:
            summary[key] = f"{type(obj).__name__}"
    return summary

def _input_path(index=0):
    paths = registry.get("input_paths", [])
    if index < 0 or index >= len(paths):
        raise IndexError(f"Input dataset index {index} is out of range. Available inputs: {len(paths)}")
    return paths[index]

def load_vector(path_or_index=0, key=None):
    path = _input_path(path_or_index) if isinstance(path_or_index, int) else path_or_index
    cache_key = key or f"input_{path_or_index}_vector" if isinstance(path_or_index, int) else key
    if cache_key and cache_key in registry:
        return registry[cache_key]
    gdf = gpd.read_file(path)
    if cache_key:
        registry[cache_key] = gdf
    return gdf

def load_raster(path_or_index=0, key=None):
    if rasterio is None:
        raise ImportError("rasterio is required to load raster datasets")
    path = _input_path(path_or_index) if isinstance(path_or_index, int) else path_or_index
    cache_key = key or f"input_{path_or_index}_raster" if isinstance(path_or_index, int) else key
    if cache_key and cache_key in registry:
        return registry[cache_key]
    src = rasterio.open(path)
    if cache_key:
        registry[cache_key] = src
    return src

def load_input(index=0, key=None):
    path = _input_path(index)
    lower = str(path).lower()
    if lower.endswith((".tif", ".tiff", ".vrt", ".jp2")):
        return load_raster(index, key=key)
    if lower.endswith((".csv", ".tsv")):
        df = pd.read_csv(path, sep="\\t" if lower.endswith(".tsv") else ",")
        registry[key or f"input_{index}_table"] = df
        return df
    return load_vector(index, key=key)

def register_georaster(key, array, profile):
    if not isinstance(profile, dict):
        profile = dict(profile)
    if not profile.get("crs") or not profile.get("transform"):
        raise ValueError("A georeferenced raster profile must include CRS and affine transform.")
    registry[key] = {"array": np.asarray(array), "profile": profile}
    return registry[key]

def rasterize_vector(gdf, value_column, resolution, key="raster_result", nodata=-9999.0, dtype="float32", all_touched=False):
    if rasterio is None:
        raise ImportError("rasterio is required for rasterization")
    if gdf.crs is None:
        raise ValueError("Cannot rasterize vector data without a CRS.")
    if getattr(gdf.crs, "is_geographic", False):
        raise ValueError("Resolution is in linear units, but the vector CRS is geographic. Project the vector data first.")
    values = pd.to_numeric(gdf[value_column], errors="coerce")
    valid = values.notna() & gdf.geometry.notna() & ~gdf.geometry.is_empty
    clean = gdf.loc[valid].copy()
    values = values.loc[valid]
    if clean.empty:
        raise ValueError(f"No valid geometries and numeric values found for '{value_column}'.")
    minx, miny, maxx, maxy = clean.total_bounds
    width = int(np.ceil((maxx - minx) / float(resolution)))
    height = int(np.ceil((maxy - miny) / float(resolution)))
    if width <= 0 or height <= 0:
        raise ValueError("Raster dimensions are not positive.")
    transform = rasterio.transform.from_origin(minx, maxy, float(resolution), float(resolution))
    shapes = ((geom, float(value)) for geom, value in zip(clean.geometry, values))
    array = rasterio.features.rasterize(
        shapes,
        out_shape=(height, width),
        transform=transform,
        fill=nodata,
        dtype=dtype,
        all_touched=all_touched,
    )
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 1,
        "dtype": dtype,
        "crs": clean.crs,
        "transform": transform,
        "nodata": nodata,
        "compress": "deflate",
    }
    return register_georaster(key, array, profile)

def clip_raster_with_vector(raster_obj, vector_gdf, key="clipped_raster", crop=True, nodata=None):
    if rasterio is None:
        raise ImportError("rasterio is required for raster clipping")
    src = raster_obj
    clip_gdf = vector_gdf
    if clip_gdf.crs and src.crs and str(clip_gdf.crs) != str(src.crs):
        clip_gdf = clip_gdf.to_crs(src.crs)
    fill_value = src.nodata if nodata is None else nodata
    clipped, transform = rasterio.mask.mask(
        src,
        [geom for geom in clip_gdf.geometry if geom is not None and not geom.is_empty],
        crop=crop,
        nodata=fill_value,
    )
    profile = src.profile.copy()
    profile.update({
        "height": clipped.shape[1],
        "width": clipped.shape[2],
        "transform": transform,
        "nodata": fill_value,
    })
    return register_georaster(key, clipped, profile)

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
            exec(full_code, exec_locals, exec_locals)
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
            elif isinstance(obj, dict) and ("array" in obj or "data" in obj) and ("profile" in obj or "meta" in obj):
                arr = obj.get("array", obj.get("data"))
                profile = obj.get("profile", obj.get("meta", {}))
                registry_summary.append(
                    f"  {key}: georeferenced raster dict, shape={getattr(arr, 'shape', None)}, crs={profile.get('crs')}"
                )
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
    # Persistence & Utilities
    # --------------------------

    def _environment_info(self) -> Dict[str, Any]:
        available_libs = ["geopandas", "pandas", "numpy", "shapely"]
        if rasterio:
            available_libs.append("rasterio")
        if rioxarray:
            available_libs.append("rioxarray")
        if gdal:
            available_libs.append("gdal")
        return {
            "python_version": platform.python_version(),
            "domain-specific libraries": available_libs,
            "supports_raster": rasterio is not None,
            "supports_vector": gpd is not None
        }

    def _raster_output_requested(self, task: str) -> bool:
        request = (task or "").lower()
        raster_terms = (
            "raster",
            "rasterize",
            "geotiff",
            "geo tiff",
            ".tif",
            ".tiff",
            "pixel",
            "cell size",
            "dem",
            "elevation",
        )
        return any(term in request for term in raster_terms)

    def _failure_response(
        self,
        *,
        user_query: str,
        dataset_path: List[str],
        start_time: float,
        message: str,
        script: str = "",
        output_dataset_paths: Optional[List[str]] = None,
        output_dataset_size: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        output_dataset_paths = output_dataset_paths or []
        output_dataset_path = output_dataset_paths[0] if output_dataset_paths else None
        output_dataset_size = output_dataset_size or {
            "type": None,
            "dimensions": None,
            "feature_count": None,
        }
        duration_sec = time.time() - start_time
        return {
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "model": self.model,
            "duration": f"{duration_sec:.2f}s",
            "total_input_tokens": self.input_tokens,
            "total_output_tokens": self.output_tokens,
            "inputs": {
                "text": user_query,
                "dataset_path": dataset_path,
            },
            "outputs": {
                "text": f"Status: failed. Error: {message}",
                "dataset_path": output_dataset_path,
                "dataset_paths": output_dataset_paths,
                "dataset_size": output_dataset_size,
            },
            "metrics": {
                "llm_calls": self.llm_calls,
                "tool_calls": self.code_executions,
                "number_of_artifacts": len(output_dataset_paths),
            },
            "environment": self._environment_info(),
            "script": script,
            "complementary": {
                "Execution": {
                    "Inputs": {
                        "text": user_query,
                        "dataset_path": dataset_path,
                    },
                    "Outputs": {
                        "dataset_path": output_dataset_path,
                        "dataset_paths": output_dataset_paths,
                        "dataset_size": output_dataset_size,
                    },
                },
                "Provenance": {
                    "Lineage": {},
                    "Tool Calls": {
                        "execute_script_count": self.code_executions,
                        "register_final_artifact_count": len(
                            self.final_artifact_keys or ([self.final_artifact_key] if self.final_artifact_key else [])
                        ),
                    },
                    "LLM Calls": {
                        "total": self.llm_calls,
                    },
                },
                "Artifacts and Logs": {
                    "Inline Artifacts": {},
                    "Persisted Artifacts": {
                        "final_artifact_keys": self.final_artifact_keys or ([self.final_artifact_key] if self.final_artifact_key else []),
                        "paths": output_dataset_paths,
                        "items": [],
                    },
                },
                "Assumptions and Limitations": {
                    "warnings": [message],
                    "limitations": [],
                    "assumptions": [],
                },
            },
        }

    def _generate_filename(self, task: str) -> str:
        return build_output_filename(
            task,
            extension="",
            fallback="analysis_result",
        )

    def _preferred_vector_output(self, task: str) -> Tuple[str, str, str]:
        request = (task or "").lower()
        if "geojson" in request and not any(term in request for term in ("geopackage", "gpkg", ".gpkg")):
            return ".geojson", "GeoJSON", "GeoJSON"
        return ".gpkg", "GPKG", "GeoPackage"

    def _artifact_output_dir(self) -> str:
        out_dir = os.path.join(BASE_DIR, "Data")
        os.makedirs(out_dir, exist_ok=True)
        return out_dir

    def _write_array_with_profile(self, array: Any, profile: Dict[str, Any], path: str) -> Dict[str, Any]:
        if not rasterio:
            raise ImportError("rasterio is required to save raster arrays as GeoTIFF")

        arr = np.asarray(array)
        if arr.ndim not in (2, 3):
            raise ValueError(f"Raster arrays must be 2D or 3D. Received shape {arr.shape}.")

        profile = dict(profile or {})
        if not profile.get("crs") or not profile.get("transform"):
            raise ValueError("Georeferenced raster artifacts must include both CRS and affine transform.")

        if arr.ndim == 2:
            height, width = arr.shape
            count = 1
        else:
            if arr.shape[0] <= 16:
                count, height, width = arr.shape
            else:
                height, width, count = arr.shape
                arr = np.moveaxis(arr, -1, 0)

        profile.update(
            {
                "driver": profile.get("driver", "GTiff"),
                "height": height,
                "width": width,
                "count": count,
                "dtype": str(arr.dtype),
            }
        )

        with rasterio.open(path, "w", **profile) as dst:
            if count == 1:
                dst.write(arr, 1)
            else:
                dst.write(arr)

        return {
            "type": "raster",
            "dimensions": [height, width, count],
            "crs": str(profile.get("crs")),
            "dtype": str(arr.dtype),
            "nodata": profile.get("nodata"),
        }

    def _save_existing_artifact_file(self, source_path: str, output_path: str) -> Dict[str, Any]:
        if not os.path.exists(source_path):
            raise FileNotFoundError(f"Registered artifact path does not exist: {source_path}")

        shutil.copy2(source_path, output_path)
        metadata: Dict[str, Any] = {
            "type": "file",
            "dimensions": None,
            "feature_count": None,
        }
        if rasterio and output_path.lower().endswith((".tif", ".tiff")):
            with rasterio.open(output_path) as src:
                metadata = {
                    "type": "raster",
                    "dimensions": [src.height, src.width, src.count],
                    "crs": str(src.crs) if src.crs else None,
                    "dtype": str(src.dtypes[0]) if src.dtypes else None,
                    "nodata": src.nodata,
                }
        elif output_path.lower().endswith((".geojson", ".gpkg", ".shp")):
            gdf = gpd.read_file(output_path)
            metadata = {
                "type": "vector",
                "dimensions": list(gdf.shape),
                "feature_count": len(gdf),
                "crs": str(gdf.crs) if gdf.crs else None,
            }
        return metadata

    def _record_artifact_warning(self, message: str) -> None:
        self.runtime_memory.setdefault("warnings", []).append(message)

    @staticmethod
    def _path_from_artifact_dict(obj: Dict[str, Any]) -> str | None:
        for key in ("path", "file_path", "filepath", "output_path", "artifact_path"):
            value = obj.get(key)
            if isinstance(value, (str, os.PathLike)):
                return os.fspath(value)
        return None

    def _extract_requested_resolution(self, task: str) -> float | None:
        match = re.search(
            r"(\d+(?:\.\d+)?)\s*-?\s*(meters?|metres?|m|kilometers?|kilometres?|km|feet|foot|ft)\b",
            task or "",
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        value = float(match.group(1))
        unit = match.group(2).lower()
        if unit in {"kilometer", "kilometers", "kilometre", "kilometres", "km"}:
            return value * 1000.0
        if unit in {"feet", "foot", "ft"}:
            return value * 0.3048
        if unit in {"meter", "meters", "metre", "metres", "m"}:
            return value
        return None

    def _is_direct_vector_rasterization_request(self, task: str) -> bool:
        task_lower = (task or "").lower()
        return any(term in task_lower for term in ("rasterize", "geotiff", "geo tiff", "raster"))

    def _extract_quoted_field(self, task: str, columns: List[str]) -> str | None:
        quoted_terms = re.findall(r"['\"]([^'\"]+)['\"]", task or "")
        column_map = {str(column).lower(): str(column) for column in columns}
        for term in quoted_terms:
            if term.lower() in column_map:
                return column_map[term.lower()]
        return None

    def _select_raster_value_column(self, gdf: gpd.GeoDataFrame, task: str) -> str | None:
        columns = [str(column) for column in gdf.columns if column != gdf.geometry.name]
        quoted = self._extract_quoted_field(task, columns)
        if quoted:
            return quoted

        task_lower = (task or "").lower()
        preferred_terms: List[str] = []
        if "density" in task_lower:
            preferred_terms.extend(["population_density", "density"])
        if "population" in task_lower:
            preferred_terms.extend(["population", "pop", "b01001_001e"])
        if "value" in task_lower:
            preferred_terms.append("value")

        candidates: List[Tuple[int, str]] = []
        for column in columns:
            values = pd.to_numeric(gdf[column], errors="coerce")
            valid_count = int(values.notna().sum())
            unique_count = int(values.dropna().nunique())
            if valid_count == 0:
                continue
            column_lower = column.lower()
            score = valid_count + min(unique_count, 1000)
            if any(term in column_lower for term in preferred_terms):
                score += 100000
            if any(term in column_lower for term in ("id", "geoid", "fips", "fp", "year", "code")):
                score -= 50000
            candidates.append((score, column))

        if not candidates:
            return None
        candidates.sort(reverse=True)
        return candidates[0][1]

    def _filter_vector_for_request(self, gdf: gpd.GeoDataFrame, task: str) -> gpd.GeoDataFrame:
        task_lower = (task or "").lower()
        if re.search(r"\b(pa|pennsylvania)\b", task_lower):
            for column in ("STUSPS", "state", "state_abbr"):
                if column in gdf.columns:
                    filtered = gdf[gdf[column].astype(str).str.upper() == "PA"].copy()
                    if not filtered.empty:
                        return filtered
            for column in ("STATE_NAME", "state_name"):
                if column in gdf.columns:
                    filtered = gdf[gdf[column].astype(str).str.lower() == "pennsylvania"].copy()
                    if not filtered.empty:
                        return filtered
            if "STATEFP" in gdf.columns:
                filtered = gdf[gdf["STATEFP"].astype(str).str.zfill(2) == "42"].copy()
                if not filtered.empty:
                    return filtered
        return gdf

    def _try_direct_vector_rasterization(self, task: str, dataset_paths: List[str]) -> bool:
        if not rasterio or not rasterio.features:
            return False
        if not self._is_direct_vector_rasterization_request(task):
            return False
        resolution = self._extract_requested_resolution(task)
        if not resolution or resolution <= 0:
            return False

        for dataset_path in dataset_paths:
            try:
                gdf = gpd.read_file(dataset_path)
            except Exception:
                continue
            if not isinstance(gdf, gpd.GeoDataFrame) or gdf.empty or gdf.geometry.isna().all():
                continue

            gdf = self._filter_vector_for_request(gdf, task)
            gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
            if gdf.empty:
                continue
            if gdf.crs is None:
                raise ValueError("Cannot rasterize vector data without a CRS. Project or define the CRS first.")
            try:
                if gdf.crs.is_geographic:
                    raise ValueError(
                        "Cannot apply a meter resolution to geographic coordinates. Project the data first."
                    )
            except AttributeError:
                pass

            value_column = self._select_raster_value_column(gdf, task)
            if not value_column:
                continue

            values = pd.to_numeric(gdf[value_column], errors="coerce")
            valid = values.notna()
            gdf = gdf.loc[valid].copy()
            values = values.loc[valid]
            if gdf.empty:
                continue

            minx, miny, maxx, maxy = gdf.total_bounds
            width = int(math.ceil((maxx - minx) / resolution))
            height = int(math.ceil((maxy - miny) / resolution))
            if width <= 0 or height <= 0:
                continue
            if width * height > 100_000_000:
                raise ValueError(
                    f"Requested raster would contain {width * height:,} pixels. "
                    "Use a coarser resolution or smaller area."
                )

            transform = rasterio.transform.from_origin(minx, maxy, resolution, resolution)
            nodata = -9999.0
            shapes = ((geom, float(value)) for geom, value in zip(gdf.geometry, values))
            array = rasterio.features.rasterize(
                shapes,
                out_shape=(height, width),
                transform=transform,
                fill=nodata,
                dtype="float32",
            )
            profile = {
                "driver": "GTiff",
                "height": height,
                "width": width,
                "count": 1,
                "dtype": "float32",
                "crs": gdf.crs,
                "transform": transform,
                "nodata": nodata,
                "compress": "deflate",
            }
            self.registry["direct_raster"] = {"array": array, "profile": profile}
            self.final_artifact_keys = ["direct_raster"]
            self.final_artifact_key = "direct_raster"
            self.runtime_memory["facts"].append(
                f"Directly rasterized {len(gdf)} features using {value_column} at {resolution:g} meter resolution."
            )
            return True

        return False

    ##
    def _save_result(self, task: str) -> Tuple[List[str], Dict[str, Any], List[Dict[str, Any]]]:
        registered_keys = self.final_artifact_keys or ([self.final_artifact_key] if self.final_artifact_key else [])
        if not registered_keys:
            return [], {
                "type": None,
                "dimensions": None,
                "feature_count": None
            }, []

        out_dir = self._artifact_output_dir()
        base_name = self._generate_filename(task)
        saved_paths: List[str] = []
        saved_artifacts: List[Dict[str, Any]] = []

        for key_index, key in enumerate(registered_keys, start=1):
            if key not in self.registry:
                raise KeyError(f"Registered artifact '{key}' was not found in the registry.")
            value = self.registry[key]
            items = list(value) if isinstance(value, (list, tuple)) else [value]
            allow_skip_unsupported = len(registered_keys) > 1 or len(items) > 1
            for item_index, obj in enumerate(items, start=1):
                suffix = f"_{key_index}"
                if len(items) > 1:
                    suffix += f"_{item_index}"

                if isinstance(obj, gpd.GeoDataFrame):
                    ext, driver, _ = self._preferred_vector_output(task)
                    path = os.path.join(out_dir, f"{base_name}{suffix}{ext}")
                    obj.to_file(path, driver=driver)
                    metadata = {
                        "type": "vector",
                        "dimensions": list(obj.shape),
                        "feature_count": len(obj),
                        "crs": str(obj.crs) if obj.crs else None
                    }
                elif rasterio and isinstance(obj, rasterio.io.DatasetReader):
                    ext = ".tif"
                    path = os.path.join(out_dir, f"{base_name}{suffix}{ext}")
                    with rasterio.open(path, "w", **obj.profile) as dst:
                        for band_index in range(1, obj.count + 1):
                            dst.write(obj.read(band_index), band_index)
                    metadata = {
                        "type": "raster",
                        "dimensions": [obj.height, obj.width, obj.count],
                        "crs": str(obj.crs),
                        "dtype": str(obj.dtypes[0]) if obj.dtypes else None
                    }
                elif rioxarray and hasattr(obj, "rio"):
                    ext = ".tif"
                    path = os.path.join(out_dir, f"{base_name}{suffix}{ext}")
                    obj.rio.to_raster(path)
                    metadata = {
                        "type": "raster",
                        "dimensions": list(obj.shape),
                        "crs": str(obj.rio.crs),
                        "dtype": str(obj.dtype)
                    }
                elif isinstance(obj, np.ndarray):
                    if not rasterio:
                        raise ImportError("rasterio is required to save raster arrays as GeoTIFF")
                    ext = ".tif"
                    path = os.path.join(out_dir, f"{base_name}{suffix}{ext}")
                    profile = self.registry.get(f"{key}_profile") or self.registry.get(f"{key}_meta")
                    if isinstance(profile, dict) and profile.get("crs") and profile.get("transform"):
                        metadata = self._write_array_with_profile(obj, profile, path)
                        if self.debug:
                            logging.info("Saving final result to: %s", path)
                        saved_paths.append(path)
                        saved_artifacts.append({"key": key, "path": path, "metadata": metadata})
                        continue
                    raise ValueError(
                        f"Registered raster array '{key}' is missing georeferencing metadata. "
                        f"Store a matching registry['{key}_profile'] or register a dictionary with "
                        "'array' and 'profile' keys."
                    )
                elif isinstance(obj, dict):
                    if ("array" in obj or "data" in obj) and ("profile" in obj or "meta" in obj):
                        ext = ".tif"
                        path = os.path.join(out_dir, f"{base_name}{suffix}{ext}")
                        metadata = self._write_array_with_profile(
                            obj.get("array", obj.get("data")),
                            obj.get("profile", obj.get("meta", {})),
                            path,
                        )
                    else:
                        source_path = self._path_from_artifact_dict(obj)
                        if source_path:
                            _, source_ext = os.path.splitext(source_path)
                            ext = source_ext or ".dat"
                            path = os.path.join(out_dir, f"{base_name}{suffix}{ext}")
                            metadata = self._save_existing_artifact_file(source_path, path)
                        elif allow_skip_unsupported:
                            self._record_artifact_warning(
                                f"Skipped registered artifact '{key}' because it is an inspection/metadata dictionary, not a saveable raster or vector artifact."
                            )
                            continue
                        else:
                            raise TypeError(
                                f"Unsupported final artifact type: {type(obj)}. "
                                "Dictionary artifacts must contain array/profile, data/meta, or a file path."
                            )
                elif isinstance(obj, (str, os.PathLike)):
                    source_path = os.fspath(obj)
                    _, source_ext = os.path.splitext(source_path)
                    ext = source_ext or ".dat"
                    path = os.path.join(out_dir, f"{base_name}{suffix}{ext}")
                    metadata = self._save_existing_artifact_file(source_path, path)
                elif isinstance(obj, pd.DataFrame):
                    raise TypeError(
                        "DataFrame is not a valid final artifact. Convert to GeoDataFrame or raster before finalizing."
                    )
                else:
                    raise TypeError(
                        f"Unsupported final artifact type: {type(obj)}. "
                        "Only GeoDataFrame or raster formats are allowed."
                    )

                if self.debug:
                    logging.info("Saving final result to: %s", path)
                saved_paths.append(path)
                saved_artifacts.append({"key": key, "path": path, "metadata": metadata})

        self.number_of_artifacts = len(saved_paths)
        if registered_keys and not saved_paths:
            raise TypeError(
                "No saveable final artifacts were registered. Register a GeoDataFrame, raster dataset, "
                "georeferenced raster dictionary, or existing artifact file path."
            )
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
            message=f"I will inspect the raster or mixed raster-vector inputs, run code-driven processing, and save a final artifact from {len(dataset_path)} dataset reference(s).",
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
        self.number_of_artifacts = 0
        self.final_artifact_key = None
        self.final_artifact_keys = []
        self.input_tokens = 0
        self.output_tokens = 0
        self.registry.clear()  # start fresh
        self.registry["input_paths"] = dataset_path
        for index, path in enumerate(dataset_path):
            self.registry[f"input_{index}_path"] = path

        if self._raster_output_requested(user_query) and rasterio is None:
            message = (
                "rasterio is required for raster output generation in this runtime. "
                "Install rasterio in the GAS server environment, then restart the server."
            )
            self.runtime_memory["errors"].append({"stage": "dependency_check", "error": message})
            self._emit_progress(
                progress_callback,
                stage="error",
                message=message,
                data={"missing_dependency": "rasterio"},
            )
            return self._failure_response(
                user_query=user_query,
                dataset_path=dataset_path,
                start_time=start_time,
                message=message,
            )

        self.runtime_memory["plan_status"] = "Starting task. Identifying initial loading steps."
        final_text_response = ""

        try:
            direct_rasterized = self._try_direct_vector_rasterization(user_query, dataset_path)
        except Exception as e:
            self.runtime_memory["errors"].append({"stage": "direct_vector_rasterization", "error": str(e)})
            if self._is_direct_vector_rasterization_request(user_query):
                self._emit_progress(
                    progress_callback,
                    stage="error",
                    message=(
                        "The direct vector-to-raster path could not complete, and this request is explicitly "
                        f"a rasterization task: {e}"
                    ),
                )
                duration_sec = time.time() - start_time
                return {
                    "agent_name": self.agent_name,
                    "agent_version": self.agent_version,
                    "model": self.model,
                    "duration": f"{duration_sec:.2f}s",
                    "total_input_tokens": self.input_tokens,
                    "total_output_tokens": self.output_tokens,
                    "inputs": {
                        "text": user_query,
                        "dataset_path": dataset_path,
                    },
                    "outputs": {
                        "text": f"Status: failed. Error: {e}",
                        "dataset_path": None,
                        "dataset_paths": [],
                        "dataset_size": {
                            "type": None,
                            "dimensions": None,
                            "feature_count": None,
                        },
                    },
                    "metrics": {
                        "llm_calls": self.llm_calls,
                        "tool_calls": self.code_executions,
                        "number_of_artifacts": 0,
                    },
                    "environment": self._environment_info(),
                    "script": "",
                    "complementary": {
                        "Execution": {
                            "Inputs": {
                                "text": user_query,
                                "dataset_path": dataset_path,
                            },
                            "Outputs": {
                                "dataset_path": None,
                                "dataset_paths": [],
                                "dataset_size": {
                                    "type": None,
                                    "dimensions": None,
                                    "feature_count": None,
                                },
                            },
                        },
                        "Provenance": {
                            "Lineage": {},
                            "Tool Calls": {
                                "execute_script_count": self.code_executions,
                                "register_final_artifact_count": 0,
                            },
                            "LLM Calls": {
                                "total": self.llm_calls,
                            },
                        },
                        "Artifacts and Logs": {
                            "Inline Artifacts": {},
                            "Persisted Artifacts": {
                                "final_artifact_keys": [],
                                "paths": [],
                                "items": [],
                            },
                        },
                    },
                }
            direct_rasterized = False
            self._emit_progress(
                progress_callback,
                stage="warning",
                message=(
                    "The direct vector-to-raster path could not complete, so I will continue with "
                    f"the model-backed raster workflow: {e}"
                ),
            )

        if direct_rasterized:
            self._emit_progress(
                progress_callback,
                stage="fallback_complete",
                message=(
                    "I directly rasterized the vector dataset with a georeferenced profile, so I can "
                    "save the GeoTIFF without additional model-generated code."
                ),
            )
            final_text_response = (
                "Created a georeferenced GeoTIFF by rasterizing the requested vector attribute."
            )

        # Inject paths and initial registry state into first user message
        user_content = (
            f"Task: {user_query}\n"
            f"Files available: {dataset_path}\n\n"
            "The registry already contains `input_paths` and `input_0_path`, `input_1_path`, etc. "
            "Use `load_input(0)` or the other toolkit helpers instead of manually copying file paths."
        )
        messages = [self.system_prompt, {"role": "user", "content": user_content}]

        for turn in range(0 if direct_rasterized else 40):  # Max iterations
            self._emit_progress(
                progress_callback,
                stage="planning",
                message=f"I am planning the next raster processing step and deciding whether to execute code or register the final artifact. This is iteration {turn + 1}.",
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
                    message=f"The model call failed before raster processing could continue, so I will return the failure details: {e}",
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
                    message="The model finished its raster workflow loop and provided a final text response, so I will prepare the saved output artifact.",
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
                    "execute_script": "I will execute Python raster processing code in the sandbox to load, inspect, transform, or analyze the dataset.",
                    "register_final_artifact": "I will register the selected raster or vector result as the final artifact to save.",
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
                            message="The raster processing code did not run successfully, so I will use the error feedback to revise the next step.",
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
                            message="The raster processing code ran successfully, so I will use the resulting variables and registry state for the next decision.",
                        )
                        if self.debug:
                            # print(f"[Sandbox] Execution Successful. Captured output length: {len(res['stdout'])}")
                            pass
                        result_content = res["full_response"]  # includes stdout + registry
                        self.runtime_memory["facts"].append(f"Executed script for: {args.get('purpose', 'unknown purpose')}")

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
                            message="I could not find one or more requested final artifact variables, so I will ask the next step to create or register a valid raster/vector result.",
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
            message="I am saving the registered raster/vector result and collecting its output metadata.",
        )
        try:
            output_dataset_paths, output_dataset_size, saved_artifacts = self._save_result(user_query)
        except Exception as e:
            self.runtime_memory["errors"].append({"stage": "save_result", "error": str(e)})
            self._emit_progress(
                progress_callback,
                stage="error",
                message=f"I could not save the registered final artifact: {e}",
            )
            return self._failure_response(
                user_query=user_query,
                dataset_path=dataset_path,
                start_time=start_time,
                message=str(e),
            )
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
            "total_output_tokens": self.output_tokens,
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
                "tool_calls": self.code_executions,
                "number_of_artifacts": self.number_of_artifacts
            },
            "environment": self._environment_info(),
            "script": cleaned_script,
            "complementary": complementary
        }
