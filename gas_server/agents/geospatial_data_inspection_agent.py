from __future__ import annotations

import html
import importlib.util
import json
import platform
import time
from pathlib import Path
from typing import Any, Callable

import geopandas as gpd
import pandas as pd

from gas_server.core.file_naming import build_output_filename
from gas_server.core.geo_agent import GeoAgent
from gas_server.core.llm_client import build_llm_client, format_service_name
from gas_server.core.config import DATA_DIR, ensure_runtime_dirs


ensure_runtime_dirs()


VECTOR_EXTENSIONS = {".geojson", ".json", ".gpkg", ".shp", ".gml", ".kml"}
RASTER_EXTENSIONS = {".tif", ".tiff", ".vrt", ".img"}
TABLE_EXTENSIONS = {".csv", ".tsv", ".txt"}
NULL_WARNING_THRESHOLD = 0.25


class GeospatialDataInspectionAgent(GeoAgent):
    agent_id = "geospatial_data_inspection_agent"
    agent_name = "Geospatial Data Inspection Agent"
    agent_version = "1.0.0"
    agent_description = (
        "Inspects vector, raster, and tabular geospatial datasets for quality, "
        "spatial readiness, and workflow suitability."
    )
    requires_input_datasets = True

    def __init__(self, api_key: str | None = None, model: str | None = None):
        super().__init__(
            api_key=api_key,
            model=model or "gpt-5.2",
            output_dir=DATA_DIR / self.agent_id,
        )
        self.service_name = format_service_name(self.agent_name)
        self.client = build_llm_client(service_name=self.service_name, openai_api_key=self.api_key)
        self.available_libraries = self._available_libraries()

    def _available_libraries(self) -> list[str]:
        candidates = ("geopandas", "pandas", "rasterio", "shapely", "pyproj")
        return [name for name in candidates if importlib.util.find_spec(name) is not None]

    def _status(self, passed: bool, warning: bool = False) -> str:
        if passed:
            return "passed"
        return "warning" if warning else "failed"

    def _check(self, name: str, status: str, message: str, **details: Any) -> dict[str, Any]:
        payload = {"name": name, "status": status, "message": message}
        payload.update(details)
        return payload

    def _read_table(self, path: Path) -> pd.DataFrame:
        separator = "\t" if path.suffix.lower() == ".tsv" else ","
        return pd.read_csv(path, sep=separator)

    def _find_coordinate_columns(self, columns: list[str]) -> tuple[str | None, str | None]:
        lower_to_original = {column.lower(): column for column in columns}
        x_candidates = ("longitude", "lon", "lng", "x", "long")
        y_candidates = ("latitude", "lat", "y")
        x_col = next((lower_to_original[name] for name in x_candidates if name in lower_to_original), None)
        y_col = next((lower_to_original[name] for name in y_candidates if name in lower_to_original), None)
        return x_col, y_col

    def _join_key_candidates(self, columns: list[str]) -> list[str]:
        key_tokens = ("id", "geoid", "geo_id", "fips", "countyfp", "tractce", "join")
        return [
            column
            for column in columns
            if any(token in column.lower() for token in key_tokens)
        ][:10]

    def _null_summary(self, df: pd.DataFrame) -> dict[str, Any]:
        if df.empty:
            return {"columns_with_nulls": {}, "high_null_columns": []}
        null_rates = (df.isna().sum() / len(df)).sort_values(ascending=False)
        columns_with_nulls = {
            str(column): round(float(rate), 4)
            for column, rate in null_rates.items()
            if rate > 0
        }
        high_null_columns = [
            column
            for column, rate in columns_with_nulls.items()
            if rate >= NULL_WARNING_THRESHOLD
        ]
        return {
            "columns_with_nulls": columns_with_nulls,
            "high_null_columns": high_null_columns,
        }

    def _inspect_vector(self, path: Path) -> dict[str, Any]:
        gdf = gpd.read_file(path)
        feature_count = int(len(gdf))
        geometry = gdf.geometry if "geometry" in gdf else None
        geometry_types = (
            sorted(str(value) for value in geometry.geom_type.dropna().unique())
            if geometry is not None
            else []
        )
        empty_geometry_count = int(geometry.is_empty.fillna(False).sum()) if geometry is not None else 0
        null_geometry_count = int(geometry.isna().sum()) if geometry is not None else feature_count
        invalid_geometry_count = int((~geometry.is_valid.fillna(False)).sum()) if geometry is not None else feature_count
        duplicate_feature_count = int(gdf.astype(str).duplicated().sum()) if feature_count else 0
        bounds = [float(value) for value in gdf.total_bounds] if feature_count and geometry is not None else None
        crs = str(gdf.crs) if gdf.crs else None
        is_projected = bool(gdf.crs and gdf.crs.is_projected)
        nulls = self._null_summary(gdf.drop(columns=["geometry"], errors="ignore"))
        columns = [str(column) for column in gdf.columns]

        checks = [
            self._check(
                "readability",
                "passed",
                "Dataset was opened successfully with GeoPandas.",
            ),
            self._check(
                "crs_presence",
                self._status(bool(crs)),
                "Coordinate reference system is present." if crs else "Coordinate reference system is missing.",
                crs=crs,
            ),
            self._check(
                "feature_count",
                self._status(feature_count > 0),
                f"Dataset contains {feature_count} feature(s).",
                feature_count=feature_count,
            ),
            self._check(
                "geometry_presence",
                self._status(geometry is not None and null_geometry_count < feature_count),
                "Geometry column is usable." if geometry is not None else "Geometry column is missing.",
                null_geometry_count=null_geometry_count,
                empty_geometry_count=empty_geometry_count,
            ),
            self._check(
                "geometry_validity",
                self._status(invalid_geometry_count == 0, warning=True),
                "All geometries are valid." if invalid_geometry_count == 0 else f"{invalid_geometry_count} invalid geometries were found.",
                invalid_geometry_count=invalid_geometry_count,
            ),
            self._check(
                "geometry_type_consistency",
                self._status(len(geometry_types) <= 1, warning=True),
                "Geometry type is consistent." if len(geometry_types) <= 1 else "Multiple geometry types were found.",
                geometry_types=geometry_types,
            ),
            self._check(
                "duplicate_features",
                self._status(duplicate_feature_count == 0, warning=True),
                "No duplicate features detected." if duplicate_feature_count == 0 else f"{duplicate_feature_count} duplicate feature(s) detected.",
                duplicate_feature_count=duplicate_feature_count,
            ),
            self._check(
                "attribute_completeness",
                self._status(not nulls["high_null_columns"], warning=True),
                "No high-null attribute columns detected." if not nulls["high_null_columns"] else "Some attribute columns have substantial missing values.",
                high_null_columns=nulls["high_null_columns"],
            ),
            self._check(
                "bbox_validity",
                self._status(bool(bounds and bounds[0] <= bounds[2] and bounds[1] <= bounds[3])),
                "Bounding box is numeric and ordered." if bounds else "Bounding box could not be extracted.",
            ),
        ]

        return {
            "dataset": path.name,
            "path": str(path),
            "type": "vector",
            "format": path.suffix.lower().lstrip("."),
            "metadata": {
                "feature_count": feature_count,
                "columns": columns,
                "schema": {str(column): str(dtype) for column, dtype in gdf.dtypes.items()},
                "crs": crs,
                "bbox": bounds,
                "geometry_types": geometry_types,
                "join_key_candidates": self._join_key_candidates(columns),
                "null_summary": nulls,
            },
            "checks": checks,
            "suitability": {
                "mapping_ready": feature_count > 0 and geometry is not None and null_geometry_count < feature_count,
                "spatial_join_ready": bool(crs and feature_count > 0 and geometry is not None and null_geometry_count < feature_count),
                "distance_analysis_ready": bool(is_projected and invalid_geometry_count == 0 and null_geometry_count == 0),
                "needs_reprojection": not is_projected,
                "needs_cleaning": any(
                    [
                        invalid_geometry_count > 0,
                        null_geometry_count > 0,
                        empty_geometry_count > 0,
                        duplicate_feature_count > 0,
                        bool(nulls["high_null_columns"]),
                    ]
                ),
            },
        }

    def _inspect_raster(self, path: Path) -> dict[str, Any]:
        import rasterio

        with rasterio.open(path) as src:
            crs = str(src.crs) if src.crs else None
            bounds = [float(src.bounds.left), float(src.bounds.bottom), float(src.bounds.right), float(src.bounds.top)]
            metadata = {
                "width": int(src.width),
                "height": int(src.height),
                "band_count": int(src.count),
                "crs": crs,
                "bbox": bounds,
                "resolution": [float(src.res[0]), float(src.res[1])],
                "nodata": src.nodata,
                "dtypes": [str(dtype) for dtype in src.dtypes],
                "transform": str(src.transform),
            }
            readable = src.width > 0 and src.height > 0 and src.count > 0
            has_nodata = src.nodata is not None

        checks = [
            self._check("readability", "passed", "Dataset was opened successfully with Rasterio."),
            self._check(
                "crs_presence",
                self._status(bool(crs)),
                "Coordinate reference system is present." if crs else "Coordinate reference system is missing.",
                crs=crs,
            ),
            self._check(
                "raster_dimensions",
                self._status(readable),
                f"Raster dimensions are {metadata['width']} by {metadata['height']} with {metadata['band_count']} band(s).",
            ),
            self._check(
                "nodata_definition",
                self._status(has_nodata, warning=True),
                "NoData value is defined." if has_nodata else "NoData value is not defined.",
                nodata=metadata["nodata"],
            ),
            self._check(
                "bbox_validity",
                self._status(bounds[0] <= bounds[2] and bounds[1] <= bounds[3]),
                "Bounding box is numeric and ordered.",
            ),
        ]

        return {
            "dataset": path.name,
            "path": str(path),
            "type": "raster",
            "format": path.suffix.lower().lstrip("."),
            "metadata": metadata,
            "checks": checks,
            "suitability": {
                "mapping_ready": readable,
                "spatial_overlay_ready": bool(readable and crs),
                "distance_analysis_ready": False,
                "needs_reprojection": not bool(crs),
                "needs_cleaning": not bool(crs) or not has_nodata,
            },
        }

    def _inspect_table(self, path: Path) -> dict[str, Any]:
        df = self._read_table(path)
        columns = [str(column) for column in df.columns]
        x_col, y_col = self._find_coordinate_columns(columns)
        join_keys = self._join_key_candidates(columns)
        duplicate_row_count = int(df.duplicated().sum())
        nulls = self._null_summary(df)
        bbox = None
        coordinate_valid = False
        if x_col and y_col:
            x = pd.to_numeric(df[x_col], errors="coerce")
            y = pd.to_numeric(df[y_col], errors="coerce")
            valid_coordinates = x.notna() & y.notna() & x.between(-180, 180) & y.between(-90, 90)
            coordinate_valid = bool(valid_coordinates.any())
            if coordinate_valid:
                bbox = [
                    float(x[valid_coordinates].min()),
                    float(y[valid_coordinates].min()),
                    float(x[valid_coordinates].max()),
                    float(y[valid_coordinates].max()),
                ]

        checks = [
            self._check("readability", "passed", "Dataset was opened successfully with Pandas."),
            self._check(
                "row_count",
                self._status(len(df) > 0),
                f"Table contains {len(df)} row(s).",
                row_count=int(len(df)),
            ),
            self._check(
                "coordinate_columns",
                self._status(coordinate_valid, warning=True),
                "Usable coordinate columns were detected." if coordinate_valid else "No complete longitude/latitude coordinate pair was detected.",
                x_column=x_col,
                y_column=y_col,
            ),
            self._check(
                "join_key_candidates",
                self._status(bool(join_keys), warning=True),
                "Potential join key columns were detected." if join_keys else "No obvious join key columns were detected.",
                join_key_candidates=join_keys,
            ),
            self._check(
                "duplicate_rows",
                self._status(duplicate_row_count == 0, warning=True),
                "No duplicate rows detected." if duplicate_row_count == 0 else f"{duplicate_row_count} duplicate row(s) detected.",
                duplicate_row_count=duplicate_row_count,
            ),
            self._check(
                "attribute_completeness",
                self._status(not nulls["high_null_columns"], warning=True),
                "No high-null columns detected." if not nulls["high_null_columns"] else "Some columns have substantial missing values.",
                high_null_columns=nulls["high_null_columns"],
            ),
        ]

        return {
            "dataset": path.name,
            "path": str(path),
            "type": "table",
            "format": path.suffix.lower().lstrip("."),
            "metadata": {
                "row_count": int(len(df)),
                "column_count": int(len(df.columns)),
                "columns": columns,
                "schema": {str(column): str(dtype) for column, dtype in df.dtypes.items()},
                "coordinate_columns": {"x": x_col, "y": y_col},
                "join_key_candidates": join_keys,
                "bbox": bbox,
                "null_summary": nulls,
            },
            "checks": checks,
            "suitability": {
                "mapping_ready": coordinate_valid,
                "spatial_join_ready": bool(coordinate_valid or join_keys),
                "distance_analysis_ready": coordinate_valid,
                "needs_reprojection": False,
                "needs_cleaning": duplicate_row_count > 0 or bool(nulls["high_null_columns"]),
            },
        }

    def _inspect_dataset(self, path_str: str) -> dict[str, Any]:
        path = Path(path_str)
        base = {
            "dataset": path.name or str(path),
            "path": str(path),
            "type": "unknown",
            "format": path.suffix.lower().lstrip("."),
        }
        if not path.exists():
            return {
                **base,
                "checks": [
                    self._check("readability", "failed", "Dataset path does not exist.", path=str(path)),
                ],
                "metadata": {},
                "suitability": {
                    "mapping_ready": False,
                    "spatial_join_ready": False,
                    "distance_analysis_ready": False,
                    "needs_reprojection": False,
                    "needs_cleaning": True,
                },
            }

        try:
            suffix = path.suffix.lower()
            if suffix in VECTOR_EXTENSIONS:
                return self._inspect_vector(path)
            if suffix in RASTER_EXTENSIONS:
                return self._inspect_raster(path)
            if suffix in TABLE_EXTENSIONS:
                return self._inspect_table(path)
            return {
                **base,
                "checks": [
                    self._check("format_support", "warning", "File format is not recognized by the built-in quality inspector."),
                ],
                "metadata": {"size_bytes": path.stat().st_size},
                "suitability": {
                    "mapping_ready": False,
                    "spatial_join_ready": False,
                    "distance_analysis_ready": False,
                    "needs_reprojection": False,
                    "needs_cleaning": True,
                },
            }
        except Exception as exc:
            return {
                **base,
                "checks": [
                    self._check("readability", "failed", f"Dataset could not be inspected: {exc}"),
                ],
                "metadata": {},
                "suitability": {
                    "mapping_ready": False,
                    "spatial_join_ready": False,
                    "distance_analysis_ready": False,
                    "needs_reprojection": False,
                    "needs_cleaning": True,
                },
            }

    def _overall_status(self, inspections: list[dict[str, Any]]) -> str:
        if not inspections:
            return "failed"
        statuses = [
            check.get("status")
            for inspection in inspections
            for check in inspection.get("checks", [])
        ]
        if any(status == "failed" for status in statuses):
            return "failed"
        if any(status == "warning" for status in statuses):
            return "warning"
        return "passed"

    def _quality_score(self, inspections: list[dict[str, Any]]) -> int:
        checks = [
            check.get("status")
            for inspection in inspections
            for check in inspection.get("checks", [])
        ]
        if not checks:
            return 0
        score = sum(1.0 if status == "passed" else 0.5 if status == "warning" else 0.0 for status in checks)
        return round(100 * score / len(checks))

    def _recommendations(self, inspections: list[dict[str, Any]]) -> list[str]:
        if not inspections:
            return ["Provide at least one dataset in input_datasets so the agent can run quality checks."]
        recommendations: list[str] = []
        for inspection in inspections:
            name = inspection.get("dataset", "dataset")
            suitability = inspection.get("suitability", {})
            if suitability.get("needs_reprojection"):
                recommendations.append(f"Define or reproject CRS for {name}; use a projected CRS before distance or area analysis.")
            if suitability.get("needs_cleaning"):
                recommendations.append(f"Clean {name} before production use; review failed and warning checks in the report.")
            if not suitability.get("spatial_join_ready"):
                recommendations.append(f"Add usable geometry, coordinates, or join keys before using {name} in a spatial join.")
        return recommendations or ["No blocking quality issues were detected by the built-in checks."]

    def _fallback_synthesis(
        self,
        query: str,
        inspections: list[dict[str, Any]],
        overall_status: str,
        quality_score: int,
        recommendations: list[str],
    ) -> dict[str, Any]:
        if not inspections:
            return {
                "workflow_answer": "No input datasets were provided, so the requested quality and readiness question cannot be answered.",
                "readiness": "not_ready",
                "key_findings": ["No datasets were available for inspection."],
                "recommended_actions": recommendations,
                "assumptions": ["The request should include one or more datasets in input_datasets."],
                "limitations": ["No data-specific conclusions can be made without input datasets."],
            }

        not_ready = [
            inspection.get("dataset")
            for inspection in inspections
            if not inspection.get("suitability", {}).get("mapping_ready")
            or not inspection.get("suitability", {}).get("spatial_join_ready")
            or inspection.get("suitability", {}).get("needs_cleaning")
        ]
        readiness = "ready" if overall_status == "passed" and not not_ready else "needs_review"
        if any(
            check.get("status") == "failed"
            for inspection in inspections
            for check in inspection.get("checks", [])
        ):
            readiness = "not_ready"

        key_findings = [
            f"{inspection.get('dataset')} is a {inspection.get('type')} dataset with suitability {inspection.get('suitability')}."
            for inspection in inspections
        ]
        answer = (
            f"Based on deterministic inspection, the datasets are {readiness.replace('_', ' ')} "
            f"for the requested workflow. Overall quality status is {overall_status} "
            f"with a score of {quality_score}/100."
        )
        if "spatial join" in query.lower() and len(inspections) >= 2:
            answer += " Review CRS compatibility and geometry roles before running the spatial join."
        if "mapping" in query.lower():
            answer += " For interactive mapping, prioritize datasets marked mapping_ready and fields suitable for popups or styling."

        return {
            "workflow_answer": answer,
            "readiness": readiness,
            "key_findings": key_findings,
            "recommended_actions": recommendations,
            "assumptions": ["Readiness is inferred from inspected metadata, validation checks, and the user's stated workflow."],
            "limitations": ["This fallback synthesis uses deterministic rules and does not replace model-assisted interpretation."],
        }

    def _extract_json_object(self, text: str | None) -> dict[str, Any]:
        if not text:
            return {}
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = stripped.strip("`")
            stripped = stripped.removeprefix("json").strip()
        try:
            parsed = json.loads(stripped)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            pass
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(stripped[start : end + 1])
                return parsed if isinstance(parsed, dict) else {}
            except ValueError:
                return {}
        return {}

    def _synthesize_workflow_assessment(
        self,
        query: str,
        inspections: list[dict[str, Any]],
        overall_status: str,
        quality_score: int,
        recommendations: list[str],
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        fallback = self._fallback_synthesis(query, inspections, overall_status, quality_score, recommendations)
        if self.client is None or not inspections:
            self._emit_progress(
                progress_callback,
                stage="fallback_start",
                message="No LLM client is configured, so I will use deterministic workflow-readiness synthesis.",
                data={"readiness": fallback["readiness"]},
            )
            return fallback

        compact_inspections = [
            {
                "dataset": inspection.get("dataset"),
                "type": inspection.get("type"),
                "format": inspection.get("format"),
                "metadata": inspection.get("metadata"),
                "checks": inspection.get("checks"),
                "suitability": inspection.get("suitability"),
            }
            for inspection in inspections
        ]
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a geospatial data inspection expert. Interpret deterministic inspection results "
                    "against the user's stated workflow. Do not invent facts that are not supported by the "
                    "inspection results. Return only JSON with keys: workflow_answer, readiness, key_findings, "
                    "recommended_actions, assumptions, limitations. readiness must be one of ready, needs_review, not_ready."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "user_request": query,
                        "overall_status": overall_status,
                        "quality_score": quality_score,
                        "deterministic_recommendations": recommendations,
                        "inspections": compact_inspections,
                    },
                    default=str,
                ),
            },
        ]
        self._emit_progress(
            progress_callback,
            stage="planning",
            message="I am asking the LLM to interpret the inspection results against the user's workflow question.",
            data={"model": self.model},
        )
        try:
            self.increment_llm_calls()
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.1,
            )
            usage = getattr(response, "usage", None)
            if usage:
                self.input_tokens += getattr(usage, "prompt_tokens", 0) or 0
                self.output_tokens += getattr(usage, "completion_tokens", 0) or 0
            content = response.choices[0].message.content
            synthesis = self._extract_json_object(content)
            if not synthesis:
                raise ValueError("LLM response did not contain a JSON object.")
            merged = {**fallback, **synthesis}
            if merged.get("readiness") not in {"ready", "needs_review", "not_ready"}:
                merged["readiness"] = fallback["readiness"]
            for key in ("key_findings", "recommended_actions", "assumptions", "limitations"):
                if not isinstance(merged.get(key), list):
                    merged[key] = fallback[key]
            if not merged.get("workflow_answer"):
                merged["workflow_answer"] = fallback["workflow_answer"]
            self._emit_progress(
                progress_callback,
                stage="method_selection",
                message="The LLM synthesized a workflow-specific readiness answer from the inspection results.",
                data={"readiness": merged["readiness"]},
            )
            return merged
        except Exception as exc:
            fallback["limitations"] = [
                *fallback.get("limitations", []),
                f"LLM synthesis was unavailable or failed, so deterministic synthesis was used: {exc}",
            ]
            self._emit_progress(
                progress_callback,
                stage="fallback_start",
                message="LLM synthesis was unavailable or failed, so I used deterministic workflow-readiness synthesis.",
                data={"error": str(exc), "readiness": fallback["readiness"]},
            )
            return fallback

    def _write_txt_report(
        self,
        query: str,
        inspections: list[dict[str, Any]],
        overall_status: str,
        quality_score: int,
        recommendations: list[str],
        workflow_assessment: dict[str, Any],
    ) -> str:
        path = Path(self.output_dir) / build_output_filename(query, extension=".txt", fallback="quality_report")
        lines = [
            "Geospatial Data Inspection Report",
            "=" * 31,
            "",
            f"User request: {query}",
            f"Overall status: {overall_status}",
            f"Quality score: {quality_score}/100",
            f"Datasets inspected: {len(inspections)}",
            "",
            "Workflow-Specific Assessment",
            "----------------------------",
            str(workflow_assessment.get("workflow_answer", "")),
            f"Readiness: {workflow_assessment.get('readiness')}",
            "",
            "Key findings:",
        ]
        lines.extend(f"- {item}" for item in workflow_assessment.get("key_findings", []))
        lines.extend(
            [
                "",
                "Recommended workflow actions:",
            ]
        )
        lines.extend(f"- {item}" for item in workflow_assessment.get("recommended_actions", []))
        lines.extend(
            [
                "",
                "Recommendations",
                "---------------",
            ]
        )
        lines.extend(f"- {item}" for item in recommendations)
        for inspection in inspections:
            lines.extend(
                [
                    "",
                    f"Dataset: {inspection.get('dataset')}",
                    "-" * (9 + len(str(inspection.get("dataset")))),
                    f"Type: {inspection.get('type')}",
                    f"Format: {inspection.get('format')}",
                    f"Suitability: {json.dumps(inspection.get('suitability', {}), indent=2)}",
                    "Checks:",
                ]
            )
            for check in inspection.get("checks", []):
                lines.append(f"- {check.get('name')}: {check.get('status')} - {check.get('message')}")
            lines.append("Metadata:")
            lines.append(json.dumps(inspection.get("metadata", {}), indent=2, default=str))
        path.write_text("\n".join(lines), encoding="utf-8")
        return str(path)

    def _write_html_report(
        self,
        query: str,
        inspections: list[dict[str, Any]],
        overall_status: str,
        quality_score: int,
        recommendations: list[str],
        workflow_assessment: dict[str, Any],
    ) -> str:
        path = Path(self.output_dir) / build_output_filename(query, extension=".html", fallback="quality_report")
        status_class = overall_status if overall_status in {"passed", "warning", "failed"} else "warning"
        workflow_answer = html.escape(str(workflow_assessment.get("workflow_answer", "")))
        readiness = html.escape(str(workflow_assessment.get("readiness", "needs_review")).replace("_", " ").title())
        finding_items = "".join(
            f"<li>{html.escape(str(item))}</li>"
            for item in workflow_assessment.get("key_findings", [])
        )
        workflow_action_items = "".join(
            f"<li>{html.escape(str(item))}</li>"
            for item in workflow_assessment.get("recommended_actions", [])
        )
        cards = []
        for inspection in inspections:
            check_rows = "".join(
                "<tr>"
                f"<td>{html.escape(str(check.get('name')))}</td>"
                f"<td><span class=\"pill {html.escape(str(check.get('status')))}\">{html.escape(str(check.get('status')))}</span></td>"
                f"<td>{html.escape(str(check.get('message')))}</td>"
                "</tr>"
                for check in inspection.get("checks", [])
            )
            metadata = html.escape(json.dumps(inspection.get("metadata", {}), indent=2, default=str))
            suitability_items = "".join(
                f"<li><strong>{html.escape(str(key))}</strong>: {html.escape(str(value))}</li>"
                for key, value in inspection.get("suitability", {}).items()
            )
            cards.append(
                f"""
                <section class="dataset">
                  <header>
                    <h2>{html.escape(str(inspection.get("dataset")))}</h2>
                    <div>
                      <span class="chip">{html.escape(str(inspection.get("type")))}</span>
                      <span class="chip">{html.escape(str(inspection.get("format")))}</span>
                    </div>
                  </header>
                  <div class="split">
                    <div>
                      <h3>Quality Checks</h3>
                      <table>
                        <thead><tr><th>Check</th><th>Status</th><th>Message</th></tr></thead>
                        <tbody>{check_rows}</tbody>
                      </table>
                    </div>
                    <aside>
                      <h3>Workflow Suitability</h3>
                      <ul>{suitability_items}</ul>
                    </aside>
                  </div>
                  <details>
                    <summary>Metadata inspection</summary>
                    <pre>{metadata}</pre>
                  </details>
                </section>
                """
            )
        recommendation_items = "".join(f"<li>{html.escape(item)}</li>" for item in recommendations)
        html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Geospatial Data Inspection Report</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #172033;
      --muted: #596579;
      --line: #d7dce5;
      --panel: #ffffff;
      --passed: #0f766e;
      --warning: #b45309;
      --failed: #b91c1c;
      --bg: #f5f7fb;
    }}
    body {{
      margin: 0;
      font-family: Arial, Helvetica, sans-serif;
      color: var(--ink);
      background: var(--bg);
      line-height: 1.45;
    }}
    .wrap {{ max-width: 1160px; margin: 0 auto; padding: 28px; }}
    .hero {{ border-bottom: 1px solid var(--line); padding-bottom: 22px; margin-bottom: 22px; }}
    h1 {{ margin: 0 0 8px; font-size: 30px; }}
    h2 {{ margin: 0; font-size: 20px; }}
    h3 {{ margin: 0 0 10px; font-size: 15px; color: var(--muted); text-transform: uppercase; }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      margin: 18px 0;
    }}
    .metric, .dataset {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 6px;
      box-shadow: 0 1px 3px rgba(23, 32, 51, 0.06);
    }}
    .metric {{ padding: 16px; }}
    .metric strong {{ display: block; font-size: 26px; }}
    .metric span {{ color: var(--muted); font-size: 13px; }}
    .dataset {{ margin: 18px 0; padding: 18px; }}
    .dataset header {{ display: flex; justify-content: space-between; gap: 16px; align-items: center; margin-bottom: 16px; }}
    .split {{ display: grid; grid-template-columns: 1fr 280px; gap: 18px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 8px; text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-size: 12px; text-transform: uppercase; }}
    aside {{ border-left: 3px solid var(--line); padding-left: 16px; }}
    ul {{ margin: 0; padding-left: 18px; }}
    pre {{ overflow: auto; background: #111827; color: #e5e7eb; padding: 14px; border-radius: 4px; }}
    .chip, .pill {{
      display: inline-block;
      border-radius: 999px;
      padding: 3px 9px;
      font-size: 12px;
      border: 1px solid var(--line);
      background: #f8fafc;
    }}
    .pill.passed, .metric .passed {{ color: var(--passed); }}
    .pill.warning, .metric .warning {{ color: var(--warning); }}
    .pill.failed, .metric .failed {{ color: var(--failed); }}
    .recommendations {{ background: #fff7ed; border: 1px solid #fed7aa; border-radius: 6px; padding: 16px; }}
    @media (max-width: 820px) {{
      .summary, .split {{ grid-template-columns: 1fr; }}
      .dataset header {{ align-items: flex-start; flex-direction: column; }}
      .wrap {{ padding: 18px; }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <h1>Geospatial Data Inspection Report</h1>
      <p>{html.escape(query)}</p>
      <div class="summary">
        <div class="metric"><strong class="{status_class}">{html.escape(overall_status.title())}</strong><span>Overall status</span></div>
        <div class="metric"><strong>{quality_score}/100</strong><span>Quality score</span></div>
        <div class="metric"><strong>{len(inspections)}</strong><span>Datasets inspected</span></div>
      </div>
      <section class="workflow">
        <h3>Workflow-Specific Answer</h3>
        <p><strong>{readiness}</strong>: {workflow_answer}</p>
        <div class="split">
          <div>
            <h3>Key Findings</h3>
            <ul>{finding_items}</ul>
          </div>
          <div>
            <h3>Workflow Actions</h3>
            <ul>{workflow_action_items}</ul>
          </div>
        </div>
      </section>
      <div class="recommendations">
        <h3>Recommended Next Steps</h3>
        <ul>{recommendation_items}</ul>
      </div>
    </section>
    {''.join(cards)}
  </main>
</body>
</html>
"""
        path.write_text(html_text, encoding="utf-8")
        return str(path)

    def run(
        self,
        query: str,
        input_dataset_paths: list[str] | str | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        start_time = time.time()
        dataset_paths = self.normalize_dataset_paths(input_dataset_paths)
        self.ensure_directory(self.output_dir)
        self.reset_metrics()
        self.input_tokens = 0
        self.output_tokens = 0

        self._emit_progress(
            progress_callback,
            stage="start",
            message="I will inspect the supplied datasets for geospatial quality, validation issues, and workflow readiness.",
            data={"dataset_count": len(dataset_paths)},
        )
        self._emit_progress(
            progress_callback,
            stage="input_inspection",
            message="I am classifying each input as vector, raster, table, or unsupported before running type-specific checks.",
            data={"dataset_paths": dataset_paths},
        )

        inspections: list[dict[str, Any]] = []
        for index, dataset_path in enumerate(dataset_paths, start=1):
            self._emit_progress(
                progress_callback,
                stage="input_inspection",
                message=f"Inspecting dataset {index} of {len(dataset_paths)}: {Path(dataset_path).name}.",
                data={"path": dataset_path},
            )
            inspection = self._inspect_dataset(dataset_path)
            inspections.append(inspection)
            failed = sum(1 for check in inspection.get("checks", []) if check.get("status") == "failed")
            warnings = sum(1 for check in inspection.get("checks", []) if check.get("status") == "warning")
            self._emit_progress(
                progress_callback,
                stage="data_validation",
                message=(
                    f"Finished {inspection.get('dataset')}: "
                    f"{failed} failed check(s), {warnings} warning check(s)."
                ),
                data={
                    "dataset": inspection.get("dataset"),
                    "type": inspection.get("type"),
                    "failed_checks": failed,
                    "warning_checks": warnings,
                },
            )

        overall_status = self._overall_status(inspections)
        quality_score = self._quality_score(inspections)
        recommendations = self._recommendations(inspections)
        self._emit_progress(
            progress_callback,
            stage="method_selection",
            message="I summarized dataset-level checks into an overall quality status.",
            data={"overall_status": overall_status, "quality_score": quality_score},
        )

        workflow_assessment = self._synthesize_workflow_assessment(
            query,
            inspections,
            overall_status,
            quality_score,
            recommendations,
            progress_callback=progress_callback,
        )

        text_report_file = self._write_txt_report(
            query,
            inspections,
            overall_status,
            quality_score,
            recommendations,
            workflow_assessment,
        )
        self._emit_progress(
            progress_callback,
            stage="report_generation",
            message="The plain-text inspection report has been written.",
            data={"path": text_report_file},
        )
        html_report_file = self._write_html_report(
            query,
            inspections,
            overall_status,
            quality_score,
            recommendations,
            workflow_assessment,
        )
        self._emit_progress(
            progress_callback,
            stage="artifact_generation",
            message="The formatted HTML inspection report has been written.",
            data={"path": html_report_file},
        )

        summary = (
            f"Inspected {len(inspections)} input dataset(s) for geospatial data readiness and quality. "
            f"Overall status is {overall_status} with a quality score of {quality_score}/100. "
            f"Workflow readiness is {workflow_assessment.get('readiness')}. "
            f"{workflow_assessment.get('workflow_answer')} "
            "The response includes both a TXT report and a formatted HTML report with checks, metadata, "
            "workflow-specific synthesis, and recommended next steps."
        )
        if not inspections:
            summary = (
                "No input datasets were provided, so the geospatial data inspection could not inspect any data. "
                "Provide one or more datasets in input_datasets."
            )
        self._emit_progress(
            progress_callback,
            stage="complete",
            message="Geospatial data inspection is complete and the standard service response can be prepared.",
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
                "parameters": {
                    "checks": [
                        "readability",
                        "crs_presence",
                        "geometry_validity",
                        "attribute_completeness",
                        "duplicate_detection",
                        "workflow_suitability",
                    ]
                },
            },
            "outputs": {
                "text": summary,
                "text_report_file": text_report_file,
                "html_report_file": html_report_file,
                "inspection_assessment": {
                    "overall_status": overall_status,
                    "quality_score": quality_score,
                    "dataset_count": len(inspections),
                    "datasets": inspections,
                    "workflow_assessment": workflow_assessment,
                    "recommendations": recommendations,
                },
            },
            "metrics": self.metrics(
                tool_calls=len(inspections),
                number_of_artifacts=2,
            ),
            "environment": {
                "python_version": platform.python_version(),
                "domain-specific libraries": self.available_libraries,
            },
            "stochasticity": {
                "used": False,
                "controls": [],
            },
            "reproducibility_notes": [
                "The inspection report is generated from deterministic library-based checks of the supplied datasets."
            ],
            "complementary": {
                "Execution": {
                    "Inputs": {"task": query, "dataset_paths": dataset_paths},
                    "Outputs": {
                        "summary": summary,
                        "text_report_file": text_report_file,
                        "html_report_file": html_report_file,
                    },
                },
                "Provenance": {
                    "Lineage": [
                        "Materialized input_datasets into server-accessible files.",
                        "Classified each input dataset by format.",
                        "Ran vector, raster, or tabular inspection checks.",
                        "Synthesized workflow-specific readiness from the inspection results.",
                        "Generated TXT and HTML inspection report artifacts.",
                    ],
                    "Tool Calls": {"count": len(inspections)},
                    "LLM Calls": {"count": self.llm_calls},
                },
                "Validation": {
                    "status": overall_status,
                    "checks": [
                        {
                            "name": "dataset_inspection",
                            "status": overall_status,
                            "message": f"Inspected {len(inspections)} dataset(s) and generated report artifacts.",
                        },
                        {
                            "name": "report_artifacts",
                            "status": "passed",
                            "message": "TXT and HTML inspection reports were created.",
                        },
                    ],
                    "quality_score": quality_score,
                },
                "Assumptions and Limitations": {
                    "assumptions": [
                        "Input datasets are accessible to the GAS server runtime after request materialization.",
                        "CRS and schema metadata reported by GeoPandas, Rasterio, and Pandas are authoritative for this inspection.",
                        *workflow_assessment.get("assumptions", []),
                    ],
                    "limitations": [
                        "Topology checks are intentionally lightweight and do not replace domain-specific QA/QC review.",
                        "Raster checks inspect dataset metadata and readability but do not perform full-cell statistical profiling.",
                        "Workflow suitability is advisory and should be reviewed against the user's actual analysis goal.",
                        *workflow_assessment.get("limitations", []),
                    ],
                },
            },
        }

