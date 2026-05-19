import os
import json
import re
import time
import random
import logging
import platform
from typing import Optional, Dict, Any, List
import requests
import geopandas as gpd
from openai import OpenAI
from dotenv import load_dotenv
from gas_server.core.file_naming import build_output_path
from gas_server.core.geo_agent import GeoAgent
from gas_server.core.llm_client import build_llm_client, format_service_name
from gas_server.core.config import DATA_DIR, PROJECT_ROOT, ensure_runtime_dirs

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logging.getLogger().setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("openai").setLevel(logging.ERROR)
load_dotenv()
ensure_runtime_dirs()

BASE_DIR = str(PROJECT_ROOT)

class PasdaAgent(GeoAgent):
    agent_id = "pasda_agent"
    agent_name = "PASDA Discovery Agent"
    agent_version = "1.0.0"
    agent_description = "Discovers PASDA layers and downloads selected GIS data."

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str | None = None,
    ):
        super().__init__(api_key=api_key, model=model or "gpt-4o-2024-05-13", output_dir=DATA_DIR / self.agent_id)
        self.service_name = format_service_name(self.agent_name)
        self.client = build_llm_client(
            service_name=self.service_name,
            openai_api_key=self.api_key,
        )

        self.downloaded: List[str] = []
        self.summary: Optional[str] = None
        self.feature_counts: Dict[str, int] = {}
        self.raster_dimensions: Dict[str, List[int]] = {}
        self._tool_result_cache: Dict[str, str] = {}
        self._tool_call_counts: Dict[str, int] = {}

        self.total_input_tokens = 0
        self.total_output_tokens = 0

        # New collectors for "complementary" field
        self.execution_inputs = {}
        self.execution_outputs = {}
        self.lineage_entries = []
        self.tool_calls_log = []
        self.llm_calls_log = []
        self.inline_artifacts = []
        self.persisted_artifacts = []

        self.system_prompt = {
            "role": "system",
            "content": (
                "You are an autonomous spatial data engineer working with PASDA "
                "(Pennsylvania Spatial Data Access).\n"
                "Your job is to find and download GIS datasets using the ArcGIS REST API.\n\n"
                "CRITICAL RULES FOR SEARCHING:\n"
                "1. PASDA organizes data by county or agency, for example 'Berks', 'Allegheny', or 'PennDOT'. "
                "Service names rarely contain specific data themes like 'land use' or 'parcels'.\n"
                "2. Use list_services with broad geographic or agency keywords only. "
                "Never use specific data themes as the list_services keyword.\n"
                "3. Use get_service_metadata on the broad services you found to inspect specific layers.\n"
                "4. Use inspect_layer_fields to check layer columns and geometry type.\n"
                "5. Use sample_layer_data to view actual attribute values. This is required before downloading.\n"
                "6. Compare multiple layers if needed. Search again if a layer is wrong.\n"
                "7. When confident, use download_data to get the layer.\n"
                "8. Always use summarize_findings before finishing.\n"
                "9. Do not inspect the same service or layer repeatedly. If a service/layer has already been inspected, move to sampling, downloading, or a different candidate.\n"
                "10. For obvious statewide requests such as hospitals, schools, roads, boundaries, or health facilities, prefer the most direct high-confidence PASDA source and avoid a long exploratory search.\n"
                "11. Keep your reasoning short and practical.\n"
            ),
        }

        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": "list_services",
                    "description": "Fetch a list of PASDA map services. Use a broad county or agency keyword.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "keyword": {"type": "string"}
                        },
                        "required": ["keyword"]
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_service_metadata",
                    "description": "Get layer IDs, names, and description for a PASDA service.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "service_name": {"type": "string"}
                        },
                        "required": ["service_name"]
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "inspect_layer_fields",
                    "description": "Get fields and geometry type for a layer.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "service_name": {"type": "string"},
                            "layer_id": {"type": "integer"}
                        },
                        "required": ["service_name", "layer_id"]
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sample_layer_data",
                    "description": "Query 5 sample records from a layer without geometry.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "service_name": {"type": "string"},
                            "layer_id": {"type": "integer"},
                            "where_clause": {"type": "string"}
                        },
                        "required": ["service_name", "layer_id", "where_clause"]
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "download_data",
                    "description": "Download a PASDA layer as GeoPackage by default. Pass a descriptive base filename only.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "service_name": {"type": "string"},
                            "layer_id": {"type": "integer"},
                            "output_filename": {"type": "string"}
                        },
                        "required": ["service_name", "layer_id", "output_filename"]
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "summarize_findings",
                    "description": "Save a short final summary of the findings and downloaded dataset.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "summary_text": {"type": "string"}
                        },
                        "required": ["summary_text"]
                    },
                },
            },
        ]

    def infer_required_roles(self, query: str) -> set:
        roles = set()
        return roles

    def _generate_output_path(self, user_query: str) -> str:
        request = (user_query or "").lower()
        extension = ".geojson" if "geojson" in request and not any(term in request for term in ("geopackage", "gpkg", ".gpkg")) else ".gpkg"
        return build_output_path(
            getattr(self, "output_dir", str(DATA_DIR / self.agent_id)),
            user_query,
            extension=extension,
            fallback="pasda",
        )

    def _environment_info(self) -> Dict[str, Any]:
        return {
            "python_version": platform.python_version(),
            "domain-specific libraries": [
                "requests",
                "geopandas",
                "openai"
            ]
        }

    def _empty_result(self, user_text: str, input_dataset_path: Optional[str] = None) -> Dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "model": "gpt-4o",
            "duration": None,
            "inputs": {
                "text": user_text,
                "dataset_path": input_dataset_path
            },
            "outputs": {
                "text": None,
                "dataset_path": None,
                "dataset_paths": [],
                "dataset_size": {
                    "type": None,
                    "dimensions": None,
                    "feature_count": None
                }
            },
            "metrics": {
                "llm_calls": 0,
                "tool_calls": 0,
                "number_of_artifacts": 0
            },
            "environment": self._environment_info(),
            "complementary": {
                "Execution": {
                    "Inputs": {},
                    "Outputs": {}
                },
                "Provenance": {
                    "Lineage": {},
                    "Tool Calls": {},
                    "LLM Calls": {}
                },
                "Artifacts and Logs": {
                    "Inline Artifacts": {},
                    "Persisted Artifacts": {}
                }
            }
        }

    def _final_result(
        self,
        user_text: str,
        input_dataset_path: Optional[str],
        duration_seconds: float
    ) -> Dict[str, Any]:
        dataset_path = self.downloaded[-1] if self.downloaded else None

        size_type = None
        dimensions = None
        feature_count = None

        if dataset_path:
            if dataset_path in self.feature_counts:
                size_type = "vector"
                feature_count = self.feature_counts.get(dataset_path)
            elif dataset_path in self.raster_dimensions:
                size_type = "raster"
                dimensions = self.raster_dimensions.get(dataset_path)

        # Build the complementary dictionary
        complementary = {
            "Execution": {
                "Inputs": self.execution_inputs,
                "Outputs": self.execution_outputs
            },
            "Provenance": {
                "Lineage": self.lineage_entries if self.lineage_entries else {},
                "Tool Calls": self.tool_calls_log if self.tool_calls_log else {},
                "LLM Calls": self.llm_calls_log if self.llm_calls_log else {}
            },
            "Artifacts and Logs": {
                "Inline Artifacts": self.inline_artifacts if self.inline_artifacts else {},
                "Persisted Artifacts": self.persisted_artifacts if self.persisted_artifacts else {}
            }
        }

        return {
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "model": "gpt-4o",
            "duration": f"{duration_seconds:.2f} seconds",
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "inputs": {
                "text": user_text,
                "dataset_path": input_dataset_path
            },
            "outputs": {
                "text": self.summary,
                "dataset_path": dataset_path,
                "dataset_paths": list(self.downloaded),
                "dataset_size": {
                    "type": size_type,
                    "dimensions": dimensions,
                    "feature_count": feature_count
                }
            },
            "metrics": {
                "llm_calls": self.llm_calls,
                "tool_calls": self.tool_calls,
                "number_of_artifacts": len(self.downloaded)
            },
            "environment": self._environment_info(),
            "script": None,
            "complementary": complementary
        }

    def list_services(self, keyword: str) -> str:
        cache_key = self._tool_cache_key("list_services", {"keyword": keyword})
        if cache_key in self._tool_result_cache:
            return self._cached_tool_result(cache_key)

        try:
            url = "https://mapservices.pasda.psu.edu/server/rest/services/pasda?f=json"
            r = requests.get(url, timeout=25)
            r.raise_for_status()

            services = [s["name"] for s in r.json().get("services", [])]
            keyword_lower = keyword.lower().strip()
            filtered = [s for s in services if keyword_lower in s.lower()]

            # Record lineage
            self.lineage_entries.append({
                "step": "list_services",
                "keyword": keyword,
                "url": url,
                "result_count": len(filtered)
            })

            result = json.dumps({
                "services": filtered,
                "total": len(filtered)
            })
            self._tool_result_cache[cache_key] = result
            self._tool_call_counts[cache_key] = 1
            return result
        except Exception as e:
            return json.dumps({
                "status": "error",
                "message": str(e)
            })

    def get_service_metadata(self, service_name: str) -> str:
        cache_key = self._tool_cache_key("get_service_metadata", {"service_name": service_name})
        if cache_key in self._tool_result_cache:
            return self._cached_tool_result(cache_key)

        try:
            url = f"https://mapservices.pasda.psu.edu/server/rest/services/{service_name}/MapServer?f=json"
            r = requests.get(url, timeout=25)
            r.raise_for_status()

            data = r.json()
            layers = [{"id": l["id"], "name": l["name"]} for l in data.get("layers", [])]
            description = data.get("description", "")

            # Record lineage
            self.lineage_entries.append({
                "step": "get_service_metadata",
                "service_name": service_name,
                "url": url,
                "layers_found": len(layers)
            })

            result = json.dumps({
                "layers": layers,
                "service_description": description[:500] if description else None
            })
            self._tool_result_cache[cache_key] = result
            self._tool_call_counts[cache_key] = 1
            return result
        except Exception as e:
            return json.dumps({
                "status": "error",
                "message": str(e)
            })

    def inspect_layer_fields(self, service_name: str, layer_id: int) -> str:
        cache_key = self._tool_cache_key("inspect_layer_fields", {"service_name": service_name, "layer_id": layer_id})
        if cache_key in self._tool_result_cache:
            return self._cached_tool_result(cache_key)

        try:
            url = f"https://mapservices.pasda.psu.edu/server/rest/services/{service_name}/MapServer/{layer_id}?f=json"
            r = requests.get(url, timeout=25)
            r.raise_for_status()

            data = r.json()
            fields = [
                {
                    "name": f["name"],
                    "type": f["type"],
                    "alias": f.get("alias", "")
                }
                for f in data.get("fields", [])
            ]
            geom_type = data.get("geometryType")

            # Record lineage
            self.lineage_entries.append({
                "step": "inspect_layer_fields",
                "service_name": service_name,
                "layer_id": layer_id,
                "geometry_type": geom_type,
                "field_count": len(fields)
            })

            result = json.dumps({
                "geometry_type": geom_type,
                "fields": fields
            })
            self._tool_result_cache[cache_key] = result
            self._tool_call_counts[cache_key] = 1
            return result
        except Exception as e:
            return json.dumps({
                "status": "error",
                "message": str(e)
            })

    def sample_layer_data(self, service_name: str, layer_id: int, where_clause: str) -> str:
        cache_key = self._tool_cache_key(
            "sample_layer_data",
            {"service_name": service_name, "layer_id": layer_id, "where_clause": where_clause or "1=1"},
        )
        if cache_key in self._tool_result_cache:
            return self._cached_tool_result(cache_key)

        try:
            url = f"https://mapservices.pasda.psu.edu/server/rest/services/{service_name}/MapServer/{layer_id}/query"
            params = {
                "where": where_clause,
                "outFields": "*",
                "returnGeometry": "false",
                "resultRecordCount": 5,
                "f": "json"
            }
            r = requests.get(url, params=params, timeout=25)
            r.raise_for_status()
            data = r.json()

            if "error" in data:
                return json.dumps({
                    "status": "error",
                    "message": data["error"].get("message")
                })

            features = [f.get("attributes", {}) for f in data.get("features", [])]

            # Store inline artifact (sample data)
            self.inline_artifacts.append({
                "type": "sample_layer_data",
                "service_name": service_name,
                "layer_id": layer_id,
                "where_clause": where_clause,
                "sample_records": features[:3]  # keep first 3 for brevity
            })

            # Record lineage
            self.lineage_entries.append({
                "step": "sample_layer_data",
                "service_name": service_name,
                "layer_id": layer_id,
                "where_clause": where_clause,
                "sample_count": len(features)
            })

            result = json.dumps({
                "sampled_records": features
            })
            self._tool_result_cache[cache_key] = result
            self._tool_call_counts[cache_key] = 1
            return result
        except Exception as e:
            return json.dumps({
                "status": "error",
                "message": str(e)
            })

    def summarize_findings(self, summary_text: str) -> str:
        self.summary = summary_text
        # Record output text in Execution.Outputs
        self.execution_outputs["text"] = summary_text
        return json.dumps({
            "status": "success",
            "message": "Process documented."
        })

    @staticmethod
    def _normalize_tool_value(value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().lower()
        if isinstance(value, list):
            return [PasdaAgent._normalize_tool_value(item) for item in value]
        if isinstance(value, dict):
            return {
                str(key).strip().lower(): PasdaAgent._normalize_tool_value(val)
                for key, val in sorted(value.items())
            }
        return value

    def _tool_cache_key(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        normalized = self._normalize_tool_value(arguments)
        return f"{tool_name}:{json.dumps(normalized, sort_keys=True, default=str)}"

    def _cached_tool_result(self, cache_key: str) -> str:
        self._tool_call_counts[cache_key] = self._tool_call_counts.get(cache_key, 1) + 1
        cached = self._tool_result_cache[cache_key]
        try:
            payload = json.loads(cached)
        except ValueError:
            payload = {"cached_result": cached}
        if isinstance(payload, dict):
            payload.setdefault("status", "cached")
            payload["cached"] = True
            payload["message"] = (
                "This PASDA step was already completed. Use the cached result and move to the next distinct "
                "action, such as inspecting a layer, sampling records, downloading the best layer, or trying a different candidate."
            )
        return json.dumps(payload)

    def _tool_call_repetition_count(self, tool_name: str, arguments: Dict[str, Any]) -> int:
        cache_key = self._tool_cache_key(tool_name, arguments)
        return self._tool_call_counts.get(cache_key, 0)

    def _get_raw_metadata(self, service_name: str, layer_id: int) -> dict:
        try:
            url = f"https://mapservices.pasda.psu.edu/server/rest/services/{service_name}/MapServer/{layer_id}?f=pjson"
            r = requests.get(url, timeout=25)
            r.raise_for_status()
            return r.json()
        except Exception:
            return {}

    def _determine_crs_and_details_with_llm(self, metadata: dict) -> dict:
        metadata_str = json.dumps(metadata)[:4000]
        prompt = (
            "Analyze the following ArcGIS layer metadata and extract important details. "
            "1. Find the coordinate reference system (CRS). Look at 'sourceSpatialReference' or 'extent.spatialReference'. "
            "Return the CRS as an EPSG code (e.g., 'EPSG:4326') or a WKT string. "
            "2. Identify geometry type and year/date of the data if available. "
            "3. Write a one-paragraph description for a user about this dataset content including these details. "
            "IMPORTANT: Do NOT mention any file names, output paths, or statements like 'the data is saved as'. "
            "Focus only on the geographical and thematic content of the layer."
            "Return only a JSON object with keys: 'crs', 'geometry_type', 'year', 'description'. "
            f"\n\nMetadata: {metadata_str}"
        )
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0
            )
            usage = response.usage
            if usage:
                self.total_input_tokens += usage.prompt_tokens or 0
                self.total_output_tokens += usage.completion_tokens or 0

            self.llm_calls += 1
            # Log this LLM call
            self.llm_calls_log.append({
                "timestamp": time.time(),
                "purpose": "crs_and_details",
                "prompt": prompt[:500],
                "response": response.choices[0].message.content[:500]
            })
            return json.loads(response.choices[0].message.content)
        except Exception:
            return {"crs": "EPSG:4326", "geometry_type": "Unknown", "year": "Unknown", "description": "Data downloaded from PASDA."}

    def download_data(self, service_name: str, layer_id: int, user_query: str) -> str:
        cache_key = self._tool_cache_key("download_data", {"service_name": service_name, "layer_id": layer_id})
        if cache_key in self._tool_result_cache:
            return self._cached_tool_result(cache_key)

        try:
            base_url = f"https://mapservices.pasda.psu.edu/server/rest/services/{service_name}/MapServer/{layer_id}/query"

            all_features = []
            offset = 0
            limit = 1000

            while True:
                params = {
                    "where": "1=1",
                    "outFields": "*",
                    "f": "geojson",
                    "resultOffset": offset,
                    "resultRecordCount": limit
                }

                r = requests.get(base_url, params=params, timeout=120)
                r.raise_for_status()
                data = r.json()

                if "error" in data:
                    return json.dumps({
                        "status": "error",
                        "message": data["error"].get("message")
                    })

                chunk_features = data.get("features", [])
                if not chunk_features:
                    break

                all_features.extend(chunk_features)

                if len(chunk_features) < limit:
                    break

                offset += limit

            if not all_features:
                return json.dumps({
                    "status": "error",
                    "message": "No features returned."
                })

            final_path = self._generate_output_path(user_query)
            
            # Metadata analysis
            raw_meta = self._get_raw_metadata(service_name, layer_id)
            details = self._determine_crs_and_details_with_llm(raw_meta)
            
            self.summary = details.get("description")
            self.execution_outputs["text"] = self.summary

            gdf = gpd.GeoDataFrame.from_features(all_features)
            
            # Assign CRS from LLM analysis
            llm_crs = details.get("crs")
            if llm_crs:
                try:
                    gdf = gdf.set_crs(llm_crs, allow_override=True)
                except Exception:
                    gdf = gdf.set_crs("EPSG:4326", allow_override=True)
            else:
                gdf = gdf.set_crs("EPSG:4326", allow_override=True)

            driver = "GeoJSON" if final_path.lower().endswith(".geojson") else "GPKG"
            artifact_type = "GeoJSON" if driver == "GeoJSON" else "GeoPackage"
            gdf.to_file(final_path, driver=driver)
            self.downloaded.append(final_path)
            self.feature_counts[final_path] = len(all_features)

            # Record persisted artifact
            self.persisted_artifacts.append({
                "type": artifact_type,
                "path": final_path,
                "feature_count": len(all_features),
                "crs": llm_crs,
                "source_service": service_name,
                "source_layer_id": layer_id
            })
            # Record lineage for download
            self.lineage_entries.append({
                "step": "download_data",
                "service_name": service_name,
                "layer_id": layer_id,
                "feature_count": len(all_features),
                "output_path": final_path
            })

            logging.info(f"Downloaded {len(all_features)} features to {final_path}")

            result = json.dumps({
                "status": "success",
                "file_path": final_path,
                "feature_count": len(all_features),
                "crs_used": llm_crs
            })
            self._tool_result_cache[cache_key] = result
            self._tool_call_counts[cache_key] = 1
            return result
        except Exception as e:
            logging.error(f"Failed to download data: {e}")
            return json.dumps({
                "status": "error",
                "message": str(e)
            })

    def _direct_pasda_candidate(self, user_query: str) -> Optional[Dict[str, Any]]:
        query = (user_query or "").lower()
        boundary_words = ("boundary", "boundaries", "polygon", "polygons")

        if any(term in query for term in ("hospital", "hospitals", "medical center", "healthcare facility")):
            return {
                "service_name": "pasda/DepHealth",
                "layer_id": 6,
                "label": "Pennsylvania hospitals",
                "reason": "Matched a common statewide PASDA Department of Health hospital layer.",
            }

        if "county" in query and any(word in query for word in boundary_words):
            return {
                "service_name": "PennDOT",
                "layer_id": 7,
                "label": "Pennsylvania county boundaries",
                "reason": "Matched a common statewide PASDA boundary layer in the PennDOT service.",
            }

        return None

    def _try_direct_pasda_download(self, user_query: str, progress_callback=None) -> bool:
        candidate = self._direct_pasda_candidate(user_query)
        if not candidate:
            return False

        self._emit_progress(
            progress_callback,
            stage="source_selection",
            message=(
                f"I recognized this as a common PASDA request for {candidate['label']} "
                "and will try the known service/layer directly."
            ),
            data={
                "service_name": candidate["service_name"],
                "layer_id": candidate["layer_id"],
                "reason": candidate["reason"],
            },
        )
        out = self.download_data(
            candidate["service_name"],
            candidate["layer_id"],
            user_query,
        )
        try:
            payload = json.loads(out)
        except ValueError:
            payload = {}

        if payload.get("status") == "success" and self.downloaded:
            self._emit_progress(
                progress_callback,
                stage="download_complete",
                message="I downloaded the matched PASDA layer and will prepare the final response.",
                data={
                    "service_name": candidate["service_name"],
                    "layer_id": candidate["layer_id"],
                    "feature_count": payload.get("feature_count"),
                },
            )
            return True

        self._emit_progress(
            progress_callback,
            stage="fallback_start",
            message=(
                "The direct PASDA match did not produce a downloadable dataset, "
                "so I will continue with the broader discovery workflow."
            ),
            data={
                "service_name": candidate["service_name"],
                "layer_id": candidate["layer_id"],
                "download_result": payload,
            },
        )
        return False

    def run(
        self,
        query: str,
        input_dataset_paths: List[str] | str | None = None,
        progress_callback=None,
        max_iterations: int = 12,
    ) -> Dict[str, Any]:
        dataset_paths = self.normalize_dataset_paths(input_dataset_paths)
        dataset_path = dataset_paths[0] if dataset_paths else None
        user_query = query
        start_time = time.time()

        self.downloaded = []
        self.summary = None
        self.feature_counts = {}
        self.raster_dimensions = {}
        self.llm_calls = 0
        self.tool_calls = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self._tool_result_cache = {}
        self._tool_call_counts = {}

        # Reset complementary data collectors
        self.execution_inputs = {"text": user_query, "dataset_path": dataset_path}
        self.execution_outputs = {}
        self.lineage_entries = []
        self.tool_calls_log = []
        self.llm_calls_log = []
        self.inline_artifacts = []
        self.persisted_artifacts = []

        result = self._empty_result(user_query, dataset_path)

        try:
            logging.info(f"Starting PASDA query: {user_query}")
            self._emit_progress(
                progress_callback,
                stage="start",
                message="I will search PASDA services, inspect candidate layers, sample fields, and download the most relevant dataset.",
                data={"has_input_dataset": dataset_path is not None, "max_iterations": max_iterations},
            )

            last_assistant_text = None
            workflow_complete = False

            if not self._try_direct_pasda_download(user_query, progress_callback):
                messages = [
                    self.system_prompt,
                    {"role": "user", "content": user_query}
                ]

                for _ in range(max_iterations):
                    if workflow_complete:
                        break
                    self._emit_progress(
                        progress_callback,
                        stage="source_selection",
                        message="I am asking the PASDA discovery model to decide the next practical search, inspection, sampling, or download step.",
                        data={"iteration": self.llm_calls + 1},
                    )
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        tools=self.tools,
                        tool_choice="auto",
                        temperature=0
                    )
                    usage = response.usage
                    if usage:
                        self.total_input_tokens += usage.prompt_tokens or 0
                        self.total_output_tokens += usage.completion_tokens or 0
                    self.llm_calls += 1
                    # Log LLM call
                    self.llm_calls_log.append({
                        "timestamp": time.time(),
                        "purpose": "agent_reasoning",
                        "messages_in": [getattr(m, "role", m.get("role", "")) if isinstance(m, dict) else getattr(m, "role", "") for m in messages[-3:]],
                        "response_snippet": response.choices[0].message.content[:300] if response.choices[0].message.content else "[tool call]"
                    })

                    msg = response.choices[0].message
                    messages.append(msg)

                    if getattr(msg, "content", None):
                        last_assistant_text = msg.content

                    if msg.tool_calls:
                        for call in msg.tool_calls:
                            self.tool_calls += 1
                            args = json.loads(call.function.arguments)
                            fn = call.function.name
                            repetition_count = self._tool_call_repetition_count(fn, args)
                            tool_messages = {
                                "list_services": "I will search PASDA's service catalog with a broad keyword to find possible data services.",
                                "get_service_metadata": "I found a candidate service and will inspect its layers to see which ones match the request.",
                                "inspect_layer_fields": "I will inspect this layer's fields and geometry type to verify whether it contains the needed attributes.",
                                "sample_layer_data": "I will sample records from the candidate layer so I can confirm the actual values before downloading.",
                                "download_data": "I am confident enough in this layer and will download it as the output dataset.",
                                "summarize_findings": "I will summarize the selected PASDA source and downloaded dataset for the final response.",
                            }
                            progress_message = tool_messages.get(fn, f"I will run the PASDA tool {fn} and inspect its result.")
                            if repetition_count:
                                progress_message = (
                                    f"I already completed this {fn} step, so I will reuse the cached result and push the workflow toward the next distinct action."
                                )
                            self._emit_progress(
                                progress_callback,
                                stage="source_validation",
                                message=progress_message,
                                data={"tool_name": fn, "cached": bool(repetition_count)},
                            )

                            # Record tool call
                            self.tool_calls_log.append({
                                "timestamp": time.time(),
                                "tool_name": fn,
                                "arguments": args,
                                "result": None  # will fill after execution
                            })

                            if fn == "list_services":
                                out = self.list_services(args.get("keyword", ""))
                            elif fn == "get_service_metadata":
                                out = self.get_service_metadata(args["service_name"])
                            elif fn == "inspect_layer_fields":
                                out = self.inspect_layer_fields(args["service_name"], args["layer_id"])
                            elif fn == "sample_layer_data":
                                out = self.sample_layer_data(
                                    args["service_name"],
                                    args["layer_id"],
                                    args.get("where_clause", "1=1")
                                )
                            elif fn == "download_data":
                                out = self.download_data(
                                    args["service_name"],
                                    args["layer_id"],
                                    user_query
                                )
                            elif fn == "summarize_findings":
                                out = self.summarize_findings(args["summary_text"])
                                if self.downloaded:
                                    workflow_complete = True
                            else:
                                out = json.dumps({
                                    "status": "error",
                                    "message": f"Unknown tool: {fn}"
                                })

                            if fn == "download_data":
                                try:
                                    download_payload = json.loads(out)
                                except ValueError:
                                    download_payload = {}
                                if download_payload.get("status") == "success" and self.downloaded and self.summary:
                                    workflow_complete = True

                            # Update tool call log with result snippet
                            self.tool_calls_log[-1]["result"] = out[:500]
                            self._emit_progress(
                                progress_callback,
                                stage="source_validation",
                                message=f"The {fn} step finished, so I will use its result to decide the next PASDA action.",
                                data={"tool_name": fn},
                            )

                            messages.append({
                                "role": "tool",
                                "tool_call_id": call.id,
                                "name": fn,
                                "content": out
                            })
                    else:
                        break

            if self.summary is None:
                self.summary = last_assistant_text
                self.execution_outputs["text"] = self.summary

            if not self.downloaded and not self.summary:
                self._emit_progress(
                    progress_callback,
                    stage="warning",
                    message="I did not find a downloadable PASDA dataset, so I will return a clear no-result summary.",
                )
                self.summary = "No datasets were found or downloaded for the query."
                self.execution_outputs["text"] = self.summary

            # Set output dataset path
            if self.downloaded:
                self._emit_progress(
                    progress_callback,
                    stage="download_complete",
                    message="I downloaded the PASDA dataset and will package its path, summary, and feature metadata.",
                    data={"downloaded_count": len(self.downloaded)},
                )
                self.execution_outputs["dataset_path"] = self.downloaded[-1]
                self.execution_outputs["dataset_paths"] = list(self.downloaded)
                self.execution_outputs["dataset_size"] = {
                    "type": "vector",
                    "feature_count": self.feature_counts.get(self.downloaded[-1])
                }

            result = self._final_result(
                user_text=user_query,
                input_dataset_path=dataset_path,
                duration_seconds=time.time() - start_time
            )

            if not result["outputs"]["text"]:
                result["outputs"]["text"] = "Process completed."
            self._emit_progress(
                progress_callback,
                stage="complete",
                message="The PASDA workflow is complete. I am preparing the normalized final response.",
            )

            return result

        except Exception as e:
            logging.error(f"Run failed: {e}")
            self._emit_progress(
                progress_callback,
                stage="error",
                message=f"The PASDA workflow hit an error, so I will return the failure details in diagnostics: {e}",
            )
            result["duration"] = f"{time.time() - start_time:.2f} seconds"
            result["outputs"]["text"] = f"Agent failed: {str(e)}"
            result["metrics"]["llm_calls"] = self.llm_calls
            result["metrics"]["tool_calls"] = self.tool_calls
            result["metrics"]["number_of_artifacts"] = len(self.downloaded)
            if self.downloaded:
                last_path = self.downloaded[-1]
                result["outputs"]["dataset_path"] = last_path
                result["outputs"]["dataset_paths"] = list(self.downloaded)
                result["outputs"]["dataset_size"]["type"] = "vector"
                result["outputs"]["dataset_size"]["feature_count"] = self.feature_counts.get(last_path)
            # Still fill complementary with whatever was collected
            result["complementary"] = {
                "Execution": {
                    "Inputs": self.execution_inputs,
                    "Outputs": self.execution_outputs
                },
                "Provenance": {
                    "Lineage": self.lineage_entries if self.lineage_entries else {},
                    "Tool Calls": self.tool_calls_log if self.tool_calls_log else {},
                    "LLM Calls": self.llm_calls_log if self.llm_calls_log else {}
                },
                "Artifacts and Logs": {
                    "Inline Artifacts": self.inline_artifacts if self.inline_artifacts else {},
                    "Persisted Artifacts": self.persisted_artifacts if self.persisted_artifacts else {}
                }
            }
            return result
