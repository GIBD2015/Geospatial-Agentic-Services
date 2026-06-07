from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
import time
import json
import importlib.util
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import geopandas as gpd
import pandas as pd

from gas_server.core.file_naming import build_output_filename
from gas_server.core.geo_agent import GeoAgent
from gas_server.core.llm_client import build_llm_client, format_service_name
from gas_server.core.config import DATA_DIR, ensure_runtime_dirs


ensure_runtime_dirs()


class WebMappingAppAgent(GeoAgent):
    agent_id = "web_mapping_app_agent"
    agent_name = "Web Mapping App Agent"
    agent_version = "1.0.0"
    agent_description = "Generates browser-ready web mapping applications from vector and raster geospatial datasets."
    requires_input_datasets = True

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str | None = None,
        max_iterations: int = 5,
        timeout_seconds: int = 90,
    ):
        super().__init__(
            api_key=api_key,
            model=model or "gpt-5.2",
            output_dir=DATA_DIR / self.agent_id,
        )
        self.max_iterations = max_iterations
        self.timeout_seconds = timeout_seconds
        self.service_name = format_service_name(self.agent_name)
        self.client = build_llm_client(
            service_name=self.service_name,
            openai_api_key=api_key,
        )
        self.generated_code: str | None = None
        self.last_error = ""
        self.available_mapping_libraries = self._available_mapping_libraries()

    def _available_mapping_libraries(self) -> list[str]:
        candidates = ("folium", "branca", "geopandas", "rasterio", "pandas")
        return [name for name in candidates if importlib.util.find_spec(name) is not None]

    def _resolve_python_runner(self) -> str:
        executable = (sys.executable or "").strip()
        if executable and "python" in os.path.basename(executable).lower():
            return executable
        return "python"

    def _extract_python_code(self, text: str | None) -> str:
        if not text:
            return ""
        match = re.search(r"```python\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        match = re.search(r"```\s*(.*?)```", text, re.DOTALL)
        return match.group(1).strip() if match else text.strip()

    def _inspect_dataset(self, path: str) -> Dict[str, Any]:
        dataset = Path(path)
        info: Dict[str, Any] = {
            "path": str(dataset),
            "name": dataset.name,
            "format": dataset.suffix.lower().lstrip("."),
            "exists": dataset.exists(),
            "type": "unknown",
        }
        if not dataset.exists():
            info["error"] = "File does not exist."
            return info

        suffix = dataset.suffix.lower()
        try:
            if suffix in {".geojson", ".json", ".gpkg", ".shp", ".gml", ".kml"}:
                gdf = gpd.read_file(dataset)
                info.update(
                    {
                        "type": "vector",
                        "feature_count": int(len(gdf)),
                        "crs": str(gdf.crs) if gdf.crs else None,
                        "columns": [str(column) for column in gdf.columns[:30]],
                        "geometry_types": sorted(str(value) for value in gdf.geometry.geom_type.dropna().unique())[:10],
                        "bounds": [float(value) for value in gdf.total_bounds] if len(gdf) else None,
                    }
                )
                del gdf
            elif suffix == ".csv":
                df = pd.read_csv(dataset, nrows=200)
                info.update(
                    {
                        "type": "table",
                        "feature_count": None,
                        "columns": [str(column) for column in df.columns[:30]],
                        "sample_rows": min(len(df), 5),
                    }
                )
            elif suffix in {".tif", ".tiff", ".vrt", ".img"}:
                import rasterio

                with rasterio.open(dataset) as src:
                    info.update(
                        {
                            "type": "raster",
                            "width": int(src.width),
                            "height": int(src.height),
                            "band_count": int(src.count),
                            "crs": str(src.crs) if src.crs else None,
                            "bounds": [float(src.bounds.left), float(src.bounds.bottom), float(src.bounds.right), float(src.bounds.top)],
                        }
                    )
        except Exception as exc:
            info["error"] = str(exc)
        return info

    def _dataset_context(self, dataset_paths: list[str]) -> list[dict[str, Any]]:
        return [self._inspect_dataset(path) for path in dataset_paths]

    def _is_temporal_column_name(self, column: str) -> bool:
        return bool(re.search(r"(?:^|[_\s-])(time|date|datetime|timestamp)(?:$|[_\s-])", str(column), re.IGNORECASE))

    def _epoch_unit_for_series(self, series: pd.Series) -> str | None:
        numeric = pd.to_numeric(series, errors="coerce").dropna()
        if numeric.empty:
            return None

        median_abs = float(numeric.abs().median())
        if median_abs >= 1e18:
            return "ns"
        if median_abs >= 1e15:
            return "us"
        if median_abs >= 1e11:
            return "ms"
        if median_abs >= 1e9:
            return "s"
        return None

    def _normalize_temporal_columns_for_leaflet(self, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        normalized = gdf.copy()
        for column in normalized.columns:
            if column == normalized.geometry.name or not self._is_temporal_column_name(str(column)):
                continue

            unit = self._epoch_unit_for_series(normalized[column])
            if not unit:
                continue

            converted = pd.to_datetime(normalized[column], unit=unit, utc=True, errors="coerce")
            valid = converted.dropna()
            if valid.empty:
                continue

            plausible = valid[(valid.dt.year >= 1900) & (valid.dt.year <= 2200)]
            if len(plausible) < max(1, int(len(valid) * 0.8)):
                continue

            normalized[column] = converted.dt.strftime("%Y-%m-%d %H:%M:%S UTC")

        return normalized

    def _prepare_leaflet_dataset_paths(self, dataset_paths: list[str], progress_callback=None) -> list[str]:
        """Return dataset paths that are safe to use directly in Leaflet/Folium.

        Leaflet expects vector coordinates in longitude/latitude (EPSG:4326).
        Many GAS analysis agents return projected GeoJSON or GeoPackage files,
        so this preparation step writes WGS84 GeoJSON copies before the LLM sees
        the paths. That keeps generated web-map code robust even when it simply
        reads a file and passes it to Folium/Leaflet.
        """

        prepared_paths: list[str] = []
        vector_extensions = {".geojson", ".json", ".gpkg", ".shp", ".gml", ".kml"}
        prepared_dir = Path(self.output_dir) / "leaflet_ready_inputs"
        prepared_dir.mkdir(parents=True, exist_ok=True)

        for index, path in enumerate(dataset_paths):
            dataset = Path(path)
            if dataset.suffix.lower() not in vector_extensions or not dataset.exists():
                prepared_paths.append(path)
                continue

            try:
                gdf = gpd.read_file(dataset)
                if gdf.empty:
                    prepared_paths.append(path)
                    continue

                source_crs = str(gdf.crs) if gdf.crs else None
                if gdf.crs is not None:
                    gdf = gdf.to_crs("EPSG:4326")
                gdf = self._normalize_temporal_columns_for_leaflet(gdf)
                target_path = prepared_dir / f"input_{index + 1}_{dataset.stem}_wgs84.geojson"
                gdf.to_file(target_path, driver="GeoJSON")
                prepared_paths.append(str(target_path))

                if source_crs and source_crs.lower() not in {"epsg:4326", "wgs84"}:
                    self._emit_progress(
                        progress_callback,
                        stage="normalization",
                        message=(
                            f"I reprojected {dataset.name} to EPSG:4326 so the web map can render it correctly in Leaflet."
                        ),
                        data={"input_path": str(dataset), "prepared_path": str(target_path), "source_crs": source_crs},
                    )
            except Exception as exc:
                self.last_error = f"{self.last_error}\nCould not prepare {dataset.name} for Leaflet: {exc}".strip()
                self._emit_progress(
                    progress_callback,
                    stage="warning",
                    message=(
                        f"I could not create a Leaflet-ready WGS84 copy of {dataset.name}, so I will use the original path."
                    ),
                    data={"input_path": str(dataset), "error": str(exc)},
                )
                prepared_paths.append(path)

        return prepared_paths

    def _build_prompt(self, task: str, dataset_paths: list[str], dataset_context: list[dict[str, Any]], output_path: str) -> list[dict[str, str]]:
        system = (
            "You are an expert geospatial web mapping application development agent. Generate robust "
            "Python code that creates a browser-ready HTML web mapping app, preferably using "
            "folium/Leaflet plus clean HTML/CSS/JavaScript. Interpret the user's instructions, inspect "
            "the dataset context, and choose suitable layers, symbology, popups, legends, basemaps, "
            "layer controls, spatial extent, and app UI components such as side panels, filters, "
            "summary cards, search, charts, tables, or explanatory sections when they help the task. "
            "Default requirements: every app must include a map, a layer control, a professional visible "
            "title derived from the request and data, and every choropleth or graduated-color map must "
            "include a clear legend. These defaults may be overridden only when the user explicitly asks "
            "to omit that element. The layer control must be a real map layer control, such as "
            "folium.LayerControl(collapsed=False).add_to(map_object) or "
            "L.control.layers(baseLayers, overlayLayers, {collapsed:false}).addTo(map). "
            "Do not satisfy this requirement only with sidebar checkboxes or explanatory text. "
            "Legends must be compact, readable, and placed in a sidebar or bottom-right control area; "
            "do not create a long horizontal legend across the map. If the legend is placed on the map, "
            "put the legend title inside the same legend box above the swatches/classes rather than as a separate "
            "sidebar card or detached heading. "
            "Legend swatches must use valid CSS colors such as hex, named colors, rgb(...), or rgba(...); "
            "never write Python or Matplotlib color tuples such as background:(0.5, 0.2, 0.1, 1.0). "
            "Do not place a full-width information panel over the map. If you add explanatory content, "
            "use a compact sidebar or a collapsible panel that leaves most of the map visible. "
            "Leaflet and Folium map layers must use longitude/latitude coordinates in EPSG:4326. "
            "The provided vector dataset paths are already prepared for Leaflet when possible; do not replace them "
            "with the original projected coordinates. If you read any vector data yourself, convert it to EPSG:4326 "
            "before creating GeoJSON, Choropleth, or GeoJson layers. "
            "Preserve temporal attributes correctly in popups, tables, labels, filters, and charts. "
            "Fields named time, event_time, timestamp, datetime, or date may contain Unix epoch values. "
            "For numeric epochs, 13-digit values are milliseconds, 10-digit values are seconds, "
            "16-digit values are microseconds, and 19-digit values are nanoseconds. "
            "In pandas, never call bare pd.to_datetime on numeric epoch values; pass the correct unit, "
            "for example pd.to_datetime(values, unit=\"ms\", utc=True) for 13-digit millisecond timestamps. "
            "In JavaScript, new Date(value) expects milliseconds; multiply only 10-digit second epochs by 1000. "
            "Do not download external datasets. Use only the provided dataset paths. "
            "The code must save the final HTML app to the exact OUTPUT_HTML path and print "
            "`__OUTPUT_PATH__=<path>` after saving. Return only Python code in a python fenced block."
        )
        user = f"""
Task:
{task}

Dataset paths:
{dataset_paths}

Dataset context:
{dataset_context}

Available mapping/data libraries in this runtime:
{self.available_mapping_libraries}

OUTPUT_HTML:
{output_path}

Implementation requirements:
- Create a browser-ready web mapping application as an HTML file, not a static PNG.
- Use folium when available; otherwise generate a standalone Leaflet HTML file from Python.
- Leaflet/Folium requires vector coordinates in EPSG:4326. Use the dataset paths provided here; they are Leaflet-ready WGS84 copies when the original inputs used a projected CRS. If you load or transform vector data, call to_crs("EPSG:4326") before adding it to a web map.
- Treat temporal fields carefully. Columns named time, event_time, timestamp, datetime, or date may already be normalized to ISO UTC strings in the prepared inputs. If they are numeric epoch values, infer the unit by digit length: 13-digit = milliseconds, 10-digit = seconds, 16-digit = microseconds, 19-digit = nanoseconds. Do not use bare pd.to_datetime on numeric epochs.
- Include an app-like layout when useful, such as a header, sidebar, summary cards, filters, search, charts, or tables.
- Always include a real Leaflet/Folium layer control for all map layers and basemaps unless the user explicitly asks not to. Use folium.LayerControl(collapsed=False).add_to(m) or L.control.layers(..., {{collapsed:false}}).addTo(map). Sidebar checkboxes can be added, but they do not replace the map layer control.
- Add a polished visible map title, not only the HTML <title>. It should be concise, professional, and based on the user's request and datasets unless the user explicitly asks not to.
- If the map uses choropleth, graduated color, classified color, or any value-based symbology, include a clear compact legend explaining colors/classes/values unless the user explicitly asks not to. Put legends in a sidebar or bottom-right map control with max-width around 320px; do not create a long horizontal legend across the map.
- If a legend is placed on the map, its title must appear inside the same legend box above the color swatches/classes. Do not put a detached "Legend" title in the left panel while the actual legend symbols are on the map.
- Legend swatches must use valid CSS colors, for example #fdae61 or rgba(253,174,97,0.85). Do not use raw Python tuples from Matplotlib colormaps in CSS.
- Keep narrative panels compact. Prefer a left sidebar no wider than about 360px, or a collapsible panel. Do not create a full-width top panel that covers a large part of the map.
- Support multiple vector layers with layer controls and popups.
- For raster inputs, include a reasonable approach if possible; otherwise document the limitation in code comments and still map available vector/tabular data.
- Fit the map extent to the spatial data when possible.
- Save exactly to OUTPUT_HTML.
- Print __OUTPUT_PATH__ after saving.
"""
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _explicitly_omits(self, query: str, feature: str) -> bool:
        normalized = query.lower()
        feature_pattern = feature.replace("_", r"[\s_-]*")
        return bool(
            re.search(
                rf"\b(no|without|omit|remove|hide|disable)\s+(the\s+)?{feature_pattern}\b",
                normalized,
            )
        )

    def _requires_choropleth_legend(self, query: str) -> bool:
        if self._explicitly_omits(query, "legend"):
            return False
        return bool(
            re.search(
                r"\b(choropleth|graduated|classified|classed|color ramp|colour ramp|value[-\s]*based|by value|by population|by rate|by density)\b",
                query.lower(),
            )
        )

    def _requires_legend(self, query: str) -> bool:
        if self._explicitly_omits(query, "legend"):
            return False
        if re.search(r"\blegend(s)?\b", query.lower()):
            return True
        return self._requires_choropleth_legend(query)

    def _html_has_invalid_leaflet_bounds(self, html: str) -> bool:
        """Detect common projected-coordinate mistakes in Leaflet fitBounds calls."""

        def outside_leaflet_coordinate_range(values: list[float]) -> bool:
            if len(values) < 2:
                return False
            return any(abs(value) > 180 for value in values)

        for match in re.finditer(
            r"fitBounds\s*\(\s*\[\s*\[\s*([-+]?\d+(?:\.\d+)?)\s*,\s*([-+]?\d+(?:\.\d+)?)\s*\]\s*,\s*\[\s*([-+]?\d+(?:\.\d+)?)\s*,\s*([-+]?\d+(?:\.\d+)?)\s*\]",
            html,
            flags=re.IGNORECASE,
        ):
            values = [float(value) for value in match.groups()]
            lat1, lon1, lat2, lon2 = values
            if abs(lat1) > 90 or abs(lat2) > 90 or abs(lon1) > 180 or abs(lon2) > 180:
                return True

        for match in re.finditer(
            r"L\.latLngBounds\s*\(\s*L\.latLng\s*\(\s*([-+]?\d+(?:\.\d+)?)\s*,\s*([-+]?\d+(?:\.\d+)?)\s*\)\s*,\s*L\.latLng\s*\(\s*([-+]?\d+(?:\.\d+)?)\s*,\s*([-+]?\d+(?:\.\d+)?)\s*\)",
            html,
            flags=re.IGNORECASE,
        ):
            values = [float(value) for value in match.groups()]
            if outside_leaflet_coordinate_range(values):
                return True

        for match in re.finditer(
            r"(?:center|setView)\s*[:(]\s*\[\s*([-+]?\d+(?:\.\d+)?)\s*,\s*([-+]?\d+(?:\.\d+)?)\s*\]",
            html,
            flags=re.IGNORECASE,
        ):
            values = [float(value) for value in match.groups()]
            lat, lon = values
            if abs(lat) > 90 or abs(lon) > 180:
                return True
        return False

    def _html_has_projected_geojson_coordinates(self, html: str) -> bool:
        """Detect embedded GeoJSON coordinate pairs that are not longitude/latitude."""

        if "coordinates" not in html.lower():
            return False

        coordinates_pattern = re.compile(
            r"[\"']coordinates[\"']\s*:\s*(?:\[\s*)+([-+]?\d+(?:\.\d+)?)\s*,\s*([-+]?\d+(?:\.\d+)?)",
            flags=re.IGNORECASE,
        )
        for match in coordinates_pattern.finditer(html):
            x_value = float(match.group(1))
            y_value = float(match.group(2))
            if abs(x_value) > 180 or abs(y_value) > 90:
                return True

        return False

    def _html_has_invalid_css_color_tuples(self, html: str) -> bool:
        """Detect Python/Matplotlib RGBA tuples accidentally written as CSS colors."""

        return bool(
            re.search(
                r"background(?:-color)?\s*:\s*\(\s*[-+]?\d+(?:\.\d+)?\s*,\s*[-+]?\d+(?:\.\d+)?\s*,\s*[-+]?\d+(?:\.\d+)?",
                html,
                flags=re.IGNORECASE,
            )
        )

    def _repair_css_color_tuples(self, html: str) -> str:
        """Convert CSS declarations like background:(0.5,0.2,0.1,1) to rgba(...)."""

        color_tuple_pattern = re.compile(
            r"(?P<property>background(?:-color)?\s*:\s*)\(\s*"
            r"(?P<r>[-+]?\d+(?:\.\d+)?)\s*,\s*"
            r"(?P<g>[-+]?\d+(?:\.\d+)?)\s*,\s*"
            r"(?P<b>[-+]?\d+(?:\.\d+)?)"
            r"(?:\s*,\s*(?P<a>[-+]?\d+(?:\.\d+)?))?\s*\)",
            flags=re.IGNORECASE,
        )

        def clamp(value: float, lower: float, upper: float) -> float:
            return max(lower, min(upper, value))

        def replacement(match: re.Match[str]) -> str:
            red = float(match.group("r"))
            green = float(match.group("g"))
            blue = float(match.group("b"))
            alpha = float(match.group("a")) if match.group("a") is not None else 1.0

            if max(abs(red), abs(green), abs(blue)) <= 1.0:
                red, green, blue = red * 255, green * 255, blue * 255

            red_i = int(round(clamp(red, 0, 255)))
            green_i = int(round(clamp(green, 0, 255)))
            blue_i = int(round(clamp(blue, 0, 255)))
            alpha_value = clamp(alpha, 0, 1)
            return f"{match.group('property')}rgba({red_i}, {green_i}, {blue_i}, {alpha_value:.3g})"

        return color_tuple_pattern.sub(replacement, html)

    def _professional_title(self, query: str) -> str:
        words = re.findall(r"[A-Za-z0-9]+", query)
        stop_words = {
            "create",
            "make",
            "generate",
            "show",
            "map",
            "interactive",
            "web",
            "please",
            "with",
            "the",
            "and",
            "for",
            "of",
        }
        selected = [word for word in words if word.lower() not in stop_words][:8]
        if not selected:
            return "Interactive Geospatial Map"
        return " ".join(selected).title()

    def _validate_html_output(self, query: str, output_path: str) -> tuple[bool, list[str]]:
        path = Path(output_path)
        if not path.is_file():
            return False, ["The HTML output file was not created."]

        html = path.read_text(encoding="utf-8", errors="ignore")
        normalized = html.lower()
        issues: list[str] = []

        if not self._explicitly_omits(query, "layer control"):
            has_layer_control = any(
                token in normalized
                for token in (
                    "leaflet-control-layers",
                    "l.control.layers(",
                    "folium.layercontrol",
                )
            )
            if not has_layer_control:
                issues.append(
                    "The app must include a real Leaflet/Folium layer control for toggling layers and basemaps."
                )

        if not self._explicitly_omits(query, "title"):
            has_visible_title = bool(
                re.search(r"<h[1-3][^>]*>[^<]{4,}</h[1-3]>", html, re.IGNORECASE)
                or re.search(r"class=[\"'][^\"']*(title|map-title)[^\"']*[\"']", html, re.IGNORECASE)
                or re.search(r"id=[\"'][^\"']*(title|map-title)[^\"']*[\"']", html, re.IGNORECASE)
            )
            if not has_visible_title:
                issues.append("The map must include a professional visible title, not only a browser title.")

        if self._requires_legend(query) and "legend" not in normalized:
            issues.append("The app must include a clear legend when requested or when using value-based symbology.")

        if self._html_has_invalid_leaflet_bounds(html):
            issues.append(
                "The app appears to use projected coordinates in Leaflet bounds. Web map layers and bounds must be EPSG:4326 longitude/latitude."
            )

        if self._html_has_projected_geojson_coordinates(html):
            issues.append(
                "The app appears to embed projected GeoJSON coordinates. Leaflet/Folium vector data must be converted to EPSG:4326 longitude/latitude before rendering."
            )

        if self._html_has_invalid_css_color_tuples(html):
            issues.append(
                "The app legend appears to use Python/Matplotlib color tuples instead of valid CSS colors. Legend swatches must use hex, rgb(...), or rgba(...)."
            )

        return not issues, issues

    def _postprocess_html_output(self, output_path: str) -> None:
        path = Path(output_path)
        if not path.is_file():
            return

        html = path.read_text(encoding="utf-8", errors="ignore")
        html = self._repair_css_color_tuples(html)
        html = re.sub(
            r"\s*<style\s+id=[\"']gas-web-map-app-safety-styles[\"'][^>]*>.*?</style>",
            "",
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        html = re.sub(
            r"\s*<script\s+id=[\"']gas-web-map-app-safety-script[\"'][^>]*>.*?</script>",
            "",
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )

        safety_css = """
<style id="gas-web-map-app-safety-styles">
  .leaflet-top.leaflet-right {
    top: 88px !important;
    right: 16px !important;
    z-index: 10020 !important;
  }
  .leaflet-control-layers {
    display: block !important;
    visibility: visible !important;
    opacity: 1 !important;
    max-width: 320px !important;
    max-height: 42vh !important;
    overflow: auto !important;
    background: rgba(255, 255, 255, 0.97) !important;
    border: 1px solid #9ca3af !important;
    border-radius: 4px !important;
    box-shadow: 0 2px 10px rgba(0,0,0,0.18) !important;
    font: 13px/1.35 Arial, sans-serif !important;
  }
  .legend, .map-legend, .info.legend, .branca-colormap {
    position: fixed !important;
    right: 18px !important;
    bottom: 24px !important;
    left: auto !important;
    top: auto !important;
    z-index: 10010 !important;
    width: min(340px, calc(100vw - 48px)) !important;
    max-width: min(340px, calc(100vw - 48px)) !important;
    max-height: 36vh !important;
    overflow: auto !important;
    background: rgba(255, 255, 255, 0.97) !important;
    border: 1px solid #9ca3af !important;
    border-radius: 4px !important;
    box-shadow: 0 2px 10px rgba(0,0,0,0.18) !important;
    padding: 10px 12px !important;
    font: 13px/1.35 Arial, sans-serif !important;
    color: #111827 !important;
  }
  .legend svg, .map-legend svg, .info.legend svg, .branca-colormap svg {
    width: 100% !important;
    max-width: 300px !important;
    height: auto !important;
  }
  .legend img, .map-legend img, .info.legend img, .branca-colormap img {
    max-width: 300px !important;
    height: auto !important;
  }
  .legend text,
  .map-legend text,
  .info.legend text,
  .branca-colormap text {
    font-size: 10px !important;
  }
  #legend-container .legend,
  #legend-container .map-legend,
  #legend-container .info.legend,
  #legend-container .branca-colormap {
    position: relative !important;
    right: auto !important;
    bottom: auto !important;
    left: auto !important;
    top: auto !important;
    z-index: auto !important;
    max-width: 100% !important;
    max-height: none !important;
    box-shadow: none !important;
    margin: 0 !important;
  }
</style>
<script id="gas-web-map-app-safety-script">
  window.addEventListener("load", function () {
    const applyLegendLayout = function (el) {
      el.style.setProperty("position", "fixed", "important");
      el.style.setProperty("right", "18px", "important");
      el.style.setProperty("bottom", "24px", "important");
      el.style.setProperty("left", "auto", "important");
      el.style.setProperty("top", "auto", "important");
      el.style.setProperty("z-index", "10010", "important");
      el.style.setProperty("width", "min(340px, calc(100vw - 48px))", "important");
      el.style.setProperty("max-width", "min(340px, calc(100vw - 48px))", "important");
      el.style.setProperty("max-height", "36vh", "important");
      el.style.setProperty("overflow", "auto", "important");
      el.style.setProperty("background", "rgba(255,255,255,0.97)", "important");
      el.style.setProperty("border", "1px solid #9ca3af", "important");
      el.style.setProperty("border-radius", "4px", "important");
      el.style.setProperty("box-shadow", "0 2px 10px rgba(0,0,0,0.18)", "important");
      el.style.setProperty("padding", "10px 12px", "important");
    };
    const repairOverlayLayout = function () {
      const mapEl = document.querySelector(".folium-map, .leaflet-container");
      if (!mapEl) {
        return;
      }
      document.querySelectorAll("#app-container, .app-container, .app-shell").forEach(function (el) {
        if (el.contains(mapEl)) {
          return;
        }
        if (el.classList.contains("app-container") && el.querySelector(".sidebar")) {
          return;
        }
        const rect = el.getBoundingClientRect();
        const consumesViewport = rect.height >= window.innerHeight * 0.6;
        if (consumesViewport) {
          el.style.setProperty("position", "fixed", "important");
          el.style.setProperty("top", "12px", "important");
          el.style.setProperty("left", "12px", "important");
          el.style.setProperty("right", "12px", "important");
          el.style.setProperty("bottom", "auto", "important");
          el.style.setProperty("height", "auto", "important");
          el.style.setProperty("max-height", "34vh", "important");
          el.style.setProperty("width", "auto", "important");
          el.style.setProperty("overflow", "auto", "important");
          el.style.setProperty("z-index", "900", "important");
          el.style.setProperty("background", "rgba(255,255,255,0.94)", "important");
          el.style.setProperty("border", "1px solid rgba(0,0,0,0.14)", "important");
          el.style.setProperty("border-radius", "8px", "important");
          el.style.setProperty("box-shadow", "0 4px 18px rgba(0,0,0,0.14)", "important");
          el.style.setProperty("pointer-events", "auto", "important");
          el.querySelectorAll("#header, .header, #sidebar, .sidebar, .panel, button, input, select, a").forEach(function (child) {
            child.style.setProperty("pointer-events", "auto", "important");
          });
          document.documentElement.style.setProperty("height", "100%", "important");
          document.body.style.setProperty("height", "100%", "important");
          document.body.style.setProperty("overflow", "hidden", "important");
        }
      });
    };
    const legendSelectors = [
      ".legend",
      ".map-legend",
      ".info.legend",
      ".branca-colormap",
      "[id*='legend' i]",
      "[class*='colorbar' i]",
      "[id*='colorbar' i]",
      "[class*='colormap' i]",
      "[id*='colormap' i]"
    ];
    const isLikelyAppContainer = function (el) {
      const signature = ((el.id || "") + " " + (el.className || "")).toLowerCase();
      if (/app-|app_|shell|sidebar|panel|header|map-wrap|map_container/.test(signature)) {
        return true;
      }
      return Boolean(el.querySelector(".folium-map, .leaflet-container, .app-shell, .map-wrap"));
    };
    document.querySelectorAll(legendSelectors.join(",")).forEach(function (el) {
      if (!isLikelyAppContainer(el) && !el.closest("#legend-container")) {
        applyLegendLayout(el);
      }
    });
    document.querySelectorAll(".leaflet-control-layers").forEach(function (el) {
      el.style.setProperty("display", "block", "important");
      el.style.setProperty("visibility", "visible", "important");
      el.style.setProperty("opacity", "1", "important");
      el.style.setProperty("z-index", "10020", "important");
    });
    repairOverlayLayout();
    setTimeout(repairOverlayLayout, 250);
    setTimeout(repairOverlayLayout, 1000);
  });
</script>
"""
        if re.search(r"</head\s*>", html, flags=re.IGNORECASE):
            html = re.sub(r"</head\s*>", safety_css + "\n</head>", html, count=1, flags=re.IGNORECASE)
        else:
            html = safety_css + "\n" + html
        path.write_text(html, encoding="utf-8")

    def _execute_code(self, code: str, output_path: str) -> tuple[bool, str, str]:
        script_path = Path(self.output_dir) / build_output_filename(
            "web mapping app generated script",
            extension=".py",
            fallback="web_mapping_app_script",
        )
        script_path.write_text(code, encoding="utf-8")
        env = os.environ.copy()
        env["OUTPUT_HTML"] = output_path
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        process = subprocess.run(
            [self._resolve_python_runner(), str(script_path)],
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            env=env,
            cwd=self.output_dir,
        )
        stdout = process.stdout or ""
        stderr = process.stderr or ""
        return process.returncode == 0 and Path(output_path).is_file(), stdout, stderr

    def _fallback_map(self, task: str, dataset_paths: list[str], output_path: str) -> tuple[bool, list[str]]:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        mapped_layers: list[str] = []
        layer_payloads: list[dict[str, Any]] = []
        bounds: list[float] | None = None

        for path in dataset_paths:
            dataset = Path(path)
            if dataset.suffix.lower() not in {".geojson", ".json", ".gpkg", ".shp", ".gml", ".kml"}:
                continue
            try:
                gdf = gpd.read_file(dataset)
                if gdf.empty:
                    continue
                if gdf.crs and str(gdf.crs).lower() not in {"epsg:4326", "wgs84"}:
                    gdf = gdf.to_crs("EPSG:4326")
                layer_name = dataset.stem
                minx, miny, maxx, maxy = gdf.total_bounds
                current_bounds = [float(minx), float(miny), float(maxx), float(maxy)]
                if bounds is None:
                    bounds = current_bounds
                else:
                    bounds = [
                        min(bounds[0], current_bounds[0]),
                        min(bounds[1], current_bounds[1]),
                        max(bounds[2], current_bounds[2]),
                        max(bounds[3], current_bounds[3]),
                    ]
                layer_payloads.append({"name": layer_name, "data": json.loads(gdf.to_json())})
                mapped_layers.append(layer_name)
                del gdf
            except Exception as exc:
                self.last_error = f"{self.last_error}\nFallback skipped {dataset.name}: {exc}".strip()

        layer_json = json.dumps(layer_payloads)
        bounds_json = json.dumps(bounds)
        title = self._professional_title(task)
        title_json = json.dumps(title)
        subtitle_json = json.dumps(task)
        layer_count = len(layer_payloads)
        needs_legend = self._requires_choropleth_legend(task)
        html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    html, body, #map {{ height: 100%; margin: 0; }}
    .app-panel {{
      position: fixed; top: 90px; left: 18px; width: 280px; max-width: calc(100vw - 36px);
      z-index: 9998; background: rgba(255, 255, 255, 0.96); border: 1px solid #9ca3af;
      box-shadow: 0 2px 10px rgba(0,0,0,0.16); border-radius: 4px;
      font: 13px/1.45 Arial, sans-serif; color: #1f2937; padding: 12px;
    }}
    .app-panel h2 {{ margin: 0 0 8px; font-size: 14px; color: #111827; }}
    .app-panel dl {{ display: grid; grid-template-columns: auto 1fr; gap: 4px 10px; margin: 0; }}
    .app-panel dt {{ font-weight: 700; color: #374151; }}
    .app-panel dd {{ margin: 0; color: #4b5563; }}
    .map-title {{
      position: fixed; top: 18px; left: 50%; transform: translateX(-50%); z-index: 9999;
      background: rgba(255, 255, 255, 0.96); padding: 10px 16px; border: 1px solid #9ca3af;
      box-shadow: 0 2px 10px rgba(0,0,0,0.16); border-radius: 4px;
      font-family: Arial, sans-serif; text-align: center; max-width: 680px;
    }}
    .map-title h1 {{ margin: 0; font-size: 18px; line-height: 1.2; color: #111827; }}
    .map-title p {{ margin: 4px 0 0; font-size: 12px; color: #4b5563; }}
    .map-legend {{
      position: fixed; bottom: 28px; right: 18px; z-index: 9999;
      background: rgba(255,255,255,0.96); padding: 10px 12px; border: 1px solid #9ca3af;
      box-shadow: 0 2px 10px rgba(0,0,0,0.16); border-radius: 4px;
      font: 13px/1.35 Arial, sans-serif;
    }}
  </style>
</head>
<body>
  <div id="map"></div>
  <div class="map-title"><h1>{title}</h1><p id="map-task"></p></div>
  <aside class="app-panel">
    <h2>Map App Summary</h2>
    <dl>
      <dt>Datasets</dt><dd>{len(dataset_paths)}</dd>
      <dt>Mapped layers</dt><dd>{layer_count}</dd>
      <dt>Interaction</dt><dd>Popups and layer control</dd>
    </dl>
  </aside>
  {"<div class=\"map-legend\"><strong>Legend</strong><br>Fallback layer colors represent input map layers. Use the layer control to toggle visibility.</div>" if needs_legend else ""}
  <script>
    const task = {subtitle_json};
    const layers = {layer_json};
    const bounds = {bounds_json};
    document.getElementById("map-task").textContent = task;
    const map = L.map("map").setView([40, -95], 4);
    const base = L.tileLayer("https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap contributors"
    }}).addTo(map);
    const overlays = {{}};
    function popupContent(properties) {{
      const entries = Object.entries(properties || {{}}).slice(0, 8);
      if (!entries.length) return "No attributes";
      return "<table>" + entries.map(([key, value]) =>
        `<tr><th style="text-align:left;padding-right:6px;">${{key}}</th><td>${{value ?? ""}}</td></tr>`
      ).join("") + "</table>";
    }}
    layers.forEach((layer, index) => {{
      const color = ["#2563eb", "#16a34a", "#dc2626", "#9333ea", "#ea580c"][index % 5];
      overlays[layer.name] = L.geoJSON(layer.data, {{
        style: {{ color, weight: 2, fillOpacity: 0.25 }},
        pointToLayer: (feature, latlng) => L.circleMarker(latlng, {{
          radius: 5, color, fillColor: color, fillOpacity: 0.75
        }}),
        onEachFeature: (feature, lyr) => lyr.bindPopup(popupContent(feature.properties))
      }}).addTo(map);
    }});
    L.control.layers({{ "OpenStreetMap": base }}, overlays, {{ collapsed: false }}).addTo(map);
    if (bounds) {{
      map.fitBounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]], {{ padding: [20, 20] }});
    }}
  </script>
</body>
</html>
"""
        Path(output_path).write_text(html, encoding="utf-8")
        return Path(output_path).is_file(), mapped_layers

    def run(
        self,
        query: str,
        input_dataset_paths: list[str] | str | None = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        start_time = time.time()
        dataset_paths = self.normalize_dataset_paths(input_dataset_paths)
        self.ensure_directory(self.output_dir)
        self.reset_metrics()
        self.input_tokens = 0
        self.output_tokens = 0
        self.generated_code = None
        self.last_error = ""

        self._emit_progress(
            progress_callback,
            stage="start",
            message="I will inspect the input datasets, design the web mapping app, generate the app code, and save an HTML artifact.",
            data={"dataset_count": len(dataset_paths), "max_iterations": self.max_iterations},
        )
        output_path = str(Path(self.output_dir) / build_output_filename(query, extension=".html", fallback="interactive_map"))

        self._emit_progress(
            progress_callback,
            stage="input_inspection",
            message=f"I am inspecting {len(dataset_paths)} dataset reference(s) to identify formats, CRS, geometry types, fields, and bounds.",
            data={"dataset_paths": dataset_paths},
        )
        prepared_dataset_paths = self._prepare_leaflet_dataset_paths(dataset_paths, progress_callback=progress_callback)
        dataset_context = self._dataset_context(prepared_dataset_paths)
        self._emit_progress(
            progress_callback,
            stage="layer_preparation",
            message="Dataset inspection and Leaflet-ready input preparation are complete. I have enough metadata to prepare the web mapping app generation prompt.",
            data={
                "original_dataset_paths": dataset_paths,
                "prepared_dataset_paths": prepared_dataset_paths,
                "dataset_context": [
                    {
                        "name": item.get("name"),
                        "type": item.get("type"),
                        "feature_count": item.get("feature_count"),
                        "crs": item.get("crs"),
                        "geometry_types": item.get("geometry_types"),
                        "has_error": bool(item.get("error")),
                    }
                    for item in dataset_context
                ],
            },
        )

        self._emit_progress(
            progress_callback,
            stage="map_design",
            message="I selected HTML as the primary artifact and prepared the exact output path for the web mapping app.",
            data={"output_path": output_path, "available_libraries": self.available_mapping_libraries},
        )

        messages = self._build_prompt(query, prepared_dataset_paths, dataset_context, output_path)
        if self.client is None:
            self.last_error = "No LLM client was configured; using deterministic fallback map."
            self._emit_progress(
                progress_callback,
                stage="warning",
                message="No LLM client is configured, so I will use the deterministic Leaflet HTML fallback renderer.",
                data={"reason": self.last_error},
            )
        else:
            for iteration in range(self.max_iterations):
                self._emit_progress(
                    progress_callback,
                    stage="llm_generation",
                    message=(
                        f"I am asking the LLM to generate web mapping app code "
                        f"(attempt {iteration + 1} of {self.max_iterations})."
                    ),
                    data={"iteration": iteration + 1, "max_iterations": self.max_iterations},
                )
                self.increment_llm_calls()
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.2,
                )
                usage = getattr(response, "usage", None)
                if usage:
                    self.input_tokens += getattr(usage, "prompt_tokens", 0) or 0
                    self.output_tokens += getattr(usage, "completion_tokens", 0) or 0
                ai_reply = response.choices[0].message.content
                code = self._extract_python_code(ai_reply)
                self._emit_progress(
                    progress_callback,
                    stage="html_generation",
                    message="The LLM returned Python code. I extracted the code block and will execute it in the server runtime.",
                    data={"iteration": iteration + 1, "code_length": len(code)},
                )
                self.increment_tool_calls()
                try:
                    self._emit_progress(
                        progress_callback,
                        stage="code_execution",
                        message=(
                            "I am running the generated code now. This may take time for large datasets "
                            "or complex interactive layers."
                        ),
                        data={"iteration": iteration + 1, "timeout_seconds": self.timeout_seconds},
                    )
                    success, stdout, stderr = self._execute_code(code, output_path)
                    validation_issues: list[str] = []
                    if success:
                        self._postprocess_html_output(output_path)
                        success, validation_issues = self._validate_html_output(query, output_path)
                        if not success:
                            stderr = (
                                f"{stderr}\nOutput validation failed:\n"
                                + "\n".join(f"- {issue}" for issue in validation_issues)
                            ).strip()
                except Exception as exc:
                    success, stdout, stderr = False, "", str(exc)
                if success:
                    self.generated_code = code
                    self._emit_progress(
                        progress_callback,
                        stage="artifact_generation",
                    message="The generated code ran successfully and created the HTML web mapping app artifact.",
                        data={"iteration": iteration + 1, "output_path": output_path},
                    )
                    break
                self.last_error = f"Attempt {iteration + 1} failed.\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
                self._emit_progress(
                    progress_callback,
                    stage="retry",
                    message=(
                        f"The generated code failed on attempt {iteration + 1}. "
                        "I will send the execution or validation feedback back to the LLM and ask it to repair the map."
                    ),
                    data={"iteration": iteration + 1, "stderr_preview": stderr[:800], "stdout_preview": stdout[:400]},
                )
                messages.append({"role": "assistant", "content": ai_reply})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"The code or output validation failed. Fix it and still save to {output_path}.\n"
                            "Remember the default requirements: include a layer control, include a professional visible title, "
                            "and include a legend for choropleth/value-based maps unless explicitly omitted by the user.\n"
                            f"{self.last_error}"
                        ),
                    }
                )

        fallback_used = False
        mapped_layers: list[str] = []
        if not Path(output_path).is_file():
            fallback_used = True
            self._emit_progress(
                progress_callback,
                stage="fallback_start",
                message="Generated code did not produce a valid HTML artifact, so I am creating a deterministic Leaflet web mapping app fallback.",
                data={"output_path": output_path},
            )
            success, mapped_layers = self._fallback_map(query, prepared_dataset_paths, output_path)
            if success:
                self._postprocess_html_output(output_path)
                success, validation_issues = self._validate_html_output(query, output_path)
                if not success:
                    self.last_error = "Fallback output validation failed: " + "; ".join(validation_issues)
            if not success:
                self._emit_progress(
                    progress_callback,
                    stage="error",
                    message="The fallback renderer could not create a usable web mapping app.",
                    data={"error": self.last_error},
                )
                raise RuntimeError(self.last_error or "Interactive map generation failed.")
            self._emit_progress(
                progress_callback,
                stage="fallback_complete",
                message="The deterministic Leaflet fallback created the HTML web mapping app successfully.",
                data={"output_path": output_path, "mapped_layers": mapped_layers},
            )

        used_datasets = [item["name"] for item in dataset_context if item.get("exists")]
        method = "LLM-generated Folium/Leaflet code" if self.generated_code else "deterministic Leaflet HTML fallback"
        summary = (
            f"Generated a browser-ready HTML web mapping app using {method}. "
            f"Used {len(used_datasets)} input dataset(s): {', '.join(used_datasets) if used_datasets else 'none'}. "
            "Included an interactive basemap, layer controls, a professional title, and available vector popups where supported."
        )
        if fallback_used and self.last_error:
            summary += " The fallback renderer was used after generated code did not complete successfully."

        valid_html, validation_issues = self._validate_html_output(query, output_path)
        self._emit_progress(
            progress_callback,
            stage="data_validation",
            message="I verified the HTML output file and required web mapping app elements, then prepared the final structured service response.",
            data={"output_path": output_path, "exists": Path(output_path).is_file(), "valid": valid_html, "issues": validation_issues},
        )

        self._emit_progress(
            progress_callback,
            stage="complete",
            message="Web mapping app workflow is complete. The final response will include the HTML artifact, summary, execution details, and provenance.",
            data={"summary": summary},
        )

        return {
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "model": self.model,
            "duration": round(time.time() - start_time, 2),
            "total_input_tokens": self.input_tokens,
            "total_output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "inputs": {
                "text": query,
                "dataset_paths": dataset_paths,
                "prepared_dataset_paths": prepared_dataset_paths,
                "parameters": {
                    "max_iterations": self.max_iterations,
                    "timeout_seconds": self.timeout_seconds,
                    "output_format": "html",
                },
            },
            "outputs": {
                "text": summary,
                "dataset_path": output_path,
                "dataset_paths": [output_path],
                "dataset_size": {
                    "type": "web_mapping_app",
                    "dimensions": None,
                    "feature_count": None,
                },
                "html_file": output_path,
            },
            "metrics": self.metrics(number_of_artifacts=1),
            "script": self.generated_code,
            "environment": {
                "python_version": platform.python_version(),
                "domain-specific libraries": self.available_mapping_libraries,
            },
            "complementary": {
                "Execution": {
                    "Inputs": {
                        "task": query,
                        "dataset_paths": dataset_paths,
                        "prepared_dataset_paths": prepared_dataset_paths,
                        "dataset_context": dataset_context,
                    },
                    "Outputs": {"summary": summary, "output_path": output_path, "mapped_layers": mapped_layers},
                },
                "Provenance": {
                    "Lineage": [
                        "Inspected input dataset metadata.",
                        "Generated web mapping app code with an LLM." if self.generated_code else "Used deterministic fallback web mapping app renderer.",
                        "Saved the final HTML web mapping app artifact.",
                    ],
                    "Tool Calls": {"count": self.tool_calls},
                    "LLM Calls": {"count": self.llm_calls},
                },
                "Artifacts and Logs": {
                    "Inline Artifacts": {"script": self.generated_code} if self.generated_code else {},
                    "Persisted Artifacts": {"html_file": output_path},
                },
                "Validation": {
                    "status": "passed" if valid_html else "failed",
                    "checks": [
                        "HTML output file exists.",
                        "Layer control is present unless explicitly omitted.",
                        "Visible professional title is present unless explicitly omitted.",
                        "Legend is present for choropleth/value-based maps unless explicitly omitted.",
                    ],
                    "issues": validation_issues,
                },
                "Assumptions and Limitations": {
                    "assumptions": ["Provided dataset paths are accessible to the GAS server runtime."],
                    "limitations": [
                        "Raster rendering in the fallback path is limited; LLM-generated code may add richer raster overlays.",
                        "Large vector layers may produce large HTML files.",
                    ],
                },
            },
        }
