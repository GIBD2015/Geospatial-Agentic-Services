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

KNOWN_PASDA_SERVICES = {
    "dephealth": "pasda/DepHealth",
    "pasda/dephealth": "pasda/DepHealth",
    "penndot": "pasda/PennDOT",
    "pasda/penndot": "pasda/PennDOT",
    "uscensus2010_2020": "pasda/USCensus2010_2020",
    "pasda/uscensus2010_2020": "pasda/USCensus2010_2020"
}

class PasdaAgent(GeoAgent):
    agent_id = "pasda_agent"
    agent_name = "PASDA Discovery Agent"
    agent_version = "1.1.0"
    agent_description = "Discovers PASDA layers and downloads selected GIS data using LLM reasoning."

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

        self.execution_inputs = {}
        self.execution_outputs = {}
        self.lineage_entries = []
        self.tool_calls_log = []
        self.llm_calls_log = []
        self.inline_artifacts = []
        self.persisted_artifacts = []

        # Knowledge base shifted directly into the system prompt.
        # This keeps discovery lightning fast while leaving the multi-download orchestration to the AI.
        self.system_prompt = {
            "role": "system",
            "content": (
                "You are an autonomous spatial data engineer working with PASDA "
                "(Pennsylvania Spatial Data Access).\n"
                "Your job is to find and download GIS datasets using the ArcGIS REST API.\n\n"

                "HIGH-CONFIDENCE QUICK-LINKS:\n"
                "When the user requests any of the following common assets, bypass general exploratory searching "
                "and use these exact service names and layer IDs directly with your tools:\n"
                "- Hospitals / Healthcare: service_name='pasda/DepHealth', layer_id=6\n"
                "- County Boundaries: service_name='pasda/USCensus2010_2020', layer_id=2\n"
                "- School Districts: service_name='pasda/PennDOT', layer_id=11\n"
                "- Municipal Boundaries: service_name='pasda/PennDOT', layer_id=10\n"
                "- State Boundary: service_name='pasda/PennDOT', layer_id=13\n"
                "- Roads / Highways: service_name='pasda/PennDOT', layer_id=4\n\n"

                "CRITICAL RULES FOR MULTIPLE DATASETS:\n"
                "1. If the user requests MULTIPLE layers or datasets in their prompt, you must execute distinct "
                "download_data calls sequentially for EACH layer requested before summarizing your findings.\n"
                "2. When choosing an output_filename for downloads, name them specifically to match the unique layer "
                "(e.g., 'pa_hospitals' and 'pa_counties' instead of generic fallback names).\n\n"

                "GENERAL SEARCH RULES (For items not listed above):\n"
                "1. PASDA organizes data by county or agency. Service names rarely contain terms like 'land use'.\n"
                "2. Use list_services with broad geographic or agency keywords only.\n"
                "3. Use get_service_metadata to inspect specific layers inside a broad service.\n"
                "4. Use inspect_layer_fields to check columns and geometry types.\n"
                "5. Use sample_layer_data to view actual attribute values before downloading.\n"
                "6. Always use summarize_findings only AFTER all requested datasets have been downloaded.\n"
                "7. Keep your reasoning short and practical.\n"
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
                    "description": "Save a short final summary of the findings and downloaded datasets.",
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
        return set()

    def _generate_output_path(self, user_query: str, template_filename: Optional[str] = None) -> str:
        request = (user_query or "").lower()
        extension = ".geojson" if "geojson" in request and not any(term in request for term in ("geopackage", "gpkg", ".gpkg")) else ".gpkg"

        fallback_name = template_filename if template_filename else "pasda"
        # Sanitize fallback name to only contain alphanumeric characters and hyphens
        stem = re.sub(r"[^a-zA-Z0-9-]", "-", fallback_name.replace("_", "-")).lower()
        stem = re.sub(r"-+", "-", stem).strip("-")

        # Generate a random suffix in the format 0336-nrri-5122
        letters = "abcdefghijklmnopqrstuvwxyz"
        random_suffix = f"{random.randint(0, 9999):04d}-{''.join(random.choice(letters) for _ in range(4))}-{random.randint(0, 9999):04d}"

        # Build the filename manually starting with the exact agent_id- pattern (with hyphen)
        # to satisfy the server's relocation check and prevent double relocation.
        filename = f"{self.agent_id}-{stem}-{random_suffix}{extension}"

        directory = getattr(self, "output_dir", str(DATA_DIR / self.agent_id))
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, filename)

    def _environment_info(self) -> Dict[str, Any]:
        return {
            "python_version": platform.python_version(),
            "domain-specific libraries": ["requests", "geopandas", "openai"]
        }

    def _empty_result(self, user_text: str, input_dataset_path: Optional[str] = None) -> Dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "model": self.model,
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
                "Execution": {"Inputs": {}, "Outputs": {}},
                "Provenance": {"Lineage": {}, "Tool Calls": {}, "LLM Calls": {}},
                "Artifacts and Logs": {"Inline Artifacts": {}, "Persisted Artifacts": {}}
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
            "model": self.model,
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

            self.lineage_entries.append({
                "step": "list_services",
                "keyword": keyword,
                "url": url,
                "result_count": len(filtered)
            })

            result = json.dumps({"services": filtered, "total": len(filtered)})
            self._tool_result_cache[cache_key] = result
            self._tool_call_counts[cache_key] = 1
            return result
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    def get_service_metadata(self, service_name: str) -> str:
        service_name = self._normalize_service_name(service_name)
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
            return json.dumps({"status": "error", "message": str(e)})

    def inspect_layer_fields(self, service_name: str, layer_id: int) -> str:
        service_name = self._normalize_service_name(service_name)
        cache_key = self._tool_cache_key("inspect_layer_fields", {"service_name": service_name, "layer_id": layer_id})
        if cache_key in self._tool_result_cache:
            return self._cached_tool_result(cache_key)

        try:
            url = f"https://mapservices.pasda.psu.edu/server/rest/services/{service_name}/MapServer/{layer_id}?f=json"
            r = requests.get(url, timeout=25)
            r.raise_for_status()

            data = r.json()
            fields = [{"name": f["name"], "type": f["type"], "alias": f.get("alias", "")} for f in data.get("fields", [])]
            geom_type = data.get("geometryType")

            self.lineage_entries.append({
                "step": "inspect_layer_fields",
                "service_name": service_name,
                "layer_id": layer_id,
                "geometry_type": geom_type,
                "field_count": len(fields)
            })

            result = json.dumps({"geometry_type": geom_type, "fields": fields})
            self._tool_result_cache[cache_key] = result
            self._tool_call_counts[cache_key] = 1
            return result
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    def sample_layer_data(self, service_name: str, layer_id: int, where_clause: str) -> str:
        service_name = self._normalize_service_name(service_name)
        cache_key = self._tool_cache_key("sample_layer_data", {"service_name": service_name, "layer_id": layer_id, "where_clause": where_clause or "1=1"})
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
                return json.dumps({"status": "error", "message": data["error"].get("message")})

            features = [f.get("attributes", {}) for f in data.get("features", [])]

            self.inline_artifacts.append({
                "type": "sample_layer_data",
                "service_name": service_name,
                "layer_id": layer_id,
                "where_clause": where_clause,
                "sample_records": features[:3]
            })

            self.lineage_entries.append({
                "step": "sample_layer_data",
                "service_name": service_name,
                "layer_id": layer_id,
                "where_clause": where_clause,
                "sample_count": len(features)
            })

            result = json.dumps({"sampled_records": features})
            self._tool_result_cache[cache_key] = result
            self._tool_call_counts[cache_key] = 1
            return result
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    def summarize_findings(self, summary_text: str) -> str:
        self.summary = summary_text
        self.execution_outputs["text"] = summary_text
        return json.dumps({"status": "success", "message": "Process documented."})

    @staticmethod
    def _normalize_tool_value(value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().lower()
        if isinstance(value, list):
            return [PasdaAgent._normalize_tool_value(item) for item in value]
        if isinstance(value, dict):
            return {str(key).strip().lower(): PasdaAgent._normalize_tool_value(val) for key, val in sorted(value.items())}
        return value

    def _tool_cache_key(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        normalized_arguments = dict(arguments or {})
        if "service_name" in normalized_arguments:
            normalized_arguments["service_name"] = self._normalize_service_name(normalized_arguments["service_name"])
        normalized = self._normalize_tool_value(normalized_arguments)
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
            payload["message"] = "This PASDA step was already completed. Use the cached result."
        return json.dumps(payload)

    def _tool_call_repetition_count(self, tool_name: str, arguments: Dict[str, Any]) -> int:
        cache_key = self._tool_cache_key(tool_name, arguments)
        return self._tool_call_counts.get(cache_key, 0)

    def _normalize_service_name(self, service_name: str) -> str:
        normalized = re.sub(r"\s+", "", str(service_name or "").strip())
        normalized = re.sub(r"/MapServer/?$", "", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"^https?://mapservices\.pasda\.psu\.edu/server/rest/services/", "", normalized, flags=re.IGNORECASE)
        return KNOWN_PASDA_SERVICES.get(normalized.lower(), normalized)

    def _get_raw_metadata(self, service_name: str, layer_id: int) -> dict:
        try:
            service_name = self._normalize_service_name(service_name)
            url = f"https://mapservices.pasda.psu.edu/server/rest/services/{service_name}/MapServer/{layer_id}?f=pjson"
            r = requests.get(url, timeout=25)
            r.raise_for_status()
            return r.json()
        except Exception:
            return {}

    def _details_from_arcgis_metadata(self, metadata: dict) -> dict:
        name = metadata.get("name") or metadata.get("displayField") or "PASDA layer"
        description = metadata.get("description") or f"Downloaded {name} from PASDA."
        description = re.sub(r"<[^>]+>", " ", str(description))
        description = re.sub(r"\s+", " ", description).strip()
        if not description or len(description) < 10:
            description = f"Downloaded {name} from PASDA."
        return {"description": description}

    def download_data(self, service_name: str, layer_id: int, output_filename: str) -> str:
        service_name = self._normalize_service_name(service_name)
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
                    return json.dumps({"status": "error", "message": data["error"].get("message")})

                chunk_features = data.get("features", [])
                if not chunk_features:
                    break

                all_features.extend(chunk_features)
                if len(chunk_features) < limit:
                    break
                offset += limit

            if not all_features:
                return json.dumps({"status": "error", "message": "No features returned."})

            final_path = self._generate_output_path(output_filename, template_filename=output_filename)
            raw_meta = self._get_raw_metadata(service_name, layer_id)
            details = self._details_from_arcgis_metadata(raw_meta)

            if self.summary:
                self.summary += f" | {details.get('description')}"
            else:
                self.summary = details.get("description")

            self.execution_outputs["text"] = self.summary

            gdf = gpd.GeoDataFrame.from_features(all_features)
            output_crs = "EPSG:4326"
            gdf = gdf.set_crs(output_crs, allow_override=True)

            driver = "GeoJSON" if final_path.lower().endswith(".geojson") else "GPKG"
            artifact_type = "GeoJSON" if driver == "GeoJSON" else "GeoPackage"
            gdf.to_file(final_path, driver=driver)

            self.downloaded.append(final_path)
            self.feature_counts[final_path] = len(all_features)

            self.persisted_artifacts.append({
                "type": artifact_type,
                "path": final_path,
                "feature_count": len(all_features),
                "crs": output_crs,
                "source_service": service_name,
                "source_layer_id": layer_id
            })
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
                "crs_used": output_crs
            })
            self._tool_result_cache[cache_key] = result
            self._tool_call_counts[cache_key] = 1
            return result
        except Exception as e:
            logging.error(f"Failed to download data: {e}")
            return json.dumps({"status": "error", "message": str(e)})

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

        self.execution_inputs = {"text": user_query, "dataset_path": dataset_path}
        self.execution_outputs = {}
        self.lineage_entries = []
        self.tool_calls_log = []
        self.llm_calls_log = []
        self.inline_artifacts = []
        self.persisted_artifacts = []

        result = self._empty_result(user_query, dataset_path)

        try:
            logging.info(f"Starting PASDA AI pipeline: {user_query}")
            self._emit_progress(
                progress_callback,
                stage="start",
                message="Analyzing prompt with AI and identifying mapped datasets.",
                data={"max_iterations": max_iterations},
            )

            last_assistant_text = None
            workflow_complete = False

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
                    message="Evaluating workflow logic status with LLM...",
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

                        self.tool_calls_log.append({
                            "timestamp": time.time(),
                            "tool_name": fn,
                            "arguments": args,
                            "result": None
                        })

                        if fn == "list_services":
                            out = self.list_services(args.get("keyword", ""))
                        elif fn == "get_service_metadata":
                            out = self.get_service_metadata(args["service_name"])
                        elif fn == "inspect_layer_fields":
                            out = self.inspect_layer_fields(args["service_name"], args["layer_id"])
                        elif fn == "sample_layer_data":
                            out = self.sample_layer_data(args["service_name"], args["layer_id"], args.get("where_clause", "1=1"))
                        elif fn == "download_data":
                            out = self.download_data(args["service_name"], args["layer_id"], args.get("output_filename", "dataset"))
                        elif fn == "summarize_findings":
                            out = self.summarize_findings(args["summary_text"])
                            workflow_complete = True
                        else:
                            out = json.dumps({"status": "error", "message": f"Unknown tool: {fn}"})

                        self.tool_calls_log[-1]["result"] = out[:500]
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
                self.summary = "No datasets were found or downloaded for the query."
                self.execution_outputs["text"] = self.summary

            if self.downloaded:
                self.execution_outputs["dataset_path"] = self.downloaded[-1]
                self.execution_outputs["dataset_paths"] = list(self.downloaded)
                self.execution_outputs["dataset_size"] = {
                    "type": "vector",
                    "feature_count": self.feature_counts.get(self.downloaded[-1])
                }

            return self._final_result(
                user_text=user_query,
                input_dataset_path=dataset_path,
                duration_seconds=time.time() - start_time
            )

        except Exception as e:
            logging.error(f"Run failed: {e}")
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

            result["complementary"] = {
                "Execution": {"Inputs": self.execution_inputs, "Outputs": self.execution_outputs},
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
