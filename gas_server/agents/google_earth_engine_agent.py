from __future__ import annotations

import csv
import html
import json
import os
import platform
import re
import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
import geopandas as gpd
import requests
from shapely.geometry import mapping

from gas_server.core.config import DATA_DIR, ensure_runtime_dirs
from gas_server.core.file_naming import build_output_filename
from gas_server.core.geo_agent import GeoAgent, ProgressCallback
from gas_server.core.llm_client import build_llm_client, format_service_name


load_dotenv()
ensure_runtime_dirs()


try:
    import ee
except Exception:  # pragma: no cover - exercised in environments without optional dependency
    ee = None


SUPPORTED_ACTIONS = {
    "ndvi_summary",
    "ndvi_time_series",
    "ndvi_map",
    "cloud_filtered_composite",
    "chirps_precipitation_summary",
    "climate_time_series",
    "land_cover_area_summary",
    "land_cover_map",
    "surface_water_map",
    "create_export_task",
}

DATASET_ALIASES = {
    "sentinel2_sr": {
        "ee_id": "COPERNICUS/S2_SR_HARMONIZED",
        "kind": "optical",
        "nir": "B8",
        "red": "B4",
        "rgb": ["B4", "B3", "B2"],
        "scale": 10,
        "cloud_property": "CLOUDY_PIXEL_PERCENTAGE",
    },
    "landsat8_sr": {
        "ee_id": "LANDSAT/LC08/C02/T1_L2",
        "kind": "optical",
        "nir": "SR_B5",
        "red": "SR_B4",
        "rgb": ["SR_B4", "SR_B3", "SR_B2"],
        "scale": 30,
        "cloud_property": "CLOUD_COVER",
    },
    "landsat9_sr": {
        "ee_id": "LANDSAT/LC09/C02/T1_L2",
        "kind": "optical",
        "nir": "SR_B5",
        "red": "SR_B4",
        "rgb": ["SR_B4", "SR_B3", "SR_B2"],
        "scale": 30,
        "cloud_property": "CLOUD_COVER",
    },
    "chirps_daily": {
        "ee_id": "UCSB-CHG/CHIRPS/DAILY",
        "kind": "climate",
        "temporal_resolution": "daily",
        "band": "precipitation",
        "variables": {
            "precipitation": {"band": "precipitation", "unit": "mm/day", "label": "Precipitation"},
        },
        "scale": 5566,
    },
    "gridmet_daily": {
        "ee_id": "IDAHO_EPSCOR/GRIDMET",
        "kind": "climate",
        "temporal_resolution": "daily",
        "variables": {
            "precipitation": {"band": "pr", "unit": "mm/day", "label": "Precipitation"},
            "tmax": {"band": "tmmx", "unit": "degC", "label": "Maximum temperature", "offset": -273.15},
            "tmin": {"band": "tmmn", "unit": "degC", "label": "Minimum temperature", "offset": -273.15},
            "relative_humidity_max": {"band": "rmax", "unit": "%", "label": "Maximum relative humidity"},
            "relative_humidity_min": {"band": "rmin", "unit": "%", "label": "Minimum relative humidity"},
            "solar_radiation": {"band": "srad", "unit": "W/m^2", "label": "Solar radiation"},
            "wind_speed": {"band": "vs", "unit": "m/s", "label": "Wind speed"},
            "pet": {"band": "pet", "unit": "mm/day", "label": "Potential evapotranspiration"},
        },
        "scale": 4638,
    },
    "daymet_daily": {
        "ee_id": "NASA/ORNL/DAYMET_V4",
        "kind": "climate",
        "temporal_resolution": "daily",
        "variables": {
            "precipitation": {"band": "prcp", "unit": "mm/day", "label": "Precipitation"},
            "tmax": {"band": "tmax", "unit": "degC", "label": "Maximum temperature"},
            "tmin": {"band": "tmin", "unit": "degC", "label": "Minimum temperature"},
            "solar_radiation": {"band": "srad", "unit": "W/m^2", "label": "Solar radiation"},
            "vapor_pressure": {"band": "vp", "unit": "Pa", "label": "Vapor pressure"},
            "snow_water_equivalent": {"band": "swe", "unit": "kg/m^2", "label": "Snow water equivalent"},
        },
        "scale": 1000,
    },
    "era5_land_daily": {
        "ee_id": "ECMWF/ERA5_LAND/DAILY_AGGR",
        "kind": "climate",
        "temporal_resolution": "daily",
        "variables": {
            "precipitation": {"band": "total_precipitation_sum", "unit": "mm/day", "label": "Total precipitation", "multiplier": 1000},
            "temperature": {"band": "temperature_2m", "unit": "degC", "label": "2m temperature", "offset": -273.15},
            "soil_temperature": {"band": "soil_temperature_level_1", "unit": "degC", "label": "Soil temperature level 1", "offset": -273.15},
            "evaporation": {"band": "total_evaporation_sum", "unit": "mm/day", "label": "Total evaporation", "multiplier": 1000},
        },
        "scale": 11132,
    },
    "terraclimate_monthly": {
        "ee_id": "IDAHO_EPSCOR/TERRACLIMATE",
        "kind": "climate",
        "temporal_resolution": "monthly",
        "variables": {
            "precipitation": {"band": "pr", "unit": "mm/month", "label": "Precipitation"},
            "tmax": {"band": "tmmx", "unit": "degC", "label": "Maximum temperature", "multiplier": 0.1},
            "tmin": {"band": "tmmn", "unit": "degC", "label": "Minimum temperature", "multiplier": 0.1},
            "aet": {"band": "aet", "unit": "mm/month", "label": "Actual evapotranspiration"},
            "pet": {"band": "pet", "unit": "mm/month", "label": "Potential evapotranspiration"},
            "soil_moisture": {"band": "soil", "unit": "mm", "label": "Soil moisture"},
            "drought_index": {"band": "pdsi", "unit": "index", "label": "Palmer drought severity index", "multiplier": 0.01},
            "vapor_pressure_deficit": {"band": "vpd", "unit": "kPa", "label": "Vapor pressure deficit", "multiplier": 0.01},
            "wind_speed": {"band": "vs", "unit": "m/s", "label": "Wind speed", "multiplier": 0.01},
        },
        "scale": 4638,
    },
    "esa_worldcover": {
        "ee_id": "ESA/WorldCover/v200",
        "kind": "land_cover",
        "band": "Map",
        "scale": 10,
    },
    "dynamic_world": {
        "ee_id": "GOOGLE/DYNAMICWORLD/V1",
        "kind": "land_cover",
        "band": "label",
        "scale": 10,
    },
    "jrc_global_surface_water": {
        "ee_id": "JRC/GSW1_4/GlobalSurfaceWater",
        "kind": "water",
        "band": "occurrence",
        "scale": 30,
    },
    "sentinel1_grd": {
        "ee_id": "COPERNICUS/S1_GRD",
        "kind": "radar",
        "band": "VV",
        "scale": 10,
    },
}

NAMED_REGION_BBOXES = {
    "centre county": [-78.36, 40.69, -77.13, 41.32],
    "centre county, pennsylvania": [-78.36, 40.69, -77.13, 41.32],
    "centre county, pa": [-78.36, 40.69, -77.13, 41.32],
    "pennsylvania": [-80.52, 39.72, -74.69, 42.27],
    "california": [-124.48, 32.53, -114.13, 42.01],
    "conterminous united states": [-124.85, 24.4, -66.88, 49.38],
    "contiguous united states": [-124.85, 24.4, -66.88, 49.38],
}

LAND_COVER_LABELS = {
    "esa_worldcover": {
        10: "Tree cover",
        20: "Shrubland",
        30: "Grassland",
        40: "Cropland",
        50: "Built-up",
        60: "Bare/sparse vegetation",
        70: "Snow and ice",
        80: "Permanent water bodies",
        90: "Herbaceous wetland",
        95: "Mangroves",
        100: "Moss and lichen",
    },
    "dynamic_world": {
        0: "Water",
        1: "Trees",
        2: "Grass",
        3: "Flooded vegetation",
        4: "Crops",
        5: "Shrub and scrub",
        6: "Built",
        7: "Bare",
        8: "Snow and ice",
    },
}

LAND_COVER_PALETTES = {
    "esa_worldcover": {
        "class_values": [10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100],
        "palette": [
            "#006400",
            "#ffbb22",
            "#ffff4c",
            "#f096ff",
            "#fa0000",
            "#b4b4b4",
            "#f0f0f0",
            "#0064c8",
            "#0096a0",
            "#00cf75",
            "#fae6a0",
        ],
    },
    "dynamic_world": {
        "class_values": [0, 1, 2, 3, 4, 5, 6, 7, 8],
        "palette": [
            "#419bdf",
            "#397d49",
            "#88b053",
            "#7a87c6",
            "#e49635",
            "#dfc35a",
            "#c4281b",
            "#a59b8f",
            "#b39fe1",
        ],
    },
}


@dataclass
class GeePlan:
    action: str
    dataset: str
    region: dict[str, Any]
    date_range: dict[str, str]
    temporal_resolution: str
    reducer: str
    scale: int
    max_cloud_percent: float
    outputs: list[str]
    export: dict[str, Any]
    variables: list[str]
    source: str
    notes: list[str]


class GoogleEarthEngineAgent(GeoAgent):
    agent_id = "google_earth_engine_agent"
    agent_name = "Google Earth Engine Agent"
    agent_version = "1.0.0"
    agent_description = (
        "Uses an LLM-planned, tool-validated workflow to run focused Google Earth "
        "Engine data processing tasks through the Earth Engine Python API."
    )
    requires_input_datasets = False
    requires_model_credentials = True

    def __init__(self, api_key: str | None = None, model: str | None = None):
        super().__init__(
            api_key=api_key,
            model=model or "gpt-5.2",
            output_dir=DATA_DIR / self.agent_id,
        )
        self.service_name = format_service_name(self.agent_name)
        self.client = build_llm_client(service_name=self.service_name, openai_api_key=self.api_key)
        self.tool_trace: list[dict[str, Any]] = []
        self._ee_initialized = False

    def _output_path(self, query: str, extension: str, fallback: str) -> str:
        directory = self.ensure_directory(self.output_dir)
        filename = build_output_filename(query, extension=extension, fallback=fallback, max_words=5)
        return str(directory / f"{fallback}_{filename}")

    def _record_tool(self, name: str, **details: Any) -> None:
        self.increment_tool_calls()
        self.tool_trace.append({"tool": name, **details})

    def _progress(self, stage: str, message: str, **data: Any) -> None:
        self.emit_progress(
            getattr(self, "_active_progress_callback", None),
            stage=stage,
            message=message,
            data=data or None,
        )

    def _extract_json_object(self, text: str) -> dict[str, Any]:
        match = re.search(r"\{.*\}", text or "", flags=re.S)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _llm_plan(self, query: str) -> dict[str, Any]:
        if self.client is None:
            raise ValueError("Google Earth Engine Agent requires OPENAI_API_KEY or GIBD_API_KEY for LLM planning.")

        prompt = {
            "allowed_actions": sorted(SUPPORTED_ACTIONS),
            "allowed_datasets": sorted(DATASET_ALIASES),
            "region_requirement": (
                "Return a named_place or bbox region from the natural-language request. "
                "The server resolves the final analysis region in this priority order: uploaded vector input, "
                "explicit parameters.bbox, then place-name lookup/geocoding."
            ),
            "date_format": "YYYY-MM-DD",
            "output_contract": {
                "action": "one allowed action",
                "dataset": "one allowed dataset alias",
                "region": {"type": "bbox|named_place", "coordinates": [], "name": ""},
                "date_range": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
                "temporal_resolution": "daily|monthly|single_period",
                "reducer": "mean|sum|median|area",
                "scale": "integer meters",
                "cloud_filter": {"max_cloud_percent": "number"},
                "variables": ["climate variable names such as precipitation, tmax, tmin, wind_speed"],
                "outputs": ["json", "csv", "map", "geojson", "html", "export"],
                "export": {"enabled": False, "destination": "drive|gcs", "description": ""},
                "notes": ["short assumptions"],
            },
            "input_dataset_guidance": (
                "If an uploaded vector input dataset is provided, "
                "the server will use that vector as the Earth Engine analysis region by default, ahead of any bbox or place name. "
                "You should still choose the best action, dataset, date_range, scale, and outputs."
            ),
            "climate_guidance": (
                "For precipitation/temperature/humidity/wind/radiation/evapotranspiration/soil moisture/drought time series, "
                "choose action climate_time_series. Prefer gridmet_daily for CONUS daily weather/climate requests, "
                "daymet_daily for North America daily local climate, chirps_daily for global precipitation-only, "
                "era5_land_daily for broad global daily variables, and terraclimate_monthly for monthly climate/water-balance/drought."
            ),
            "land_cover_guidance": (
                "For land-cover maps, land-use maps, classified map previews, or requests to visualize ESA WorldCover or Dynamic World, "
                "choose action land_cover_map. For class area statistics or tabular area summaries, choose land_cover_area_summary."
            ),
            "surface_water_guidance": (
                "For surface-water occurrence, flood-water maps, inundation previews, exposure-ready water polygons, JRC Global Surface Water, "
                "or Sentinel-1 water detection, choose action surface_water_map. Prefer jrc_global_surface_water for long-term occurrence "
                "requests and sentinel1_grd for recent flood-water or radar/SAR requests."
            ),
            "ndvi_guidance": (
                "For daily NDVI, NDVI trend, NDVI line chart, or NDVI time series requests, choose action ndvi_time_series. "
                "Set temporal_resolution to monthly when the user asks for monthly, per-month, one row per month, or monthly composites. "
                "For a single period summary, choose ndvi_summary. For a raster preview map, choose ndvi_map."
            ),
            "user_request": query,
            "existing_parameters": self.request_parameters,
        }
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You plan Google Earth Engine workflows for a GAS service. "
                        "Return strict JSON only. Do not return Python code. Choose only allowed actions and datasets."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt, default=str)},
            ],
            temperature=0.1,
        )
        self.increment_llm_calls()
        self._record_llm_usage(response)
        content = response.choices[0].message.content
        return self._extract_json_object(content)

    def _record_llm_usage(self, response: Any) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        if prompt_tokens is None:
            prompt_tokens = getattr(usage, "input_tokens", None)
        if completion_tokens is None:
            completion_tokens = getattr(usage, "output_tokens", None)
        if prompt_tokens is not None:
            self.input_tokens += int(prompt_tokens or 0)
            self.token_usage_available = True
        if completion_tokens is not None:
            self.output_tokens += int(completion_tokens or 0)
            self.token_usage_available = True

    def _normalize_dataset(self, value: Any, action: str) -> str:
        raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "sentinel_2": "sentinel2_sr",
            "sentinel2": "sentinel2_sr",
            "s2": "sentinel2_sr",
            "landsat": "landsat8_sr",
            "landsat_8": "landsat8_sr",
            "landsat8": "landsat8_sr",
            "landsat_9": "landsat9_sr",
            "landsat9": "landsat9_sr",
            "chirps": "chirps_daily",
            "chirps_daily": "chirps_daily",
            "gridmet": "gridmet_daily",
            "gridmet_daily": "gridmet_daily",
            "daymet": "daymet_daily",
            "daymet_daily": "daymet_daily",
            "era5": "era5_land_daily",
            "era5_land": "era5_land_daily",
            "era5_land_daily": "era5_land_daily",
            "terraclimate": "terraclimate_monthly",
            "terraclimate_monthly": "terraclimate_monthly",
            "esa": "esa_worldcover",
            "worldcover": "esa_worldcover",
            "esa_world_cover": "esa_worldcover",
            "dynamicworld": "dynamic_world",
            "jrc": "jrc_global_surface_water",
            "jrc_gsw": "jrc_global_surface_water",
            "jrc_global_surface_water": "jrc_global_surface_water",
            "global_surface_water": "jrc_global_surface_water",
            "global_surface_water_occurrence": "jrc_global_surface_water",
            "water_occurrence": "jrc_global_surface_water",
            "sentinel_1": "sentinel1_grd",
            "sentinel1": "sentinel1_grd",
            "s1": "sentinel1_grd",
            "sentinel1_grd": "sentinel1_grd",
        }
        dataset = aliases.get(raw, raw)
        if dataset in DATASET_ALIASES:
            return dataset
        if action == "chirps_precipitation_summary":
            return "chirps_daily"
        if action == "climate_time_series":
            return "gridmet_daily"
        if action == "land_cover_area_summary":
            return "esa_worldcover"
        if action == "surface_water_map":
            return "jrc_global_surface_water"
        return "sentinel2_sr"

    def _lookup_region_bbox(self, name: str) -> list[float] | None:
        normalized = re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()
        if not normalized:
            return None
        normalized_tokens = set(normalized.split())
        for region_name, bbox in NAMED_REGION_BBOXES.items():
            region_normalized = re.sub(r"[^a-z0-9]+", " ", region_name.lower()).strip()
            if normalized == region_normalized:
                return bbox
            region_tokens = set(region_normalized.split())
            if len(region_tokens) >= 2 and region_normalized and region_normalized in normalized:
                return bbox
            if len(region_tokens) >= 2 and region_tokens <= normalized_tokens:
                return bbox
        return None

    def _geocode_cache_path(self) -> Path:
        return self.ensure_directory(self.output_dir) / "geocode_cache.json"

    def _load_geocode_cache(self) -> dict[str, Any]:
        path = self._geocode_cache_path()
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_geocode_cache(self, cache: dict[str, Any]) -> None:
        path = self._geocode_cache_path()
        path.write_text(json.dumps(cache, indent=2, default=str), encoding="utf-8")

    def _geocode_place(self, name: str) -> dict[str, Any] | None:
        place_name = str(name or "").strip()
        if not place_name:
            return None
        cache_key = re.sub(r"\s+", " ", place_name.lower()).strip()
        cache = self._load_geocode_cache()
        cached = cache.get(cache_key)
        if isinstance(cached, dict) and isinstance(cached.get("coordinates"), list):
            self._record_tool("geocode_place_cache_hit", place=place_name, source=cached.get("source"))
            return cached

        endpoint = os.getenv("GEE_GEOCODER_URL", "https://nominatim.openstreetmap.org/search")
        headers = {
            "User-Agent": os.getenv(
                "GEE_GEOCODER_USER_AGENT",
                "GAS-GoogleEarthEngineAgent/1.0 (contact: zhenlong@psu.edu)",
            )
        }
        try:
            response = requests.get(
                endpoint,
                params={
                    "q": place_name,
                    "format": "jsonv2",
                    "limit": 1,
                    "polygon_geojson": 1,
                },
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
        except Exception as exc:
            self._record_tool("geocode_place_failed", place=place_name, error=str(exc))
            return None
        results = response.json()
        if not isinstance(results, list) or not results:
            self._record_tool("geocode_place_no_result", place=place_name)
            return None
        item = results[0]
        bbox_values = item.get("boundingbox")
        if not isinstance(bbox_values, list) or len(bbox_values) != 4:
            self._record_tool("geocode_place_invalid_bbox", place=place_name)
            return None
        south, north, west, east = [float(value) for value in bbox_values]
        if west >= east or south >= north:
            self._record_tool("geocode_place_invalid_bbox", place=place_name, boundingbox=bbox_values)
            return None
        resolved = {
            "type": "geocoded_place",
            "coordinates": [west, south, east, north],
            "name": item.get("display_name") or place_name,
            "geojson": item.get("geojson") if isinstance(item.get("geojson"), dict) else None,
            "source": "OpenStreetMap Nominatim",
            "source_url": endpoint,
            "queried_name": place_name,
        }
        cache[cache_key] = resolved
        self._write_geocode_cache(cache)
        self._record_tool("geocode_place", place=place_name, resolved_name=resolved["name"], source=resolved["source"])
        return resolved

    def _region_from_input_dataset(self, input_dataset_paths: list[str] | None) -> dict[str, Any] | None:
        for dataset_path in input_dataset_paths or []:
            path = Path(dataset_path)
            if not path.exists():
                continue
            try:
                gdf = gpd.read_file(path)
            except Exception:
                continue
            if gdf.empty or gdf.geometry.is_empty.all():
                continue
            if gdf.crs is not None and str(gdf.crs).upper() not in {"EPSG:4326", "OGC:CRS84"}:
                gdf = gdf.to_crs("EPSG:4326")
            bounds = [float(value) for value in gdf.total_bounds]
            union = gdf.geometry.union_all() if hasattr(gdf.geometry, "union_all") else gdf.geometry.unary_union
            return {
                "type": "input_vector",
                "coordinates": bounds,
                "name": path.name,
                "geojson": mapping(union),
            }
        return None

    def _normalize_requested_outputs(self, raw_outputs: list[Any], query: str | None = None) -> list[str]:
        alias_map = {
            "chart": "html",
            "line_chart": "html",
            "map": "html",
            "interactive_map": "html",
            "thumbnail": "thumbnail",
            "tiles": "tiles",
            "tile": "tiles",
            "table": "csv",
            "spreadsheet": "csv",
            "report": "json",
            "metadata": "json",
            "summary": "json",
            "geotiff": "export",
            "tif": "export",
            "tiff": "export",
        }
        outputs: list[str] = []
        for item in raw_outputs:
            output = str(item).strip().lower().replace("-", "_").replace(" ", "_")
            if output:
                outputs.append(alias_map.get(output, output))

        query_text = (query or "").lower()
        explicit_outputs: set[str] = set()
        if re.search(r"\bhtml\b|\bchart\b|\bline chart\b|\bmap\b|\bpreview\b", query_text):
            explicit_outputs.add("html")
        if re.search(r"\bthumbnail\b|\bpreview image\b|\bstatic image\b|\bimage preview\b|\bpng\b|\bjpg\b|\bjpeg\b", query_text):
            explicit_outputs.add("thumbnail")
        if re.search(r"\btile\b|\btiles\b|\bmap tile\b", query_text):
            explicit_outputs.add("tiles")
        if re.search(r"\bcsv\b|\btable\b|\bspreadsheet\b", query_text):
            explicit_outputs.add("csv")
        if re.search(r"\bjson\b|\breport\b|\bmetadata\b", query_text):
            explicit_outputs.add("json")
        if re.search(r"\bgeojson\b", query_text):
            explicit_outputs.add("geojson")
        if re.search(r"\bexport\b|\bgeotiff\b|\btiff?\b|\bgcs\b|\bcloud storage\b|\bdrive\b", query_text):
            explicit_outputs.add("export")

        if explicit_outputs:
            outputs = [output for output in outputs if output in explicit_outputs or output in {"thumbnail", "tiles"}]
            for output in explicit_outputs:
                if output not in outputs:
                    outputs.append(output)

        if not outputs:
            outputs = ["json", "csv"]
        return list(dict.fromkeys(outputs))

    def _wants_output(self, plan: GeePlan, *formats: str, default: bool = False) -> bool:
        if not plan.outputs:
            return default
        normalized = {str(output).strip().lower() for output in plan.outputs}
        aliases = {"table": "csv", "spreadsheet": "csv", "chart": "html", "map": "html", "report": "json"}
        wanted = {aliases.get(str(fmt).strip().lower(), str(fmt).strip().lower()) for fmt in formats}
        return bool(normalized & wanted)

    def _artifact_outputs(self, plan: GeePlan) -> dict[str, bool]:
        return {
            "json": self._wants_output(plan, "json"),
            "csv": self._wants_output(plan, "csv"),
            "html": self._wants_output(plan, "html"),
            "geojson": self._wants_output(plan, "geojson"),
            "export": self._wants_output(plan, "export"),
            "thumbnail": self._wants_output(plan, "thumbnail"),
            "tiles": self._wants_output(plan, "tiles"),
        }

    def _requested_artifact_labels(self, plan: GeePlan, *, rows: bool = False, html_chart: bool = False, html_map: bool = False) -> list[str]:
        requested = self._artifact_outputs(plan)
        labels = []
        if requested["json"]:
            labels.append("JSON report artifact")
        if rows and requested["csv"]:
            labels.append("CSV table")
        if html_chart and requested["html"]:
            labels.append("HTML line chart")
        if html_map and requested["html"]:
            labels.append("HTML map/preview")
        if requested["export"]:
            labels.append("Earth Engine export metadata")
        return labels

    def _normalize_temporal_resolution(self, value: Any, *, action: str, query: str | None = None) -> str:
        raw = str(value or self.request_parameters.get("temporal_resolution") or "").strip().lower()
        normalized = raw.replace("-", "_").replace(" ", "_")
        query_text = str(query or "").lower()
        if normalized in {"month", "monthly", "per_month", "month_start", "calendar_month"}:
            return "monthly"
        if normalized in {"day", "daily", "per_day", "date", "available_date", "available_dates"}:
            return "daily"
        if normalized in {"single", "single_period", "period", "summary"}:
            return "single_period"
        if re.search(r"\bmonthly\b|\bper month\b|\bone row per month\b|\beach month\b|\bmonthly composite", query_text):
            return "monthly"
        if re.search(r"\bdaily\b|\bper day\b|\beach date\b|\bavailable[- ]date", query_text):
            return "daily"
        if action == "ndvi_summary":
            return "single_period"
        return "daily"

    def _validate_plan(
        self,
        raw_plan: dict[str, Any],
        input_dataset_paths: list[str] | None = None,
        query: str | None = None,
    ) -> GeePlan:
        action = str(raw_plan.get("action") or "").strip().lower()
        action_aliases = {
            "compute_ndvi_summary": "ndvi_summary",
            "compute_ndvi_time_series": "ndvi_time_series",
            "daily_ndvi": "ndvi_time_series",
            "ndvi_daily": "ndvi_time_series",
            "ndvi_timeseries": "ndvi_time_series",
            "ndvi_time_series": "ndvi_time_series",
            "ndvi_trend": "ndvi_time_series",
            "compute_ndvi_map": "ndvi_map",
            "create_ndvi_map": "ndvi_map",
            "map_ndvi": "ndvi_map",
            "create_cloud_filtered_composite": "cloud_filtered_composite",
            "summarize_chirps_precipitation": "chirps_precipitation_summary",
            "create_climate_time_series": "climate_time_series",
            "climate_variable_time_series": "climate_time_series",
            "summarize_climate_time_series": "climate_time_series",
            "weather_time_series": "climate_time_series",
            "temperature_time_series": "climate_time_series",
            "precipitation_time_series": "climate_time_series",
            "summarize_land_cover_area": "land_cover_area_summary",
            "create_land_cover_map": "land_cover_map",
            "map_land_cover": "land_cover_map",
            "land_cover_map": "land_cover_map",
            "land_use_map": "land_cover_map",
            "classify_land_cover": "land_cover_map",
            "surface_water_map": "surface_water_map",
            "water_occurrence_map": "surface_water_map",
            "map_surface_water": "surface_water_map",
            "map_water_occurrence": "surface_water_map",
            "flood_water_map": "surface_water_map",
            "recent_flood_water": "surface_water_map",
            "detect_flood_water": "surface_water_map",
            "map_inundation": "surface_water_map",
            "create_earth_engine_export_task": "create_export_task",
        }
        action = action_aliases.get(action, action)
        raw_outputs = raw_plan.get("outputs", []) if isinstance(raw_plan.get("outputs"), list) else []
        if action == "ndvi_summary" and any(output in raw_outputs for output in ("map", "geojson")):
            action = "ndvi_map"
        if action == "ndvi_summary" and any(output in raw_outputs for output in ("chart", "time_series", "timeseries")):
            action = "ndvi_time_series"
        if action == "land_cover_area_summary" and any(output in raw_outputs for output in ("map", "html", "thumbnail", "tiles")):
            action = "land_cover_map"
        if any(str(output).strip().lower() in {"geojson", "map", "html"} for output in raw_outputs) and re.search(
            r"\b(water|flood|inundation|jrc|sentinel[- ]?1|sar)\b",
            str(raw_plan.get("action") or ""),
            flags=re.I,
        ):
            action = "surface_water_map"
        action = action if action in SUPPORTED_ACTIONS else "ndvi_summary"
        dataset = self._normalize_dataset(raw_plan.get("dataset"), action)
        metadata = DATASET_ALIASES[dataset]

        if action in {"ndvi_summary", "ndvi_time_series", "ndvi_map", "cloud_filtered_composite", "create_export_task"} and metadata["kind"] != "optical":
            dataset = "sentinel2_sr"
            metadata = DATASET_ALIASES[dataset]
        if action == "chirps_precipitation_summary":
            dataset = "chirps_daily"
            metadata = DATASET_ALIASES[dataset]
        if action == "climate_time_series" and metadata["kind"] != "climate":
            dataset = "gridmet_daily"
            metadata = DATASET_ALIASES[dataset]
        if action in {"land_cover_area_summary", "land_cover_map"} and metadata["kind"] != "land_cover":
            dataset = "esa_worldcover"
            metadata = DATASET_ALIASES[dataset]
        if action == "surface_water_map" and metadata["kind"] not in {"water", "radar"}:
            dataset = "jrc_global_surface_water"
            metadata = DATASET_ALIASES[dataset]

        region = raw_plan.get("region") if isinstance(raw_plan.get("region"), dict) else {}
        region_name = str(region.get("name") or "").strip()
        input_region = self._region_from_input_dataset(input_dataset_paths)
        use_input_region = self.request_parameters.get("use_input_region")
        opt_out_input_region = (
            use_input_region is False
            or (isinstance(use_input_region, str) and use_input_region.strip().lower() in {"false", "0", "no", "off"})
        )
        prefer_input_region = bool(input_region and not opt_out_input_region)
        if prefer_input_region:
            region = input_region
            region_name = str(region.get("name") or "input vector region")
        elif isinstance(self.request_parameters.get("bbox"), list) and len(self.request_parameters.get("bbox")) == 4:
            region = {
                "type": "bbox",
                "coordinates": self.request_parameters["bbox"],
                "name": region_name or "requested bbox",
            }
            region_name = str(region.get("name") or "requested bbox")
        coords = region.get("coordinates")
        if not isinstance(coords, list) or len(coords) != 4:
            lookup = self._lookup_region_bbox(region_name)
            if lookup is None:
                geocoded_region = self._geocode_place(region_name)
                if geocoded_region is not None:
                    region = geocoded_region
                    coords = geocoded_region["coordinates"]
                else:
                    lookup = self.request_parameters.get("bbox")
                    coords = lookup
            else:
                coords = lookup
        if not isinstance(coords, list) or len(coords) != 4:
            raise ValueError(
                "The GEE plan needs a bounding box region. Ask for a named demo region such as "
                "Centre County, Pennsylvania, or provide parameters.bbox=[min_lon,min_lat,max_lon,max_lat]."
            )
        bbox = [float(value) for value in coords]
        if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
            raise ValueError("Invalid region bbox; expected [min_lon, min_lat, max_lon, max_lat].")
        if region.get("type") in {"input_vector", "geocoded_place"}:
            region["coordinates"] = bbox
        else:
            region = {"type": "bbox", "coordinates": bbox, "name": region_name or "requested bbox"}

        date_range = raw_plan.get("date_range") if isinstance(raw_plan.get("date_range"), dict) else {}
        start = str(date_range.get("start") or self.request_parameters.get("start_date") or "2024-06-01")
        end = str(date_range.get("end") or self.request_parameters.get("end_date") or "2024-06-30")
        for date_value in (start, end):
            datetime.strptime(date_value, "%Y-%m-%d")

        reducer = str(raw_plan.get("reducer") or "mean").strip().lower()
        if reducer not in {"mean", "sum", "median", "area"}:
            reducer = "mean"
        temporal_resolution = self._normalize_temporal_resolution(
            raw_plan.get("temporal_resolution"),
            action=action,
            query=query,
        )
        cloud_filter = raw_plan.get("cloud_filter") if isinstance(raw_plan.get("cloud_filter"), dict) else {}
        max_cloud = float(cloud_filter.get("max_cloud_percent") or self.request_parameters.get("max_cloud_percent") or 20)
        scale = int(raw_plan.get("scale") or self.request_parameters.get("scale") or metadata.get("scale") or 30)
        outputs = self._normalize_requested_outputs(
            raw_plan.get("outputs") if isinstance(raw_plan.get("outputs"), list) else [],
            query=query,
        )
        export = raw_plan.get("export") if isinstance(raw_plan.get("export"), dict) else {}
        if "export" in outputs:
            export["enabled"] = True
        export.setdefault("enabled", False)
        if self.request_parameters.get("export_destination"):
            export["destination"] = self.request_parameters["export_destination"]
        export.setdefault("destination", self._default_export_destination())
        variables = raw_plan.get("variables") if isinstance(raw_plan.get("variables"), list) else self.request_parameters.get("variables")
        if isinstance(variables, str):
            variables = [part.strip() for part in re.split(r"[,;]", variables) if part.strip()]
        variables = [str(item).strip() for item in variables] if isinstance(variables, list) else []
        notes = [str(note) for note in raw_plan.get("notes", [])] if isinstance(raw_plan.get("notes"), list) else []

        return GeePlan(
            action=action,
            dataset=dataset,
            region=region,
            date_range={"start": start, "end": end},
            temporal_resolution=temporal_resolution,
            reducer=reducer,
            scale=scale,
            max_cloud_percent=max(0, min(max_cloud, 100)),
            outputs=outputs,
            export=export,
            variables=variables,
            source="llm_planned",
            notes=notes,
        )

    def _initialize_ee(self) -> None:
        if self._ee_initialized:
            return
        if ee is None:
            raise RuntimeError("earthengine-api is required for Google Earth Engine Agent live execution.")
        project = os.getenv("GEE_PROJECT") or str(self.request_parameters.get("earth_engine_project") or "").strip()
        key_file = os.getenv("GEE_SERVICE_ACCOUNT_KEY") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if not project:
            raise RuntimeError("GEE_PROJECT must be configured in the GAS deployment environment.")
        if not key_file:
            raise RuntimeError("GEE_SERVICE_ACCOUNT_KEY or GOOGLE_APPLICATION_CREDENTIALS must be configured for live GEE execution.")
        key_path = Path(key_file)
        if not key_path.is_file():
            raise RuntimeError(f"Configured Earth Engine service-account key was not found: {key_path}")
        try:
            key_payload = json.loads(key_path.read_text(encoding="utf-8"))
            service_account = key_payload.get("client_email")
        except Exception as exc:
            raise RuntimeError("Could not read client_email from the configured Earth Engine key file.") from exc
        if not service_account:
            raise RuntimeError("Configured Earth Engine key file does not contain client_email.")
        credentials = ee.ServiceAccountCredentials(service_account, str(key_path))
        ee.Initialize(credentials, project=project)
        self._ee_initialized = True
        self._record_tool("initialize_earth_engine", project=project, service_account=service_account)
        self._progress(
            "earth_engine_authentication",
            f"Initialized Earth Engine with project {project} using the deployment service account.",
            project=project,
            service_account=service_account,
        )

    def _region_geometry(self, plan: GeePlan) -> Any:
        if plan.region.get("geojson"):
            return ee.Geometry(plan.region["geojson"])
        return ee.Geometry.Rectangle(plan.region["coordinates"], proj="EPSG:4326", geodesic=False)

    def _optical_collection(self, plan: GeePlan) -> Any:
        dataset = DATASET_ALIASES[plan.dataset]
        collection = (
            ee.ImageCollection(dataset["ee_id"])
            .filterBounds(self._region_geometry(plan))
            .filterDate(plan.date_range["start"], plan.date_range["end"])
            .filter(ee.Filter.lte(dataset["cloud_property"], plan.max_cloud_percent))
        )
        self._record_tool(
            "build_optical_collection",
            dataset=dataset["ee_id"],
            start=plan.date_range["start"],
            end=plan.date_range["end"],
            max_cloud_percent=plan.max_cloud_percent,
        )
        self._progress(
            "earth_engine_collection",
            f"Built the {dataset['ee_id']} image collection for {plan.date_range['start']} to {plan.date_range['end']} over the resolved region.",
            dataset=dataset["ee_id"],
            start=plan.date_range["start"],
            end=plan.date_range["end"],
            max_cloud_percent=plan.max_cloud_percent,
        )
        return collection

    def _optical_reflectance_image(self, image: Any, dataset: str) -> Any:
        if dataset.startswith("landsat"):
            bands = DATASET_ALIASES[dataset]["rgb"] + [DATASET_ALIASES[dataset]["nir"]]
            scaled = image.select(bands).multiply(0.0000275).add(-0.2)
            return image.addBands(scaled, overwrite=True)
        return image

    def _mask_optical_clouds(self, image: Any, dataset: str) -> Any:
        if dataset == "sentinel2_sr":
            scl = image.select("SCL")
            clear_mask = (
                scl.neq(3)
                .And(scl.neq(8))
                .And(scl.neq(9))
                .And(scl.neq(10))
                .And(scl.neq(11))
            )
            return image.updateMask(clear_mask)
        if dataset.startswith("landsat"):
            qa = image.select("QA_PIXEL")
            cloud_shadow = 1 << 3
            snow = 1 << 4
            cloud = 1 << 5
            clear_mask = (
                qa.bitwiseAnd(cloud_shadow).eq(0)
                .And(qa.bitwiseAnd(snow).eq(0))
                .And(qa.bitwiseAnd(cloud).eq(0))
            )
            return image.updateMask(clear_mask)
        return image

    def _median_optical_image(self, plan: GeePlan) -> Any:
        collection = self._optical_collection(plan)
        self._progress(
            "earth_engine_collection_count",
            "I am asking Earth Engine how many optical scenes match the date, region, and cloud filter.",
            dataset=plan.dataset,
            max_cloud_percent=plan.max_cloud_percent,
        )
        image_count = int(collection.size().getInfo())
        if image_count == 0 and plan.max_cloud_percent < 100:
            relaxed_plan = GeePlan(
                action=plan.action,
                dataset=plan.dataset,
                region=plan.region,
                date_range=plan.date_range,
                temporal_resolution=plan.temporal_resolution,
                reducer=plan.reducer,
                scale=plan.scale,
                max_cloud_percent=100,
                outputs=plan.outputs,
                export=plan.export,
                variables=plan.variables,
                source=plan.source,
                notes=plan.notes + ["Relaxed cloud filter to 100 percent because the initial collection was empty."],
            )
            self._record_tool(
                "relax_cloud_filter",
                original_max_cloud_percent=plan.max_cloud_percent,
                relaxed_max_cloud_percent=100,
            )
            self._progress(
                "earth_engine_collection",
                "No scenes matched the requested cloud filter, so I am retrying with scene-level filtering relaxed.",
                dataset=plan.dataset,
                original_max_cloud_percent=plan.max_cloud_percent,
            )
            collection = self._optical_collection(relaxed_plan)
            image_count = int(collection.size().getInfo())
        if image_count == 0:
            raise ValueError(
                f"Earth Engine returned no {plan.dataset} images for the requested region/date range. "
                "Try a wider date range, a larger region, or a less restrictive cloud filter."
            )
        self._progress(
            "earth_engine_composite",
            f"Earth Engine found {image_count} optical scene(s); I am creating the median composite.",
            image_count=image_count,
            dataset=plan.dataset,
        )
        image = self._optical_reflectance_image(collection.median(), plan.dataset)
        self._record_tool("create_median_composite", dataset=plan.dataset, image_count=image_count)
        return image

    def _median_visualization_image(self, plan: GeePlan) -> Any:
        relaxed_plan = GeePlan(
            action=plan.action,
            dataset=plan.dataset,
            region=plan.region,
            date_range=plan.date_range,
            temporal_resolution=plan.temporal_resolution,
            reducer=plan.reducer,
            scale=plan.scale,
            max_cloud_percent=100,
            outputs=plan.outputs,
            export=plan.export,
            variables=plan.variables,
            source=plan.source,
            notes=plan.notes,
        )
        collection = self._optical_collection(relaxed_plan).map(
            lambda image: self._mask_optical_clouds(image, plan.dataset)
        )
        self._progress(
            "earth_engine_collection_count",
            "I am counting scenes for the cloud-masked visualization composite.",
            dataset=plan.dataset,
            scene_cloud_filter_percent=100,
        )
        image_count = int(collection.size().getInfo())
        if image_count == 0:
            raise ValueError(
                f"Earth Engine returned no {plan.dataset} images for the requested region/date range. "
                "Try a wider date range, a larger region, or a different optical dataset."
            )
        self._progress(
            "earth_engine_composite",
            f"Earth Engine found {image_count} scene(s); I am building the per-pixel cloud-masked visualization composite.",
            image_count=image_count,
            dataset=plan.dataset,
        )
        image = self._optical_reflectance_image(collection.median(), plan.dataset)
        self._record_tool(
            "create_cloud_masked_visualization_composite",
            dataset=plan.dataset,
            image_count=image_count,
            scene_cloud_filter=100,
        )
        return image

    def _combined_numeric_reducer(self) -> Any:
        return (
            ee.Reducer.mean()
            .combine(ee.Reducer.minMax(), sharedInputs=True)
            .combine(ee.Reducer.stdDev(), sharedInputs=True)
        )

    def _write_json_artifact(self, query: str, fallback: str, payload: dict[str, Any]) -> str:
        path = self._output_path(query, "json", fallback)
        Path(path).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        self._record_tool("write_json_artifact", path=path)
        return path

    def _write_csv_artifact(self, query: str, fallback: str, rows: list[dict[str, Any]]) -> str:
        path = self._output_path(query, "csv", fallback)
        fieldnames = sorted({key for row in rows for key in row}) if rows else ["message"]
        with Path(path).open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        self._record_tool("write_csv_artifact", path=path, row_count=len(rows))
        return path

    def _external_url_artifact(
        self,
        *,
        url: str,
        filename: str,
        role: str,
        label: str,
        mime_type: str,
        format_name: str,
        description: str,
    ) -> dict[str, Any]:
        return {
            "kind": "downloadable_file",
            "filename": filename,
            "format": format_name,
            "mime_type": mime_type,
            "size_bytes": None,
            "url": url,
            "description": description,
            "_artifact_role": role,
            "_artifact_label": label,
            "_original_filename": filename,
        }

    def _safe_request_parameters(self) -> dict[str, Any]:
        sensitive_tokens = ("api_key", "apikey", "token", "secret", "password", "credential", "private_key")

        def redact(value: Any, key: str = "") -> Any:
            normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
            if normalized == "key" or normalized.endswith("_key") or any(token in normalized for token in sensitive_tokens):
                return "[REDACTED]"
            if isinstance(value, dict):
                return {item_key: redact(item, str(item_key)) for item_key, item in value.items()}
            if isinstance(value, list):
                return [redact(item, key) for item in value]
            return value

        return redact(self.request_parameters)

    def _artifact_role_for_path(self, artifact_path: str) -> str:
        name = Path(artifact_path).name.lower()
        suffix = Path(artifact_path).suffix.lower().lstrip(".")
        role_prefixes = [
            ("gee_validated_plan", "validated_plan"),
            ("gee_ndvi_summary", "ndvi_summary"),
            ("gee_ndvi_time_series_summary", "ndvi_time_series_summary"),
            ("gee_ndvi_time_series", "ndvi_time_series"),
            ("gee_ndvi_map_summary", "ndvi_map_summary"),
            ("gee_ndvi_map", "ndvi_interactive_map"),
            ("gee_chirps_precipitation_summary", "chirps_precipitation_summary"),
            ("gee_climate_time_series_summary", "climate_time_series_summary"),
            ("gee_climate_time_series", "climate_time_series"),
            ("gee_land_cover_area_summary", "land_cover_area_summary"),
            ("gee_land_cover_map_summary", "land_cover_map_summary"),
            ("gee_land_cover_map", "land_cover_interactive_map"),
            ("gee_surface_water_summary", "surface_water_summary"),
            ("gee_surface_water_map", "surface_water_interactive_map"),
            ("gee_surface_water_polygons", "surface_water_polygons"),
            ("gee_cloud_filtered_composite", "cloud_filtered_composite_preview"),
            ("gee_export_task", "earth_engine_export_task"),
        ]
        for prefix, role in role_prefixes:
            if name.startswith(prefix):
                return f"{role}_{suffix}_file" if suffix else f"{role}_file"
        return f"gee_artifact_{suffix}_file" if suffix else "gee_artifact_file"

    def _semantic_artifact_outputs(self, artifacts: list[str]) -> dict[str, str]:
        semantic_outputs: dict[str, str] = {}
        for artifact_path in artifacts:
            role = self._artifact_role_for_path(artifact_path)
            candidate = role
            index = 2
            while candidate in semantic_outputs:
                candidate = f"{role}_{index}"
                index += 1
            semantic_outputs[candidate] = artifact_path
        return semantic_outputs

    def _result_summary_text(self, plan: GeePlan, summary: dict[str, Any]) -> str:
        region_name = plan.region.get("name", "the requested region")
        dataset_id = DATASET_ALIASES[plan.dataset]["ee_id"]
        date_text = f"{plan.date_range['start']} to {plan.date_range['end']}"
        if plan.action == "ndvi_map":
            visualization = summary.get("earth_engine_visualization") if isinstance(summary.get("earth_engine_visualization"), dict) else {}
            preview_parts = []
            if visualization.get("thumbnail_url"):
                preview_parts.append("thumbnail artifact")
            if visualization.get("tile_fetcher_url_format"):
                preview_parts.append("map tile URL")
            preview_text = " and ".join(preview_parts) if preview_parts else "preview metadata"
            return (
                f"Created an NDVI map for {region_name} from {dataset_id} for {date_text}. "
                f"The response includes an Earth Engine {preview_text} for the actual clipped NDVI raster "
                f"and an interactive HTML map with the NDVI tile layer and analysis boundary. "
                "Use the export skill only if a durable GeoTIFF is needed."
            )
        if plan.action == "ndvi_summary":
            ndvi_stats = summary.get("ndvi") if isinstance(summary.get("ndvi"), dict) else {}
            mean_ndvi = ndvi_stats.get("NDVI_mean")
            mean_text = f" Mean NDVI is {mean_ndvi:.3f}." if isinstance(mean_ndvi, (int, float)) else ""
            return f"Computed NDVI summary statistics for {region_name} from {dataset_id} for {date_text}.{mean_text}"
        if plan.action == "ndvi_time_series":
            returned = summary.get("returned_outputs") if isinstance(summary.get("returned_outputs"), list) else []
            output_text = ", ".join(returned) if returned else "requested outputs"
            temporal_text = summary.get("temporal_resolution") or plan.temporal_resolution or "daily"
            row_label = "month(s)" if temporal_text == "monthly" else "observation date(s)"
            return (
                f"Created a {temporal_text} NDVI time series for {region_name} from {dataset_id} for {date_text}. "
                f"Returned {summary.get('row_count', 0)} {row_label} as {output_text}."
            )
        if plan.action == "cloud_filtered_composite":
            preview_available = bool(summary.get("thumbnail_url") or summary.get("tile_fetcher_url_format"))
            preview_text = "with Earth Engine visualization URLs" if preview_available else "with visualization metadata"
            return f"Created a cloud-filtered median composite preview for {region_name} from {dataset_id} for {date_text} {preview_text}."
        if plan.action == "chirps_precipitation_summary":
            return f"Summarized CHIRPS daily precipitation for {region_name} for {date_text} using {summary.get('image_count', 0)} daily image(s)."
        if plan.action == "climate_time_series":
            variables = summary.get("variables") if isinstance(summary.get("variables"), list) else []
            variable_text = ", ".join(variable.get("label", variable.get("name", "")) for variable in variables if isinstance(variable, dict))
            returned = summary.get("returned_outputs") if isinstance(summary.get("returned_outputs"), list) else []
            output_text = ", ".join(returned) if returned else "requested outputs"
            return (
                f"Created a {summary.get('temporal_resolution', 'climate')} climate time series for {region_name} "
                f"from {dataset_id} for {date_text}. Variables: {variable_text or 'requested climate variables'}. "
                f"Returned {summary.get('row_count', 0)} time step(s) as {output_text}."
            )
        if plan.action == "land_cover_area_summary":
            return f"Computed land-cover class areas for {region_name} using {dataset_id}; {len(summary.get('class_area', []))} classes were returned."
        if plan.action == "land_cover_map":
            visualization = summary.get("earth_engine_visualization") if isinstance(summary.get("earth_engine_visualization"), dict) else {}
            preview_parts = []
            if visualization.get("thumbnail_url"):
                preview_parts.append("thumbnail artifact")
            if visualization.get("tile_fetcher_url_format"):
                preview_parts.append("map tile URL")
            preview_text = " and ".join(preview_parts) if preview_parts else "preview metadata"
            return (
                f"Created a land-cover map for {region_name} using {dataset_id}. "
                f"The response includes an Earth Engine {preview_text} and an interactive HTML map with the analysis boundary."
            )
        if plan.action == "surface_water_map":
            visualization = summary.get("earth_engine_visualization") if isinstance(summary.get("earth_engine_visualization"), dict) else {}
            preview_parts = []
            if visualization.get("thumbnail_url"):
                preview_parts.append("thumbnail artifact")
            if visualization.get("tile_fetcher_url_format"):
                preview_parts.append("map tile URL")
            preview_text = " and ".join(preview_parts) if preview_parts else "preview metadata"
            area = summary.get("water_area_sq_km")
            area_text = f" Estimated mapped water area is {area:.2f} sq km." if isinstance(area, (int, float)) else ""
            return (
                f"Created a surface-water map for {region_name} using {dataset_id}. "
                f"The response includes an Earth Engine {preview_text}, an interactive HTML map, "
                f"and exposure-ready CSV/GeoJSON outputs when requested.{area_text}"
            )
        if plan.action == "create_export_task":
            status = summary.get("export_status") if isinstance(summary.get("export_status"), dict) else {}
            raster_output = summary.get("raster_output") if isinstance(summary.get("raster_output"), dict) else {}
            destination = raster_output.get("uri") or "the configured export destination"
            return f"Started an Earth Engine export task for {region_name}; task state is {status.get('state', 'unknown')} and output target is {destination}."
        return f"Executed Google Earth Engine action '{plan.action}' for {region_name} from {date_text}."

    def _normalize_climate_variables(self, plan: GeePlan) -> list[dict[str, Any]]:
        metadata = DATASET_ALIASES[plan.dataset]
        available = metadata.get("variables", {})
        requested = self.request_parameters.get("variables")
        if requested is None:
            requested = plan.variables
        if requested is None:
            requested = ["precipitation"]
        if isinstance(requested, str):
            requested = [part.strip() for part in re.split(r"[,;]", requested) if part.strip()]
        aliases = {
            "precip": "precipitation",
            "precipitation_mm": "precipitation",
            "rain": "precipitation",
            "rainfall": "precipitation",
            "temperature": "temperature",
            "temp": "temperature",
            "mean_temperature": "temperature",
            "tmean": "temperature",
            "max_temperature": "tmax",
            "maximum_temperature": "tmax",
            "tmmx": "tmax",
            "min_temperature": "tmin",
            "minimum_temperature": "tmin",
            "tmmn": "tmin",
            "humidity": "relative_humidity_max",
            "relative_humidity": "relative_humidity_max",
            "rhmax": "relative_humidity_max",
            "rhmin": "relative_humidity_min",
            "wind": "wind_speed",
            "solar": "solar_radiation",
            "radiation": "solar_radiation",
            "evapotranspiration": "pet",
            "potential_evapotranspiration": "pet",
            "actual_evapotranspiration": "aet",
            "soil": "soil_moisture",
            "soil_moisture": "soil_moisture",
            "pdsi": "drought_index",
            "drought": "drought_index",
            "vpd": "vapor_pressure_deficit",
            "vapor_pressure": "vapor_pressure",
            "snow": "snow_water_equivalent",
            "swe": "snow_water_equivalent",
        }
        normalized = []
        seen = set()
        for value in requested if isinstance(requested, list) else []:
            key = str(value).strip().lower().replace("-", "_").replace(" ", "_")
            key = aliases.get(key, key)
            if key not in available:
                continue
            if key in seen:
                continue
            seen.add(key)
            spec = dict(available[key])
            spec["name"] = key
            normalized.append(spec)
        if not normalized:
            fallback = "precipitation" if "precipitation" in available else next(iter(available))
            spec = dict(available[fallback])
            spec["name"] = fallback
            normalized.append(spec)
        return normalized

    def _apply_climate_unit_transform(self, value: Any, variable: dict[str, Any]) -> Any:
        if value is None:
            return None
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return value
        multiplier = float(variable.get("multiplier", 1))
        offset = float(variable.get("offset", 0))
        return value * multiplier + offset

    def _save_climate_chart_html(self, query: str, rows: list[dict[str, Any]], variables: list[dict[str, Any]], title: str) -> str:
        path = self._output_path(query, "html", "gee_climate_time_series")
        labels = [row.get("date") or row.get("month") or row.get("period_start") for row in rows]
        palette = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2", "#4f46e5", "#65a30d"]
        datasets = []
        for index, variable in enumerate(variables):
            name = variable["name"]
            label = f"{variable.get('label', name)} ({variable.get('unit', '')})".strip()
            datasets.append(
                {
                    "label": label,
                    "data": [row.get(name) for row in rows],
                    "borderColor": palette[index % len(palette)],
                    "backgroundColor": palette[index % len(palette)],
                    "spanGaps": True,
                    "tension": 0.18,
                    "pointRadius": 2,
                }
            )
        html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    html {{ color-scheme: light; background: #ffffff; }}
    body {{ margin: 0; font-family: Arial, sans-serif; color: #1f2937; background: #ffffff; }}
    .wrap {{ padding: 18px; }}
    h1 {{ font-size: 20px; margin: 0 0 14px; }}
    canvas {{ width: 100%; max-height: 620px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>{title}</h1>
    <canvas id="chart"></canvas>
  </div>
  <script>
    const labels = {json.dumps(labels)};
    const datasets = {json.dumps(datasets)};
    new Chart(document.getElementById('chart'), {{
      type: 'line',
      data: {{ labels, datasets }},
      options: {{
        responsive: true,
        interaction: {{ mode: 'index', intersect: false }},
        plugins: {{ legend: {{ position: 'bottom' }} }},
        scales: {{ x: {{ ticks: {{ maxTicksLimit: 12 }} }}, y: {{ beginAtZero: false }} }}
      }}
    }});
  </script>
</body>
</html>"""
        Path(path).write_text(html, encoding="utf-8")
        self._record_tool("write_climate_chart_html", path=path, row_count=len(rows), variable_count=len(variables))
        return path

    def run_ndvi_summary(self, query: str, plan: GeePlan) -> tuple[dict[str, Any], list[str]]:
        image = self._median_optical_image(plan)
        metadata = DATASET_ALIASES[plan.dataset]
        ndvi = image.normalizedDifference([metadata["nir"], metadata["red"]]).rename("NDVI")
        self._progress(
            "earth_engine_reduction",
            "I am reducing the NDVI raster over the analysis region to compute mean, min, max, and standard deviation.",
            reducer="mean_min_max_stddev",
            scale=plan.scale,
        )
        stats = ndvi.reduceRegion(
            reducer=self._combined_numeric_reducer(),
            geometry=self._region_geometry(plan),
            scale=plan.scale,
            maxPixels=1e9,
        ).getInfo()
        self._record_tool("reduce_region_ndvi", scale=plan.scale)
        summary = {
            "action": plan.action,
            "dataset": plan.dataset,
            "dataset_id": metadata["ee_id"],
            "region": plan.region,
            "date_range": plan.date_range,
            "scale_m": plan.scale,
            "ndvi": stats,
        }
        rows = [{"metric": key, "value": value} for key, value in stats.items()]
        artifacts: list[str] = []
        if self._wants_output(plan, "json"):
            artifacts.append(self._write_json_artifact(query, "gee_ndvi_summary", summary))
        if self._wants_output(plan, "csv"):
            artifacts.append(self._write_csv_artifact(query, "gee_ndvi_summary", rows))
        summary["returned_artifacts"] = self._requested_artifact_labels(plan, rows=True)
        return summary, artifacts

    def _save_line_chart_html(
        self,
        query: str,
        fallback: str,
        rows: list[dict[str, Any]],
        series: list[dict[str, str]],
        title: str,
    ) -> str:
        path = self._output_path(query, "html", fallback)
        labels = [row.get("date") or row.get("month") or row.get("period_start") for row in rows]
        palette = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2", "#4f46e5", "#65a30d"]
        datasets = []
        for index, item in enumerate(series):
            field = item["field"]
            datasets.append(
                {
                    "label": item.get("label", field),
                    "data": [row.get(field) for row in rows],
                    "borderColor": palette[index % len(palette)],
                    "backgroundColor": palette[index % len(palette)],
                    "spanGaps": True,
                    "tension": 0.18,
                    "pointRadius": 2,
                }
            )
        html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    html {{ color-scheme: light; background: #ffffff; }}
    body {{ margin: 0; font-family: Arial, sans-serif; color: #1f2937; background: #ffffff; }}
    .wrap {{ padding: 18px; }}
    h1 {{ font-size: 20px; margin: 0 0 14px; }}
    canvas {{ width: 100%; max-height: 620px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>{title}</h1>
    <canvas id="chart"></canvas>
  </div>
  <script>
    const labels = {json.dumps(labels)};
    const datasets = {json.dumps(datasets)};
    new Chart(document.getElementById('chart'), {{
      type: 'line',
      data: {{ labels, datasets }},
      options: {{
        responsive: true,
        interaction: {{ mode: 'index', intersect: false }},
        plugins: {{ legend: {{ position: 'bottom' }} }},
        scales: {{ x: {{ ticks: {{ maxTicksLimit: 12 }} }}, y: {{ beginAtZero: false }} }}
      }}
    }});
  </script>
</body>
</html>"""
        Path(path).write_text(html, encoding="utf-8")
        self._record_tool("write_line_chart_html", path=path, row_count=len(rows), series_count=len(series))
        return path

    def run_monthly_ndvi_time_series(self, query: str, plan: GeePlan) -> tuple[dict[str, Any], list[str]]:
        metadata = DATASET_ALIASES[plan.dataset]
        region = self._region_geometry(plan)
        collection = self._optical_collection(plan).map(
            lambda image: self._mask_optical_clouds(image, plan.dataset)
        )
        self._progress(
            "earth_engine_collection_count",
            "I am counting available scenes before computing the monthly NDVI time series.",
            dataset=plan.dataset,
            scene_cloud_filter_percent=plan.max_cloud_percent,
        )
        image_count = int(collection.size().getInfo())
        if image_count == 0:
            raise ValueError(
                f"Earth Engine returned no {plan.dataset} images for the requested region/date range. "
                "Try a wider date range, a larger region, or a less restrictive cloud filter."
            )

        start_date = ee.Date(plan.date_range["start"])
        end_exclusive = ee.Date(plan.date_range["end"]).advance(1, "day")
        month_offsets = ee.List.sequence(0, end_exclusive.difference(start_date, "month").ceil().subtract(1))

        def month_to_feature(offset):
            month_start = start_date.advance(offset, "month")
            next_month = month_start.advance(1, "month")
            month_end = ee.Date(
                ee.Algorithms.If(
                    next_month.millis().lt(end_exclusive.millis()),
                    next_month,
                    end_exclusive,
                )
            )
            monthly_collection = collection.filterDate(month_start, month_end)
            monthly_count = monthly_collection.size()
            reflectance = self._optical_reflectance_image(monthly_collection.median(), plan.dataset)
            ndvi = reflectance.normalizedDifference([metadata["nir"], metadata["red"]]).rename("NDVI")
            stats = ee.Dictionary(
                ee.Algorithms.If(
                    monthly_count.gt(0),
                    ndvi.reduceRegion(
                        reducer=self._combined_numeric_reducer(),
                        geometry=region,
                        scale=plan.scale,
                        maxPixels=1e9,
                    ),
                    ee.Dictionary({}),
                )
            )
            return ee.Feature(
                None,
                stats.set("month", month_start.format("YYYY-MM"))
                .set("period_start", month_start.format("YYYY-MM-dd"))
                .set("period_end", month_end.advance(-1, "day").format("YYYY-MM-dd"))
                .set("image_count", monthly_count),
            )

        self._progress(
            "earth_engine_time_series",
            "I am asking Earth Engine to compute one monthly cloud-masked NDVI statistic per monthly composite.",
            start=plan.date_range["start"],
            end=plan.date_range["end"],
            scale=plan.scale,
            temporal_resolution="monthly",
        )
        feature_payload = ee.FeatureCollection(month_offsets.map(month_to_feature)).filter(
            ee.Filter.gt("image_count", 0)
        ).getInfo()
        rows = []
        for feature in feature_payload.get("features", []) if isinstance(feature_payload, dict) else []:
            props = feature.get("properties", {}) if isinstance(feature.get("properties"), dict) else {}
            row = {
                "month": props.get("month"),
                "period_start": props.get("period_start"),
                "period_end": props.get("period_end"),
                "image_count": props.get("image_count"),
                "ndvi_mean": props.get("NDVI_mean"),
                "ndvi_min": props.get("NDVI_min"),
                "ndvi_max": props.get("NDVI_max"),
                "ndvi_stddev": props.get("NDVI_stdDev"),
            }
            if row["month"] and any(row[key] is not None for key in ("ndvi_mean", "ndvi_min", "ndvi_max")):
                rows.append(row)
        rows.sort(key=lambda row: str(row.get("month")))
        values = [row["ndvi_mean"] for row in rows if isinstance(row.get("ndvi_mean"), (int, float))]
        summary = {
            "action": plan.action,
            "dataset": plan.dataset,
            "dataset_id": metadata["ee_id"],
            "region": plan.region,
            "date_range": plan.date_range,
            "scale_m": plan.scale,
            "image_count": image_count,
            "row_count": len(rows),
            "temporal_resolution": "monthly",
            "summary_statistics": {
                "ndvi_mean": {
                    "count": len(values),
                    "mean": sum(values) / len(values) if values else None,
                    "min": min(values) if values else None,
                    "max": max(values) if values else None,
                }
            },
            "cloud_handling": {
                "scene_cloud_filter_percent": plan.max_cloud_percent,
                "pixel_mask": "Sentinel-2 SCL or Landsat QA_PIXEL cloud/shadow/snow mask before monthly median NDVI compositing.",
            },
            "method": "For each calendar month, the agent builds one cloud-masked monthly median composite, computes NDVI, and runs one region reduction.",
            "time_series_preview": rows[:10],
        }
        self._record_tool("create_monthly_ndvi_time_series", image_count=image_count, row_count=len(rows), scale=plan.scale)
        returned_outputs = []
        wants_csv = self._wants_output(plan, "csv")
        wants_html = self._wants_output(plan, "html")
        wants_json = self._wants_output(plan, "json")
        if wants_json:
            returned_outputs.append("JSON report artifact")
        if wants_csv:
            returned_outputs.append("CSV table")
        if wants_html:
            returned_outputs.append("HTML line chart")
        summary["returned_outputs"] = returned_outputs
        artifacts = []
        if wants_json:
            artifacts.append(self._write_json_artifact(query, "gee_ndvi_time_series_summary", summary))
        if wants_csv:
            artifacts.append(self._write_csv_artifact(query, "gee_ndvi_time_series", rows))
        if wants_html:
            artifacts.append(
                self._save_line_chart_html(
                    query,
                    "gee_ndvi_time_series",
                    rows,
                    [{"field": "ndvi_mean", "label": "Mean NDVI"}],
                    f"Monthly NDVI Time Series: {plan.region.get('name', 'Requested region')}",
                )
            )
        return summary, artifacts

    def run_ndvi_time_series(self, query: str, plan: GeePlan) -> tuple[dict[str, Any], list[str]]:
        if plan.temporal_resolution == "monthly":
            return self.run_monthly_ndvi_time_series(query, plan)

        metadata = DATASET_ALIASES[plan.dataset]
        region = self._region_geometry(plan)
        relaxed_plan = GeePlan(
            action=plan.action,
            dataset=plan.dataset,
            region=plan.region,
            date_range=plan.date_range,
            temporal_resolution=plan.temporal_resolution,
            reducer=plan.reducer,
            scale=plan.scale,
            max_cloud_percent=100,
            outputs=plan.outputs,
            export=plan.export,
            variables=plan.variables,
            source=plan.source,
            notes=plan.notes,
        )
        collection = self._optical_collection(relaxed_plan).map(
            lambda image: self._mask_optical_clouds(image, plan.dataset)
        )
        self._progress(
            "earth_engine_collection_count",
            "I am counting available scenes before computing the daily NDVI time series.",
            dataset=plan.dataset,
            scene_cloud_filter_percent=100,
        )
        image_count = int(collection.size().getInfo())
        if image_count == 0:
            raise ValueError(
                f"Earth Engine returned no {plan.dataset} images for the requested region/date range. "
                "Try a wider date range, a larger region, or a different optical dataset."
            )

        start_date = ee.Date(plan.date_range["start"])
        end_date = ee.Date(plan.date_range["end"])
        day_offsets = ee.List.sequence(0, end_date.difference(start_date, "day").subtract(1))

        def day_to_feature(offset):
            day = start_date.advance(offset, "day")
            next_day = day.advance(1, "day")
            daily_collection = collection.filterDate(day, next_day)
            daily_count = daily_collection.size()
            reflectance = self._optical_reflectance_image(daily_collection.median(), plan.dataset)
            ndvi = reflectance.normalizedDifference([metadata["nir"], metadata["red"]]).rename("NDVI")
            stats = ee.Dictionary(
                ee.Algorithms.If(
                    daily_count.gt(0),
                    ndvi.reduceRegion(
                        reducer=self._combined_numeric_reducer(),
                        geometry=region,
                        scale=plan.scale,
                        maxPixels=1e9,
                    ),
                    ee.Dictionary({}),
                )
            )
            return ee.Feature(
                None,
                stats.set("date", day.format("YYYY-MM-dd")).set("image_count", daily_count),
            )

        self._progress(
            "earth_engine_time_series",
            "I am asking Earth Engine to compute daily cloud-masked NDVI statistics for each date with imagery.",
            start=plan.date_range["start"],
            end=plan.date_range["end"],
            scale=plan.scale,
        )
        feature_payload = ee.FeatureCollection(day_offsets.map(day_to_feature)).filter(
            ee.Filter.gt("image_count", 0)
        ).getInfo()
        rows = []
        for feature in feature_payload.get("features", []) if isinstance(feature_payload, dict) else []:
            props = feature.get("properties", {}) if isinstance(feature.get("properties"), dict) else {}
            row = {
                "date": props.get("date"),
                "image_count": props.get("image_count"),
                "ndvi_mean": props.get("NDVI_mean"),
                "ndvi_min": props.get("NDVI_min"),
                "ndvi_max": props.get("NDVI_max"),
                "ndvi_stddev": props.get("NDVI_stdDev"),
            }
            if row["date"] and any(row[key] is not None for key in ("ndvi_mean", "ndvi_min", "ndvi_max")):
                rows.append(row)
        rows.sort(key=lambda row: str(row.get("date")))
        values = [row["ndvi_mean"] for row in rows if isinstance(row.get("ndvi_mean"), (int, float))]
        summary = {
            "action": plan.action,
            "dataset": plan.dataset,
            "dataset_id": metadata["ee_id"],
            "region": plan.region,
            "date_range": plan.date_range,
            "scale_m": plan.scale,
            "image_count": image_count,
            "row_count": len(rows),
            "temporal_resolution": "daily",
            "summary_statistics": {
                "ndvi_mean": {
                    "count": len(values),
                    "mean": sum(values) / len(values) if values else None,
                    "min": min(values) if values else None,
                    "max": max(values) if values else None,
                }
            },
            "cloud_handling": {
                "scene_cloud_filter_percent": 100,
                "pixel_mask": "Sentinel-2 SCL or Landsat QA_PIXEL cloud/shadow/snow mask before daily median NDVI reduction.",
                "requested_max_cloud_percent": plan.max_cloud_percent,
            },
            "method": "For each date with imagery, the agent builds a cloud-masked daily median image, computes NDVI, and reduces the clipped raster over the analysis region.",
            "time_series_preview": rows[:10],
        }
        self._record_tool("create_ndvi_time_series", image_count=image_count, row_count=len(rows), scale=plan.scale)
        returned_outputs = []
        wants_csv = self._wants_output(plan, "csv")
        wants_html = self._wants_output(plan, "html")
        wants_json = self._wants_output(plan, "json")
        if wants_json:
            returned_outputs.append("JSON report artifact")
        if wants_csv:
            returned_outputs.append("CSV table")
        if wants_html:
            returned_outputs.append("HTML line chart")
        summary["returned_outputs"] = returned_outputs
        artifacts = []
        if wants_json:
            artifacts.append(self._write_json_artifact(query, "gee_ndvi_time_series_summary", summary))
        if wants_csv:
            artifacts.append(self._write_csv_artifact(query, "gee_ndvi_time_series", rows))
        if wants_html:
            artifacts.append(
                self._save_line_chart_html(
                    query,
                    "gee_ndvi_time_series",
                    rows,
                    [{"field": "ndvi_mean", "label": "Mean NDVI"}],
                    f"NDVI Time Series: {plan.region.get('name', 'Requested region')}",
                )
            )
        return summary, artifacts

    def _ndvi_image(self, plan: GeePlan) -> Any:
        image = self._median_optical_image(plan)
        metadata = DATASET_ALIASES[plan.dataset]
        return image.normalizedDifference([metadata["nir"], metadata["red"]]).rename("NDVI")

    def _ndvi_visualization_image(self, plan: GeePlan) -> Any:
        image = self._median_visualization_image(plan)
        metadata = DATASET_ALIASES[plan.dataset]
        return image.normalizedDifference([metadata["nir"], metadata["red"]]).rename("NDVI")

    def _save_ndvi_folium_map(self, query: str, plan: GeePlan, visualization: dict[str, Any]) -> str:
        import folium
        from branca.colormap import linear

        minx, miny, maxx, maxy = plan.region["coordinates"]
        center = [(miny + maxy) / 2, (minx + maxx) / 2]
        fmap = folium.Map(location=center, zoom_start=9, tiles="CartoDB positron")
        tile_url = visualization.get("tile_fetcher_url_format")
        if tile_url:
            folium.TileLayer(
                tiles=tile_url,
                name="Earth Engine NDVI raster",
                attr="Google Earth Engine",
                overlay=True,
                control=True,
                opacity=0.78,
            ).add_to(fmap)
        if plan.region.get("geojson"):
            folium.GeoJson(
                {"type": "Feature", "properties": {"name": plan.region.get("name")}, "geometry": plan.region["geojson"]},
                name="Analysis boundary",
                style_function=lambda feature: {"fillOpacity": 0, "color": "#1f2937", "weight": 2},
            ).add_to(fmap)
        fmap.get_root().header.add_child(
            folium.Element("<style>html, body { color-scheme: light; background: #ffffff !important; color: #1f2937; }</style>")
        )
        colormap = linear.YlGn_09.scale(-0.2, 0.9)
        colormap.caption = "NDVI"
        colormap.add_to(fmap)
        folium.LayerControl().add_to(fmap)
        path = self._output_path(query, "html", "gee_ndvi_map")
        fmap.save(path)
        self._record_tool("write_ndvi_folium_map", path=path)
        return path

    def _earth_engine_visualization_urls(
        self,
        image: Any,
        plan: GeePlan,
        vis_params: dict[str, Any],
        visualization_name: str = "raster",
    ) -> dict[str, Any]:
        region = self._region_geometry(plan)
        result: dict[str, Any] = {
            "thumbnail_url": None,
            "map_id": None,
            "tile_fetcher_url_format": None,
            "errors": {},
            "note": "Earth Engine visualization URLs are temporary and intended for preview, not durable raster delivery.",
        }
        try:
            self._progress(
                "earth_engine_visualization",
                "I am requesting an Earth Engine thumbnail URL for the raster preview.",
                visualization=visualization_name,
            )
            result["thumbnail_url"] = image.getThumbURL({"region": region, "dimensions": 1024, **vis_params})
            self._record_tool("create_thumbnail_url", visualization=visualization_name)
        except Exception as exc:
            result["errors"]["thumbnail"] = str(exc)
            self._record_tool("create_thumbnail_url_failed", visualization=visualization_name, error=str(exc))
        try:
            self._progress(
                "earth_engine_visualization",
                "I am requesting an Earth Engine map tile URL for interactive visualization.",
                visualization=visualization_name,
            )
            map_info = image.getMapId(vis_params)
            result["map_id"] = map_info.get("mapid")
            tile_fetcher = map_info.get("tile_fetcher")
            if tile_fetcher is not None:
                result["tile_fetcher_url_format"] = getattr(tile_fetcher, "url_format", None)
            self._record_tool("create_map_tile_url", visualization=visualization_name, map_id=result["map_id"])
        except Exception as exc:
            result["errors"]["map_tiles"] = str(exc)
            self._record_tool("create_map_tile_url_failed", visualization=visualization_name, error=str(exc))
        return result

    def run_ndvi_map(self, query: str, plan: GeePlan) -> tuple[dict[str, Any], list[str]]:
        region = self._region_geometry(plan)
        ndvi = self._ndvi_visualization_image(plan).clip(region)
        vis_params = {"min": -0.2, "max": 0.9, "palette": ["#7f3b08", "#f6e8c3", "#c7eae5", "#35978f", "#01665e"]}
        visualization = self._earth_engine_visualization_urls(ndvi, plan, vis_params, visualization_name="ndvi")
        self._progress(
            "earth_engine_visualization",
            "Earth Engine returned NDVI visualization metadata; I am preparing the requested map and preview artifacts.",
            has_thumbnail=bool(visualization.get("thumbnail_url")),
            has_tile_url=bool(visualization.get("tile_fetcher_url_format")),
        )
        summary = {
            "action": plan.action,
            "dataset": plan.dataset,
            "dataset_id": DATASET_ALIASES[plan.dataset]["ee_id"],
            "region": plan.region,
            "date_range": plan.date_range,
            "scale_m": plan.scale,
            "visualization": vis_params,
            "cloud_handling": {
                "scene_cloud_filter_percent": 100,
                "pixel_mask": "Sentinel-2 SCL or Landsat QA_PIXEL cloud/shadow/snow mask before median compositing.",
                "reason": "NDVI map previews prioritize full-region visual coverage; strict scene-level cloud metadata can drop adjacent Sentinel/Landsat tiles.",
                "requested_max_cloud_percent": plan.max_cloud_percent,
            },
            "preferred_visualization": "earth_engine_thumbnail_or_map_tiles",
            "earth_engine_visualization": visualization,
            "raster_delivery": {
                "preview": "Use the ndvi_thumbnail_png_url artifact or earth_engine_visualization.tile_fetcher_url_format.",
                "durable_geotiff": "Call create_export_task if the preview is useful and a GeoTIFF is needed.",
            },
        }
        artifacts = []
        if self._wants_output(plan, "json"):
            artifacts.append(self._write_json_artifact(query, "gee_ndvi_map_summary", summary))
        if self._wants_output(plan, "html"):
            artifacts.append(self._save_ndvi_folium_map(query, plan, visualization))
        summary["returned_artifacts"] = self._requested_artifact_labels(plan, html_map=True)
        return summary, artifacts

    def run_chirps_precipitation_summary(self, query: str, plan: GeePlan) -> tuple[dict[str, Any], list[str]]:
        metadata = DATASET_ALIASES["chirps_daily"]
        collection = (
            ee.ImageCollection(metadata["ee_id"])
            .filterBounds(self._region_geometry(plan))
            .filterDate(plan.date_range["start"], plan.date_range["end"])
            .select(metadata["band"])
        )
        total = collection.sum().rename("precipitation_total_mm")
        self._progress(
            "earth_engine_reduction",
            "I am reducing CHIRPS daily precipitation totals over the analysis region.",
            dataset=metadata["ee_id"],
            scale=plan.scale,
        )
        mean_total = total.reduceRegion(
            reducer=self._combined_numeric_reducer(),
            geometry=self._region_geometry(plan),
            scale=plan.scale,
            maxPixels=1e9,
        ).getInfo()
        self._progress(
            "earth_engine_collection_count",
            "I am counting CHIRPS daily images included in the precipitation summary.",
            dataset=metadata["ee_id"],
        )
        image_count = collection.size().getInfo()
        self._record_tool("summarize_chirps_precipitation", image_count=image_count, scale=plan.scale)
        summary = {
            "action": plan.action,
            "dataset": "chirps_daily",
            "dataset_id": metadata["ee_id"],
            "region": plan.region,
            "date_range": plan.date_range,
            "scale_m": plan.scale,
            "image_count": image_count,
            "precipitation_total_mm": mean_total,
        }
        rows = [{"metric": key, "value": value} for key, value in mean_total.items()]
        rows.append({"metric": "image_count", "value": image_count})
        artifacts: list[str] = []
        if self._wants_output(plan, "json"):
            artifacts.append(self._write_json_artifact(query, "gee_chirps_precipitation_summary", summary))
        if self._wants_output(plan, "csv"):
            artifacts.append(self._write_csv_artifact(query, "gee_chirps_precipitation_summary", rows))
        summary["returned_artifacts"] = self._requested_artifact_labels(plan, rows=True)
        return summary, artifacts

    def run_climate_time_series(self, query: str, plan: GeePlan) -> tuple[dict[str, Any], list[str]]:
        metadata = DATASET_ALIASES[plan.dataset]
        variables = self._normalize_climate_variables(plan)
        bands = [variable["band"] for variable in variables]
        collection = (
            ee.ImageCollection(metadata["ee_id"])
            .filterBounds(self._region_geometry(plan))
            .filterDate(plan.date_range["start"], plan.date_range["end"])
            .select(bands)
        )
        self._progress(
            "earth_engine_collection_count",
            "I am counting climate/environmental images for the requested variables and date range.",
            dataset=metadata["ee_id"],
            variables=[variable["name"] for variable in variables],
        )
        image_count = int(collection.size().getInfo())
        region = self._region_geometry(plan)

        def image_to_feature(image):
            stats = image.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=region,
                scale=plan.scale,
                maxPixels=1e9,
            )
            return ee.Feature(None, stats.set("date", image.date().format("YYYY-MM-dd")))

        self._progress(
            "earth_engine_time_series",
            "I am reducing each climate/environmental image over the analysis region to build the time series.",
            dataset=metadata["ee_id"],
            image_count=image_count,
            scale=plan.scale,
        )
        feature_payload = collection.map(image_to_feature).getInfo()
        rows: list[dict[str, Any]] = []
        for feature in feature_payload.get("features", []) if isinstance(feature_payload, dict) else []:
            properties = feature.get("properties", {}) if isinstance(feature.get("properties"), dict) else {}
            row: dict[str, Any] = {"date": properties.get("date")}
            for variable in variables:
                row[variable["name"]] = self._apply_climate_unit_transform(properties.get(variable["band"]), variable)
            if row.get("date"):
                rows.append(row)
        rows.sort(key=lambda row: str(row.get("date")))
        summary_stats = {}
        for variable in variables:
            values = [row.get(variable["name"]) for row in rows if isinstance(row.get(variable["name"]), (int, float))]
            summary_stats[variable["name"]] = {
                "count": len(values),
                "mean": sum(values) / len(values) if values else None,
                "min": min(values) if values else None,
                "max": max(values) if values else None,
                "sum": sum(values) if values else None,
                "unit": variable.get("unit"),
            }
        variable_metadata = [
            {
                "name": variable["name"],
                "band": variable["band"],
                "label": variable.get("label", variable["name"]),
                "unit": variable.get("unit"),
            }
            for variable in variables
        ]
        self._record_tool(
            "create_climate_time_series",
            dataset=metadata["ee_id"],
            image_count=image_count,
            row_count=len(rows),
            variables=[variable["name"] for variable in variables],
        )
        title = f"Climate Time Series: {plan.region.get('name', 'Requested region')}"
        summary = {
            "action": plan.action,
            "dataset": plan.dataset,
            "dataset_id": metadata["ee_id"],
            "temporal_resolution": metadata.get("temporal_resolution"),
            "region": plan.region,
            "date_range": plan.date_range,
            "scale_m": plan.scale,
            "variables": variable_metadata,
            "image_count": image_count,
            "row_count": len(rows),
            "summary_statistics": summary_stats,
            "time_series_preview": rows[:10],
        }
        returned_outputs = []
        wants_csv = self._wants_output(plan, "csv")
        wants_html = self._wants_output(plan, "html")
        wants_json = self._wants_output(plan, "json")
        if wants_json:
            returned_outputs.append("JSON report artifact")
        if wants_csv:
            returned_outputs.append("CSV table")
        if wants_html:
            returned_outputs.append("HTML line chart")
        summary["returned_outputs"] = returned_outputs
        artifacts = []
        if wants_json:
            artifacts.append(self._write_json_artifact(query, "gee_climate_time_series_summary", summary))
        if wants_csv:
            artifacts.append(self._write_csv_artifact(query, "gee_climate_time_series", rows))
        if wants_html:
            artifacts.append(self._save_climate_chart_html(query, rows, variable_metadata, title))
        return summary, artifacts

    def _land_cover_label_image(self, plan: GeePlan) -> Any:
        metadata = DATASET_ALIASES[plan.dataset]
        region = self._region_geometry(plan)
        if plan.dataset == "dynamic_world":
            return (
                ee.ImageCollection(metadata["ee_id"])
                .filterBounds(region)
                .filterDate(plan.date_range["start"], plan.date_range["end"])
                .select(metadata["band"])
                .mode()
            )
        return ee.ImageCollection(metadata["ee_id"]).first().select(metadata["band"])

    def _land_cover_visualization_image(self, plan: GeePlan) -> Any:
        palette = LAND_COVER_PALETTES[plan.dataset]
        label_image = self._land_cover_label_image(plan)
        return label_image.remap(
            palette["class_values"],
            list(range(len(palette["class_values"]))),
        ).rename("land_cover")

    def _land_cover_legend(self, dataset: str) -> list[dict[str, Any]]:
        palette = LAND_COVER_PALETTES[dataset]
        labels = LAND_COVER_LABELS.get(dataset, {})
        return [
            {
                "class_id": class_id,
                "class_name": labels.get(class_id, f"Class {class_id}"),
                "color": palette["palette"][index],
                "visualization_value": index,
            }
            for index, class_id in enumerate(palette["class_values"])
        ]

    def run_land_cover_area_summary(self, query: str, plan: GeePlan) -> tuple[dict[str, Any], list[str]]:
        metadata = DATASET_ALIASES[plan.dataset]
        region = self._region_geometry(plan)
        label_image = self._land_cover_label_image(plan)
        area_image = ee.Image.pixelArea().divide(1_000_000).addBands(label_image.rename("class"))
        self._progress(
            "earth_engine_reduction",
            "I am summing pixel area by land-cover class over the analysis region.",
            dataset=metadata["ee_id"],
            scale=plan.scale,
        )
        grouped = area_image.reduceRegion(
            reducer=ee.Reducer.sum().group(groupField=1, groupName="class"),
            geometry=region,
            scale=plan.scale,
            maxPixels=1e9,
        ).getInfo()
        classes = LAND_COVER_LABELS.get(plan.dataset, {})
        rows = []
        for item in grouped.get("groups", []) if isinstance(grouped, dict) else []:
            class_id = int(item.get("class"))
            rows.append(
                {
                    "class_id": class_id,
                    "class_name": classes.get(class_id, f"Class {class_id}"),
                    "area_sq_km": item.get("sum"),
                }
            )
        self._record_tool("summarize_land_cover_area", class_count=len(rows), scale=plan.scale)
        summary = {
            "action": plan.action,
            "dataset": plan.dataset,
            "dataset_id": metadata["ee_id"],
            "region": plan.region,
            "date_range": plan.date_range if plan.dataset == "dynamic_world" else None,
            "scale_m": plan.scale,
            "class_area": rows,
        }
        artifacts: list[str] = []
        if self._wants_output(plan, "json"):
            artifacts.append(self._write_json_artifact(query, "gee_land_cover_area_summary", summary))
        if self._wants_output(plan, "csv"):
            artifacts.append(self._write_csv_artifact(query, "gee_land_cover_area_summary", rows))
        summary["returned_artifacts"] = self._requested_artifact_labels(plan, rows=True)
        return summary, artifacts

    def _save_land_cover_folium_map(self, query: str, plan: GeePlan, visualization: dict[str, Any]) -> str:
        import folium

        minx, miny, maxx, maxy = plan.region["coordinates"]
        center = [(miny + maxy) / 2, (minx + maxx) / 2]
        fmap = folium.Map(location=center, zoom_start=9, tiles="CartoDB positron")
        tile_url = visualization.get("tile_fetcher_url_format")
        if tile_url:
            folium.TileLayer(
                tiles=tile_url,
                name="Earth Engine land-cover raster",
                attr="Google Earth Engine",
                overlay=True,
                control=True,
                opacity=0.82,
            ).add_to(fmap)
        if plan.region.get("geojson"):
            folium.GeoJson(
                {"type": "Feature", "properties": {"name": plan.region.get("name")}, "geometry": plan.region["geojson"]},
                name="Analysis boundary",
                style_function=lambda feature: {"fillOpacity": 0, "color": "#1f2937", "weight": 2},
            ).add_to(fmap)
        fmap.get_root().header.add_child(
            folium.Element("<style>html, body { color-scheme: light; background: #ffffff !important; color: #1f2937; }</style>")
        )
        legend_items = []
        for item in self._land_cover_legend(plan.dataset):
            legend_items.append(
                f"<div><span style='display:inline-block;width:12px;height:12px;background:{item['color']};"
                f"margin-right:6px;border:1px solid #666;'></span>{item['class_id']} - {item['class_name']}</div>"
            )
        legend_html = (
            "<div style='position: fixed; bottom: 24px; left: 24px; z-index: 9999; background: white; "
            "padding: 10px 12px; border: 1px solid #999; font-size: 12px; max-height: 260px; overflow: auto;'>"
            "<strong>Land cover</strong>"
            + "".join(legend_items)
            + "</div>"
        )
        fmap.get_root().html.add_child(folium.Element(legend_html))
        folium.LayerControl().add_to(fmap)
        path = self._output_path(query, "html", "gee_land_cover_map")
        fmap.save(path)
        self._record_tool("write_land_cover_folium_map", path=path)
        return path

    def _save_composite_preview_html(self, query: str, plan: GeePlan, summary: dict[str, Any]) -> str:
        import folium

        path = self._output_path(query, "html", "gee_cloud_filtered_composite")
        region_name = html.escape(str(plan.region.get("name", "Requested region")))
        dataset_id = html.escape(str(summary.get("dataset_id", DATASET_ALIASES[plan.dataset]["ee_id"])))
        date_text = html.escape(f"{plan.date_range['start']} to {plan.date_range['end']}")
        thumbnail_url = summary.get("thumbnail_url")
        tile_url = summary.get("tile_fetcher_url_format")

        if tile_url:
            minx, miny, maxx, maxy = plan.region["coordinates"]
            center = [(miny + maxy) / 2, (minx + maxx) / 2]
            fmap = folium.Map(location=center, zoom_start=11, tiles="CartoDB positron")
            folium.TileLayer(
                tiles=str(tile_url),
                name="Earth Engine true-color composite",
                attr="Google Earth Engine",
                overlay=True,
                control=True,
                opacity=0.9,
            ).add_to(fmap)
            if plan.region.get("geojson"):
                folium.GeoJson(
                    {"type": "Feature", "properties": {"name": plan.region.get("name")}, "geometry": plan.region["geojson"]},
                    name="Analysis boundary",
                    style_function=lambda feature: {"fillOpacity": 0, "color": "#1f2937", "weight": 2},
                ).add_to(fmap)
            fmap.fit_bounds([[miny, minx], [maxy, maxx]])
            fmap.get_root().header.add_child(
                folium.Element("<style>html, body { color-scheme: light; background: #ffffff !important; color: #1f2937; }</style>")
            )
            title_html = (
                "<div style='position: fixed; top: 12px; left: 56px; z-index: 9999; background: white; "
                "padding: 8px 10px; border: 1px solid #cbd5e1; font-family: Arial, sans-serif; "
                "font-size: 13px; color: #1f2937;'>"
                f"<strong>Earth Engine Composite Preview</strong><br>{region_name} | {dataset_id} | {date_text}</div>"
            )
            fmap.get_root().html.add_child(folium.Element(title_html))
            folium.LayerControl().add_to(fmap)
            fmap.save(path)
            self._record_tool("write_composite_folium_map", path=path, has_tile_url=True)
            return path

        image_html = ""
        if thumbnail_url:
            image_html = (
                f'<img src="{html.escape(str(thumbnail_url), quote=True)}" '
                'alt="Earth Engine composite preview" />'
            )
        else:
            image_html = "<p class=\"empty\">Earth Engine did not return a thumbnail or map tile URL for this request.</p>"
        html_text = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Earth Engine Composite Preview</title>
  <style>
    html {{ color-scheme: light; background: #ffffff; }}
    body {{ margin: 0; font-family: Arial, sans-serif; color: #1f2937; background: #f8fafc; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 22px; }}
    h1 {{ font-size: 22px; margin: 0 0 6px; }}
    p {{ margin: 0 0 14px; color: #4b5563; }}
    img {{ display: block; max-width: 100%; border: 1px solid #cbd5e1; background: #111827; }}
    section {{ margin-top: 18px; }}
    h2 {{ font-size: 15px; margin: 0 0 8px; }}
    code {{ display: block; white-space: pre-wrap; overflow-wrap: anywhere; padding: 10px; background: #e5e7eb; }}
    .empty {{ padding: 18px; border: 1px solid #cbd5e1; background: white; }}
  </style>
</head>
<body>
  <main>
    <h1>Earth Engine Composite Preview</h1>
    <p>{region_name} | {dataset_id} | {date_text}</p>
    {image_html}
  </main>
</body>
</html>"""
        Path(path).write_text(html_text, encoding="utf-8")
        self._record_tool("write_composite_preview_html", path=path, has_thumbnail=bool(thumbnail_url))
        return path

    def run_land_cover_map(self, query: str, plan: GeePlan) -> tuple[dict[str, Any], list[str]]:
        metadata = DATASET_ALIASES[plan.dataset]
        region = self._region_geometry(plan)
        image = self._land_cover_visualization_image(plan).clip(region)
        palette = LAND_COVER_PALETTES[plan.dataset]["palette"]
        vis_params = {"min": 0, "max": len(palette) - 1, "palette": palette}
        visualization = self._earth_engine_visualization_urls(
            image,
            plan,
            vis_params,
            visualization_name="land_cover",
        )
        self._progress(
            "earth_engine_visualization",
            "Earth Engine returned land-cover visualization metadata; I am preparing the requested map and preview artifacts.",
            has_thumbnail=bool(visualization.get("thumbnail_url")),
            has_tile_url=bool(visualization.get("tile_fetcher_url_format")),
        )
        summary = {
            "action": plan.action,
            "dataset": plan.dataset,
            "dataset_id": metadata["ee_id"],
            "region": plan.region,
            "date_range": plan.date_range if plan.dataset == "dynamic_world" else None,
            "scale_m": plan.scale,
            "visualization": vis_params,
            "legend": self._land_cover_legend(plan.dataset),
            "preferred_visualization": "earth_engine_thumbnail_or_map_tiles",
            "earth_engine_visualization": visualization,
            "raster_delivery": {
                "preview": "Use the land_cover_thumbnail_png_url artifact or earth_engine_visualization.tile_fetcher_url_format.",
                "durable_geotiff": "Call create_export_task if a durable GeoTIFF is needed.",
            },
        }
        self._record_tool("create_land_cover_map", dataset=metadata["ee_id"], scale=plan.scale)
        artifacts: list[str] = []
        if self._wants_output(plan, "json"):
            artifacts.append(self._write_json_artifact(query, "gee_land_cover_map_summary", summary))
        if self._wants_output(plan, "html"):
            artifacts.append(self._save_land_cover_folium_map(query, plan, visualization))
        summary["returned_artifacts"] = self._requested_artifact_labels(plan, html_map=True)
        return summary, artifacts

    def _write_geojson_artifact(self, query: str, fallback: str, payload: dict[str, Any]) -> str:
        path = self._output_path(query, "geojson", fallback)
        Path(path).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        self._record_tool("write_geojson_artifact", path=path)
        return path

    def _surface_water_images(self, plan: GeePlan) -> tuple[Any, Any, dict[str, Any]]:
        metadata = DATASET_ALIASES[plan.dataset]
        region = self._region_geometry(plan)
        if plan.dataset == "sentinel1_grd":
            threshold = float(self.request_parameters.get("sentinel1_water_threshold_db", -17))
            collection = (
                ee.ImageCollection(metadata["ee_id"])
                .filterBounds(region)
                .filterDate(plan.date_range["start"], plan.date_range["end"])
                .filter(ee.Filter.eq("instrumentMode", "IW"))
                .filter(ee.Filter.listContains("transmitterReceiverPolarisation", metadata["band"]))
                .select(metadata["band"])
            )
            self._progress(
                "earth_engine_collection_count",
                "I am counting Sentinel-1 SAR scenes before thresholding recent flood/surface water.",
                dataset=metadata["ee_id"],
                polarization=metadata["band"],
            )
            image_count = int(collection.size().getInfo())
            if image_count == 0:
                raise ValueError(
                    "Earth Engine returned no Sentinel-1 VV images for the requested region/date range. "
                    "Try a wider recent date range or JRC Global Surface Water for long-term occurrence."
                )
            radar = collection.median().clip(region)
            water_mask = radar.lt(threshold).selfMask().rename("water_mask")
            self._record_tool(
                "detect_sentinel1_water",
                dataset=metadata["ee_id"],
                image_count=image_count,
                threshold_db=threshold,
                scale=plan.scale,
            )
            return water_mask, radar.rename("sentinel1_vv_db"), {
                "method": "Sentinel-1 VV median backscatter threshold; lower backscatter is mapped as open water.",
                "threshold_db": threshold,
                "image_count": image_count,
                "water_definition": f"VV backscatter < {threshold} dB",
            }

        threshold = float(self.request_parameters.get("water_occurrence_threshold", 50))
        occurrence = ee.Image(metadata["ee_id"]).select(metadata["band"]).clip(region)
        water_mask = occurrence.gte(threshold).selfMask().rename("water_mask")
        self._record_tool(
            "map_jrc_surface_water_occurrence",
            dataset=metadata["ee_id"],
            occurrence_threshold_percent=threshold,
            scale=plan.scale,
        )
        return water_mask, occurrence.rename("water_occurrence_percent"), {
            "method": "JRC Global Surface Water occurrence threshold; pixels meeting the occurrence threshold are mapped as surface water.",
            "occurrence_threshold_percent": threshold,
            "water_definition": f"JRC occurrence >= {threshold} percent",
        }

    def _surface_water_geojson(self, plan: GeePlan, water_mask: Any) -> dict[str, Any]:
        region = self._region_geometry(plan)
        vectors = water_mask.toByte().reduceToVectors(
            geometry=region,
            scale=plan.scale,
            geometryType="polygon",
            eightConnected=True,
            labelProperty="water_mask",
            maxPixels=1e9,
        )
        payload = vectors.getInfo()
        if not isinstance(payload, dict):
            return {"type": "FeatureCollection", "features": []}
        for feature in payload.get("features", []) if isinstance(payload.get("features"), list) else []:
            properties = feature.setdefault("properties", {})
            if isinstance(properties, dict):
                properties.setdefault("region_name", plan.region.get("name"))
                properties.setdefault("dataset", plan.dataset)
                properties.setdefault("water_class", "mapped_surface_water")
        return payload

    def _save_surface_water_folium_map(self, query: str, plan: GeePlan, visualization: dict[str, Any]) -> str:
        import folium

        minx, miny, maxx, maxy = plan.region["coordinates"]
        center = [(miny + maxy) / 2, (minx + maxx) / 2]
        fmap = folium.Map(location=center, zoom_start=10, tiles="CartoDB positron")
        tile_url = visualization.get("tile_fetcher_url_format")
        if tile_url:
            folium.TileLayer(
                tiles=tile_url,
                name="Earth Engine surface-water raster",
                attr="Google Earth Engine",
                overlay=True,
                control=True,
                opacity=0.82,
            ).add_to(fmap)
        if plan.region.get("geojson"):
            folium.GeoJson(
                {"type": "Feature", "properties": {"name": plan.region.get("name")}, "geometry": plan.region["geojson"]},
                name="Analysis boundary",
                style_function=lambda feature: {"fillOpacity": 0, "color": "#1f2937", "weight": 2},
            ).add_to(fmap)
        fmap.fit_bounds([[miny, minx], [maxy, maxx]])
        fmap.get_root().header.add_child(
            folium.Element("<style>html, body { color-scheme: light; background: #ffffff !important; color: #1f2937; }</style>")
        )
        legend_html = (
            "<div style='position: fixed; bottom: 24px; left: 24px; z-index: 9999; background: white; "
            "padding: 10px 12px; border: 1px solid #999; font-size: 12px;'>"
            "<strong>Surface water</strong>"
            "<div><span style='display:inline-block;width:12px;height:12px;background:#1d4ed8;"
            "margin-right:6px;border:1px solid #666;'></span>Mapped water</div></div>"
        )
        fmap.get_root().html.add_child(folium.Element(legend_html))
        folium.LayerControl().add_to(fmap)
        path = self._output_path(query, "html", "gee_surface_water_map")
        fmap.save(path)
        self._record_tool("write_surface_water_folium_map", path=path)
        return path

    def run_surface_water_map(self, query: str, plan: GeePlan) -> tuple[dict[str, Any], list[str]]:
        metadata = DATASET_ALIASES[plan.dataset]
        region = self._region_geometry(plan)
        water_mask, _source_image, method = self._surface_water_images(plan)
        vis_image = water_mask.visualize(min=1, max=1, palette=["#1d4ed8"]).clip(region)
        visualization = self._earth_engine_visualization_urls(
            vis_image,
            plan,
            {},
            visualization_name="surface_water",
        )
        self._progress(
            "earth_engine_reduction",
            "I am calculating mapped water area over the analysis region for exposure-ready tabular output.",
            dataset=metadata["ee_id"],
            scale=plan.scale,
        )
        area_stats = (
            ee.Image.pixelArea()
            .divide(1_000_000)
            .rename("water_area_sq_km")
            .updateMask(water_mask)
            .reduceRegion(
                reducer=ee.Reducer.sum(),
                geometry=region,
                scale=plan.scale,
                maxPixels=1e9,
            )
            .getInfo()
        )
        water_area = area_stats.get("water_area_sq_km") if isinstance(area_stats, dict) else None
        rows = [
            {
                "region_name": plan.region.get("name"),
                "dataset": plan.dataset,
                "dataset_id": metadata["ee_id"],
                "water_area_sq_km": water_area,
                "scale_m": plan.scale,
                "date_start": plan.date_range["start"] if plan.dataset == "sentinel1_grd" else None,
                "date_end": plan.date_range["end"] if plan.dataset == "sentinel1_grd" else None,
                "water_definition": method.get("water_definition"),
            }
        ]
        export_status = None
        export_wait = None
        export_error = None
        raster_output = None
        if plan.export.get("enabled"):
            description = re.sub(r"[^A-Za-z0-9_]+", "_", plan.export.get("description") or "gee_surface_water_map")[:100]
            try:
                export_result = self._export_image(water_mask, plan, description)
                export_wait = self._wait_for_export(export_result)
                export_status = export_result["status"]
                raster_output = export_result.get("raster_output")
            except Exception as exc:
                export_error = str(exc)
                self._record_tool("start_earth_engine_export_failed", destination=plan.export.get("destination"), error=export_error)
        summary = {
            "action": plan.action,
            "dataset": plan.dataset,
            "dataset_id": metadata["ee_id"],
            "region": plan.region,
            "date_range": plan.date_range if plan.dataset == "sentinel1_grd" else None,
            "scale_m": plan.scale,
            "water_area_sq_km": water_area,
            "method": method,
            "visualization": {"palette": ["#1d4ed8"], "water_value": 1},
            "source_band": metadata["band"],
            "preferred_visualization": "earth_engine_thumbnail_or_map_tiles",
            "earth_engine_visualization": visualization,
            "export_status": export_status,
            "export_wait": export_wait,
            "export_error": export_error,
            "raster_output": raster_output,
            "exposure_ready_outputs": {
                "csv": "One-row regional water exposure summary with mapped water area and method fields.",
                "geojson": "Mapped water polygons generated from the thresholded water mask when requested.",
            },
        }
        self._record_tool(
            "create_surface_water_map",
            dataset=metadata["ee_id"],
            water_area_sq_km=water_area,
            scale=plan.scale,
        )
        artifacts: list[str] = []
        if self._wants_output(plan, "json"):
            artifacts.append(self._write_json_artifact(query, "gee_surface_water_summary", summary))
        if self._wants_output(plan, "csv"):
            artifacts.append(self._write_csv_artifact(query, "gee_surface_water_summary", rows))
        if self._wants_output(plan, "geojson"):
            geojson_payload = self._surface_water_geojson(plan, water_mask)
            summary["geojson_feature_count"] = len(geojson_payload.get("features", [])) if isinstance(geojson_payload.get("features"), list) else 0
            artifacts.append(self._write_geojson_artifact(query, "gee_surface_water_polygons", geojson_payload))
        if self._wants_output(plan, "html"):
            artifacts.append(self._save_surface_water_folium_map(query, plan, visualization))
        summary["returned_artifacts"] = self._requested_artifact_labels(plan, rows=True, html_map=True)
        return summary, artifacts

    def _export_image_to_drive(self, image: Any, plan: GeePlan, description: str) -> dict[str, Any]:
        task = ee.batch.Export.image.toDrive(
            image=image,
            description=description,
            region=self._region_geometry(plan),
            scale=plan.scale,
            maxPixels=1e9,
            fileFormat="GeoTIFF",
        )
        task.start()
        status = task.status()
        self._record_tool("start_earth_engine_export", destination="drive", task_id=status.get("id"))
        return {"status": status, "raster_output": None, "_task": task}

    def _export_file_prefix(self, description: str) -> str:
        prefix_root = (os.getenv("GEE_EXPORT_PREFIX") or "gee_exports").strip().strip("/")
        digest = hashlib.sha1(description.encode("utf-8")).hexdigest()[:10]
        safe_description = re.sub(r"[^A-Za-z0-9_/-]+", "_", description).strip("_/")[:80] or "gee_export"
        return f"{prefix_root}/{safe_description}_{digest}" if prefix_root else f"{safe_description}_{digest}"

    def _export_image_to_gcs(self, image: Any, plan: GeePlan, description: str) -> dict[str, Any]:
        bucket = (os.getenv("GEE_EXPORT_BUCKET") or str(self.request_parameters.get("gcs_bucket") or "")).strip()
        if not bucket:
            raise RuntimeError("GEE_EXPORT_BUCKET must be configured to export Earth Engine rasters to Cloud Storage.")
        file_prefix = self._export_file_prefix(description)
        task = ee.batch.Export.image.toCloudStorage(
            image=image,
            description=description,
            bucket=bucket,
            fileNamePrefix=file_prefix,
            region=self._region_geometry(plan),
            scale=plan.scale,
            maxPixels=1e9,
            fileFormat="GeoTIFF",
        )
        task.start()
        status = task.status()
        raster_output = {
            "destination": "gcs",
            "format": "GeoTIFF",
            "bucket": bucket,
            "file_prefix": file_prefix,
            "uri": f"gs://{bucket}/{file_prefix}.tif",
            "https_url": f"https://storage.googleapis.com/{bucket}/{file_prefix}.tif",
            "signed_url": None,
            "signed_url_note": "Signed URL can be generated after the Earth Engine export task completes and the object exists.",
            "object_exists": None,
        }
        self._record_tool("start_earth_engine_export", destination="gcs", task_id=status.get("id"), uri=raster_output["uri"])
        self.emit_progress(
            getattr(self, "_active_progress_callback", None),
            stage="export_started",
            message=(
                f"Started Earth Engine Cloud Storage export task {status.get('id', 'unknown')} "
                f"with initial state {status.get('state', 'UNKNOWN')}."
            ),
            data={
                "task_id": status.get("id"),
                "state": status.get("state"),
                "gcs_uri": raster_output["uri"],
                "https_url": raster_output["https_url"],
            },
        )
        return {"status": status, "raster_output": raster_output, "_task": task}

    def _export_image(self, image: Any, plan: GeePlan, description: str) -> dict[str, Any]:
        region = self._region_geometry(plan)
        image = image.clip(region)
        destination = str(
            plan.export.get("destination")
            or self.request_parameters.get("export_destination")
            or self._default_export_destination()
        ).strip().lower()
        if destination in {"gcs", "cloud_storage", "google_cloud_storage"}:
            return self._export_image_to_gcs(image, plan, description)
        return self._export_image_to_drive(image, plan, description)

    def _default_export_destination(self) -> str:
        if (os.getenv("GEE_EXPORT_BUCKET") or str(self.request_parameters.get("gcs_bucket") or "")).strip():
            return "gcs"
        return "drive"

    def _request_bool(self, key: str, default: bool = False) -> bool:
        value = self.request_parameters.get(key)
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"true", "1", "yes", "y", "on"}
        return bool(value)

    def _request_positive_int(self, key: str, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
        value = self.request_parameters.get(key, default)
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        parsed = max(minimum, parsed)
        if maximum is not None:
            parsed = min(maximum, parsed)
        return parsed

    def _wait_for_export_completion(self, task: Any, raster_output: dict[str, Any] | None) -> dict[str, Any]:
        timeout_seconds = self._request_positive_int("wait_timeout_seconds", 900, minimum=1, maximum=7200)
        poll_interval_seconds = self._request_positive_int("poll_interval_seconds", 15, minimum=1, maximum=300)
        started = time.time()
        status = task.status()
        polls = 1
        progress_callback = getattr(self, "_active_progress_callback", None)
        if isinstance(raster_output, dict):
            self._augment_gcs_raster_output(raster_output)
            self.emit_progress(
                progress_callback,
                stage="export_wait",
                message=(
                    f"Earth Engine export task {status.get('id', 'unknown')} is {status.get('state', 'UNKNOWN')}; "
                    f"GCS object visible: {raster_output.get('object_exists')}."
                ),
                data={
                    "task_id": status.get("id"),
                    "state": status.get("state"),
                    "polls": polls,
                    "gcs_uri": raster_output.get("uri"),
                    "object_exists": raster_output.get("object_exists"),
                    "signed_url_note": raster_output.get("signed_url_note"),
                },
            )
            if raster_output.get("object_exists") is True:
                elapsed = time.time() - started
                self._record_tool(
                    "wait_for_export_gcs_object_available",
                    task_id=status.get("id"),
                    state=status.get("state"),
                    elapsed_seconds=elapsed,
                    polls=polls,
                    uri=raster_output.get("uri"),
                )
                return {
                    "completed": True,
                    "timed_out": False,
                    "elapsed_seconds": elapsed,
                    "polls": polls,
                    "timeout_seconds": timeout_seconds,
                    "poll_interval_seconds": poll_interval_seconds,
                    "final_status": status,
                    "completion_source": "gcs_object",
                }
        while status.get("state") not in {"COMPLETED", "FAILED", "CANCELLED", "CANCELED"}:
            elapsed = time.time() - started
            if elapsed >= timeout_seconds:
                self._record_tool(
                    "wait_for_export_timeout",
                    task_id=status.get("id"),
                    state=status.get("state"),
                    elapsed_seconds=elapsed,
                    polls=polls,
                )
                return {
                    "completed": False,
                    "timed_out": True,
                    "elapsed_seconds": elapsed,
                    "polls": polls,
                    "timeout_seconds": timeout_seconds,
                    "poll_interval_seconds": poll_interval_seconds,
                    "final_status": status,
                }
            time.sleep(min(poll_interval_seconds, max(0, timeout_seconds - elapsed)))
            status = task.status()
            polls += 1
            if isinstance(raster_output, dict):
                self._augment_gcs_raster_output(raster_output)
                self.emit_progress(
                    progress_callback,
                    stage="export_wait",
                    message=(
                        f"Earth Engine export task {status.get('id', 'unknown')} is {status.get('state', 'UNKNOWN')}; "
                        f"GCS object visible: {raster_output.get('object_exists')}."
                    ),
                    data={
                        "task_id": status.get("id"),
                        "state": status.get("state"),
                        "polls": polls,
                        "gcs_uri": raster_output.get("uri"),
                        "object_exists": raster_output.get("object_exists"),
                        "signed_url_note": raster_output.get("signed_url_note"),
                    },
                )
                if raster_output.get("object_exists") is True:
                    elapsed = time.time() - started
                    self._record_tool(
                        "wait_for_export_gcs_object_available",
                        task_id=status.get("id"),
                        state=status.get("state"),
                        elapsed_seconds=elapsed,
                        polls=polls,
                        uri=raster_output.get("uri"),
                    )
                    return {
                        "completed": True,
                        "timed_out": False,
                        "elapsed_seconds": elapsed,
                        "polls": polls,
                        "timeout_seconds": timeout_seconds,
                        "poll_interval_seconds": poll_interval_seconds,
                        "final_status": status,
                        "completion_source": "gcs_object",
                    }
        elapsed = time.time() - started
        if status.get("state") == "COMPLETED" and isinstance(raster_output, dict):
            self._augment_gcs_raster_output(raster_output)
        self._record_tool(
            "wait_for_export_completion",
            task_id=status.get("id"),
            state=status.get("state"),
            elapsed_seconds=elapsed,
            polls=polls,
        )
        return {
            "completed": status.get("state") == "COMPLETED",
            "timed_out": False,
            "elapsed_seconds": elapsed,
            "polls": polls,
            "timeout_seconds": timeout_seconds,
            "poll_interval_seconds": poll_interval_seconds,
            "final_status": status,
        }

    def _augment_gcs_raster_output(self, raster_output: dict[str, Any]) -> None:
        if raster_output.get("destination") != "gcs":
            return
        bucket_name = raster_output.get("bucket")
        file_prefix = raster_output.get("file_prefix")
        if not bucket_name or not file_prefix:
            return
        object_name = f"{file_prefix}.tif"
        try:
            from google.cloud import storage  # type: ignore
        except Exception as exc:
            raster_output["object_exists"] = None
            raster_output["signed_url_note"] = (
                "Install google-cloud-storage in the GAS deployment to verify object existence "
                f"and generate signed URLs. Import error: {exc}"
            )
            return
        try:
            key_file = os.getenv("GEE_SERVICE_ACCOUNT_KEY") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
            if key_file and Path(key_file).is_file():
                client = storage.Client.from_service_account_json(key_file)
            else:
                client = storage.Client()
            bucket = client.bucket(str(bucket_name))
            blob = bucket.blob(object_name)
            exists = blob.exists()
            if not exists:
                candidates = [
                    candidate
                    for candidate in bucket.list_blobs(prefix=str(file_prefix))
                    if str(candidate.name).lower().endswith((".tif", ".tiff"))
                ]
                if candidates:
                    blob = sorted(candidates, key=lambda candidate: candidate.name)[0]
                    object_name = blob.name
                    exists = True
                    raster_output["file_prefix_matched_object"] = object_name
                    raster_output["uri"] = f"gs://{bucket_name}/{object_name}"
                    raster_output["https_url"] = f"https://storage.googleapis.com/{bucket_name}/{object_name}"
            raster_output["object_exists"] = exists
            if not exists:
                raster_output["signed_url_note"] = "The expected GCS object was not visible yet."
                return
            expiration = self._request_positive_int("signed_url_expiration_seconds", 3600, minimum=60, maximum=604800)
            try:
                raster_output["signed_url"] = blob.generate_signed_url(
                    version="v4",
                    expiration=timedelta(seconds=expiration),
                    method="GET",
                )
                raster_output["signed_url_expires_in_seconds"] = expiration
                raster_output["signed_url_note"] = "Signed URL generated after Earth Engine export completion."
            except Exception as exc:
                raster_output["signed_url"] = None
                raster_output["signed_url_note"] = (
                    "The GCS object exists, but signed URL generation failed. "
                    f"Use the authenticated_url, https_url, or gs uri if you have access. Error: {exc}"
                )
        except Exception as exc:
            raster_output["object_exists"] = None
            raster_output["signed_url_note"] = f"Could not verify the GCS object or generate a signed URL: {exc}"

    def _wait_for_export(self, export_result: dict[str, Any]) -> dict[str, Any] | None:
        task = export_result.get("_task")
        if task is None:
            return {
                "completed": False,
                "timed_out": False,
                "error": "Export task object was not available for polling.",
            }
        wait_result = self._wait_for_export_completion(task, export_result.get("raster_output"))
        if isinstance(wait_result.get("final_status"), dict):
            export_result["status"] = wait_result["final_status"]
        return wait_result

    def run_cloud_filtered_composite(self, query: str, plan: GeePlan) -> tuple[dict[str, Any], list[str]]:
        region = self._region_geometry(plan)
        image = self._median_visualization_image(plan).clip(region)
        metadata = DATASET_ALIASES[plan.dataset]
        vis = {"bands": metadata["rgb"], "min": 0, "max": 0.3 if plan.dataset.startswith("landsat") else 3000}
        thumb_url = None
        map_id = None
        tile_fetcher_url_format = None
        thumbnail_error = None
        try:
            self._progress(
                "earth_engine_visualization",
                "I am requesting an Earth Engine thumbnail URL for the cloud-filtered composite preview.",
                dataset=plan.dataset,
            )
            thumb_url = image.getThumbURL({"region": region, "dimensions": 1024, **vis})
            self._record_tool("create_thumbnail_url", dataset=plan.dataset)
        except Exception as exc:
            thumbnail_error = str(exc)
            self._record_tool("create_thumbnail_url_failed", dataset=plan.dataset, error=thumbnail_error)
        map_tile_error = None
        try:
            self._progress(
                "earth_engine_visualization",
                "I am requesting an Earth Engine map tile URL for the cloud-filtered composite.",
                dataset=plan.dataset,
            )
            map_info = image.getMapId(vis)
            map_id = map_info.get("mapid")
            tile_fetcher = map_info.get("tile_fetcher")
            if tile_fetcher is not None:
                tile_fetcher_url_format = getattr(tile_fetcher, "url_format", None)
            self._record_tool("create_map_tile_url", dataset=plan.dataset, map_id=map_id)
        except Exception as exc:
            map_tile_error = str(exc)
            self._record_tool("create_map_tile_url_failed", dataset=plan.dataset, error=map_tile_error)
        export_status = None
        export_error = None
        if plan.export.get("enabled"):
            description = re.sub(r"[^A-Za-z0-9_]+", "_", plan.export.get("description") or "gee_cloud_filtered_composite")[:100]
            try:
                export_result = self._export_image(image.select(metadata["rgb"]), plan, description)
                export_wait = self._wait_for_export(export_result)
                export_status = export_result["status"]
                raster_output = export_result.get("raster_output")
            except Exception as exc:
                export_error = str(exc)
                export_wait = None
                raster_output = None
                self._record_tool("start_earth_engine_export_failed", destination="drive", error=export_error)
        else:
            raster_output = None
            export_wait = None
        summary = {
            "action": plan.action,
            "dataset": plan.dataset,
            "dataset_id": metadata["ee_id"],
            "region": plan.region,
            "date_range": plan.date_range,
            "scale_m": plan.scale,
            "max_cloud_percent": plan.max_cloud_percent,
            "visualization": vis,
            "thumbnail_url": thumb_url,
            "map_id": map_id,
            "tile_fetcher_url_format": tile_fetcher_url_format,
            "thumbnail_error": thumbnail_error,
            "map_tile_error": map_tile_error,
            "export_status": export_status,
            "export_error": export_error,
            "export_wait": export_wait,
            "raster_output": raster_output,
            "preferred_visualization": "thumbnail_url_or_tile_fetcher_url_format",
            "cloud_handling": {
                "scene_cloud_filter_percent": 100,
                "pixel_mask": "Sentinel-2 SCL or Landsat QA_PIXEL cloud/shadow/snow mask before median compositing.",
                "reason": "Composite previews prioritize full-region visual coverage; strict scene-level cloud metadata can drop adjacent Sentinel/Landsat tiles.",
                "requested_max_cloud_percent": plan.max_cloud_percent,
            },
            "raster_delivery": {
                "preview": "Use the composite_thumbnail_png_url artifact, thumbnail_url, or tile_fetcher_url_format for visual inspection.",
                "durable_geotiff": "Call create_export_task if the preview is useful and a GeoTIFF is needed.",
            },
        }
        artifacts: list[str] = []
        if self._wants_output(plan, "json"):
            artifacts.append(self._write_json_artifact(query, "gee_cloud_filtered_composite", summary))
        if self._wants_output(plan, "html"):
            artifacts.append(self._save_composite_preview_html(query, plan, summary))
        summary["returned_artifacts"] = self._requested_artifact_labels(plan, html_map=True)
        return summary, artifacts

    def run_create_export_task(self, query: str, plan: GeePlan) -> tuple[dict[str, Any], list[str]]:
        plan.export["enabled"] = True
        image = self._median_optical_image(plan)
        description = re.sub(r"[^A-Za-z0-9_]+", "_", plan.export.get("description") or "gee_export_task")[:100]
        export_error = None
        raster_output = None
        try:
            export_result = self._export_image(image, plan, description)
            export_wait = self._wait_for_export(export_result)
            status = export_result["status"]
            raster_output = export_result.get("raster_output")
        except Exception as exc:
            status = None
            export_error = str(exc)
            export_wait = None
            self._record_tool("start_earth_engine_export_failed", destination="drive", error=export_error)
        summary = {
            "action": plan.action,
            "dataset": plan.dataset,
            "dataset_id": DATASET_ALIASES[plan.dataset]["ee_id"],
            "region": plan.region,
            "date_range": plan.date_range,
            "scale_m": plan.scale,
            "export_status": status,
            "export_wait": export_wait,
            "export_error": export_error,
            "raster_output": raster_output,
            "required_permission": "earthengine.exports.create",
            "suggested_role": "roles/earthengine.writer",
        }
        artifacts: list[str] = []
        if self._wants_output(plan, "json", "export", default=True):
            artifacts.append(self._write_json_artifact(query, "gee_export_task", summary))
        summary["returned_artifacts"] = self._requested_artifact_labels(plan)
        return summary, artifacts

    def _execute_plan(self, query: str, plan: GeePlan) -> tuple[dict[str, Any], list[str]]:
        if plan.action == "ndvi_summary":
            return self.run_ndvi_summary(query, plan)
        if plan.action == "ndvi_time_series":
            return self.run_ndvi_time_series(query, plan)
        if plan.action == "ndvi_map":
            return self.run_ndvi_map(query, plan)
        if plan.action == "chirps_precipitation_summary":
            return self.run_chirps_precipitation_summary(query, plan)
        if plan.action == "climate_time_series":
            return self.run_climate_time_series(query, plan)
        if plan.action == "land_cover_area_summary":
            return self.run_land_cover_area_summary(query, plan)
        if plan.action == "land_cover_map":
            return self.run_land_cover_map(query, plan)
        if plan.action == "surface_water_map":
            return self.run_surface_water_map(query, plan)
        if plan.action == "cloud_filtered_composite":
            return self.run_cloud_filtered_composite(query, plan)
        if plan.action == "create_export_task":
            return self.run_create_export_task(query, plan)
        raise ValueError(f"Unsupported GEE action: {plan.action}")

    def run(
        self,
        query: str,
        input_dataset_paths: list[str] | str | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        start_time = time.time()
        self.reset_metrics()
        self.token_usage_available = False
        self.tool_trace = []
        input_paths = self.normalize_dataset_paths(input_dataset_paths)
        self._active_progress_callback = progress_callback
        try:
            self.emit_progress(progress_callback, stage="planning", message="I am asking the LLM for a constrained Earth Engine workflow plan.")
            raw_plan = self._llm_plan(query)
            plan = self._validate_plan(raw_plan, input_paths, query=query)

            self.emit_progress(
                progress_callback,
                stage="source_validation",
                message="I validated the requested Earth Engine action, dataset, date range, region, and outputs.",
                data={"action": plan.action, "dataset": plan.dataset},
            )
            self._initialize_ee()
            self.emit_progress(progress_callback, stage="analysis_execution", message="I am executing the validated workflow with the Earth Engine Python API.")
            summary, artifacts = self._execute_plan(query, plan)
        finally:
            self._active_progress_callback = None

        plan_payload = {
            "action": plan.action,
            "dataset": plan.dataset,
            "region": plan.region,
            "date_range": plan.date_range,
            "reducer": plan.reducer,
            "scale": plan.scale,
            "max_cloud_percent": plan.max_cloud_percent,
            "outputs": plan.outputs,
            "export": plan.export,
            "variables": plan.variables,
            "source": plan.source,
            "notes": plan.notes,
        }
        artifacts = list(dict.fromkeys(artifacts))
        semantic_outputs = self._semantic_artifact_outputs(artifacts)
        visualization = summary.get("earth_engine_visualization") if isinstance(summary.get("earth_engine_visualization"), dict) else {}
        requested_outputs = self._artifact_outputs(plan)
        if plan.action == "ndvi_map" and requested_outputs["thumbnail"] and visualization.get("thumbnail_url"):
            semantic_outputs = {
                "ndvi_thumbnail_png_url": self._external_url_artifact(
                    url=visualization["thumbnail_url"],
                    filename="gee_ndvi_thumbnail.png",
                    role="ndvi_thumbnail_png_url",
                    label="Earth Engine NDVI Preview",
                    mime_type="image/png",
                    format_name="png",
                    description="Earth Engine thumbnail URL for visual inspection of the NDVI raster.",
                ),
                **semantic_outputs,
            }
        if plan.action == "cloud_filtered_composite" and requested_outputs["thumbnail"] and summary.get("thumbnail_url"):
            semantic_outputs = {
                "composite_thumbnail_png_url": self._external_url_artifact(
                    url=summary["thumbnail_url"],
                    filename="gee_composite_thumbnail.png",
                    role="composite_thumbnail_png_url",
                    label="Earth Engine Composite Preview",
                    mime_type="image/png",
                    format_name="png",
                    description="Earth Engine thumbnail URL for visual inspection of the cloud-masked true-color composite.",
                ),
                **semantic_outputs,
            }
        if plan.action == "land_cover_map" and requested_outputs["thumbnail"] and visualization.get("thumbnail_url"):
            semantic_outputs = {
                "land_cover_thumbnail_png_url": self._external_url_artifact(
                    url=visualization["thumbnail_url"],
                    filename="gee_land_cover_thumbnail.png",
                    role="land_cover_thumbnail_png_url",
                    label="Earth Engine Land-Cover Preview",
                    mime_type="image/png",
                    format_name="png",
                    description="Earth Engine thumbnail URL for visual inspection of the classified land-cover raster.",
                ),
                **semantic_outputs,
            }
        if plan.action == "surface_water_map" and requested_outputs["thumbnail"] and visualization.get("thumbnail_url"):
            semantic_outputs = {
                "surface_water_thumbnail_png_url": self._external_url_artifact(
                    url=visualization["thumbnail_url"],
                    filename="gee_surface_water_thumbnail.png",
                    role="surface_water_thumbnail_png_url",
                    label="Earth Engine Surface-Water Preview",
                    mime_type="image/png",
                    format_name="png",
                    description="Earth Engine thumbnail URL for visual inspection of the mapped surface-water raster.",
                ),
                **semantic_outputs,
            }
        raster_output = summary.get("raster_output") if isinstance(summary.get("raster_output"), dict) else {}
        if raster_output.get("signed_url"):
            semantic_outputs = {
                "exported_geotiff_signed_url": self._external_url_artifact(
                    url=raster_output["signed_url"],
                    filename=Path(str(raster_output.get("uri") or "gee_export.tif")).name,
                    role="exported_geotiff_signed_url",
                    label="Exported GeoTIFF",
                    mime_type="image/tiff",
                    format_name="tif",
                    description="Signed HTTPS URL for the completed Google Cloud Storage GeoTIFF export.",
                ),
                **semantic_outputs,
            }
        safe_parameters = self._safe_request_parameters()
        delivered_artifact_count = len(semantic_outputs)
        self.set_artifact_count(delivered_artifact_count)
        self.emit_progress(progress_callback, stage="response_preparation", message="I am packaging Earth Engine outputs, provenance, and limitations.")

        text = self._result_summary_text(plan, summary)

        duration = time.time() - start_time
        return {
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "model": self.model,
            "duration": duration,
            "inputs": {"dataset_paths": input_paths, "parameters": safe_parameters},
            "outputs": {
                "text": text,
                **semantic_outputs,
                "output_files": artifacts,
                "dataset_paths": artifacts,
                "dataset_path": artifacts[0] if artifacts else None,
                "dataset_size": {
                    "type": "earth_engine_result",
                    "artifact_count": delivered_artifact_count,
                },
            },
            "metrics": self.metrics(number_of_artifacts=delivered_artifact_count),
            "total_input_tokens": self.input_tokens if self.token_usage_available else None,
            "total_output_tokens": self.output_tokens if self.token_usage_available else None,
            "total_tokens": (self.input_tokens + self.output_tokens) if self.token_usage_available else None,
            "environment": {
                "python_version": platform.python_version(),
                "domain-specific_libraries": ["earthengine-api", "pandas", "python-dotenv"],
            },
            "complementary": {
                "Execution": {
                    "Inputs": {"task": query, "dataset_paths": input_paths, "parameters": safe_parameters},
                    "Outputs": {"summary": text, "artifacts": artifacts, "gee_summary": summary},
                },
                "Provenance": {
                    "Lineage": [
                        "Used an LLM to create a constrained JSON Earth Engine workflow plan.",
                        "Validated the plan against supported actions, datasets, region, date, scale, and output rules.",
                        "Executed only trusted Google Earth Engine Python API tool functions.",
                    ],
                    "GEE Summary": summary,
                    "Validated Plan": plan_payload,
                    "Raw LLM Plan": raw_plan,
                    "Tool Calls": {"count": self.tool_calls, "tools": self.tool_trace},
                    "LLM Calls": {"count": self.llm_calls},
                },
                "Validation": {
                    "status": "passed",
                    "checks": [
                        {"name": "llm_plan_parsed", "status": "passed", "message": "The LLM returned a JSON workflow plan."},
                        {"name": "plan_validated", "status": "passed", "message": f"Validated action {plan.action} on dataset {plan.dataset}."},
                        {"name": "artifact_count", "status": "passed", "message": f"Created {delivered_artifact_count} artifact(s)."},
                    ],
                },
                "Assumptions and Limitations": {
                    "assumptions": [
                        "The deployment provides Earth Engine service-account credentials and a Cloud project.",
                        "Named-place handling is intentionally conservative unless the LLM returns an explicit bounding box.",
                    ],
                    "limitations": [
                        "The GEE agent supports a focused set of remote-sensing workflows rather than arbitrary Earth Engine code.",
                        "Earth Engine export tasks are started asynchronously and may need to be monitored outside the initial GAS response.",
                        "Cloud and quality masking is dataset-specific and conservative for demo workflows.",
                    ],
                },
                "Artifacts and Logs": {
                    "Inline Artifacts": {},
                    "Persisted Artifacts": {"paths": artifacts},
                },
            },
            "stochasticity": {
                "used": True,
                "controls": [
                    {
                        "name": "llm_planning_temperature",
                        "value": 0.1,
                        "description": "The model is used only to produce a constrained JSON workflow plan.",
                    },
                    {
                        "name": "trusted_tool_validation",
                        "description": "The server validates the plan and dispatches only prebuilt Earth Engine Python API tools.",
                    },
                ],
            },
            "reproducibility_notes": [
                "The GEE agent does not execute LLM-generated Python code; execution.code.available is false by design.",
                "The reproducible workflow specification is the validated plan in provenance.details.validated_plan plus execution inputs, parameters, and artifact references.",
            ],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
