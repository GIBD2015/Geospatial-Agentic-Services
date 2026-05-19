from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict


SPATIAL_FILE_EXTENSIONS = {
    ".geojson",
    ".json",
    ".gpkg",
    ".shp",
    ".tif",
    ".tiff",
}


def _validation(status: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": status,
        "checks": checks,
    }


def _passed(name: str, message: str, **data: Any) -> dict[str, Any]:
    return {"name": name, "status": "passed", "message": message, **data}


def _warning(name: str, message: str, **data: Any) -> dict[str, Any]:
    return {"name": name, "status": "warning", "message": message, **data}


def _failed(name: str, message: str, **data: Any) -> dict[str, Any]:
    return {"name": name, "status": "failed", "message": message, **data}


def _empty_result(path: Path, message: str) -> dict[str, Any]:
    return {
        "spatial_metadata": {
            "type": _artifact_type_from_extension(path.suffix.lower()),
            "crs": None,
            "bbox": None,
            "geometry_type": None,
            "feature_count": None,
            "dimensions": None,
            "schema": None,
            "raster": None,
        },
        "validation": _validation("warning", [_warning("metadata_extraction", message)]),
    }


def _artifact_type_from_extension(extension: str) -> str | None:
    if extension in {".geojson", ".json", ".gpkg", ".shp"}:
        return "vector"
    if extension in {".tif", ".tiff"}:
        return "raster"
    if extension == ".csv":
        return "table"
    return None


def inspect_artifact(path_value: str | Path | None) -> dict[str, Any]:
    """Return best-effort spatial metadata and validation for a local artifact."""
    if not path_value:
        return {}

    path = Path(path_value)
    if not path.is_file():
        return {}

    extension = path.suffix.lower()
    if extension in {".geojson", ".json"}:
        return _inspect_geojson(path)
    if extension in {".gpkg", ".shp"}:
        return _inspect_vector_with_geopandas(path)
    if extension in {".tif", ".tiff"}:
        return _inspect_raster(path)
    if extension == ".csv":
        return _inspect_csv(path)
    return {
        "validation": _validation(
            "not_applicable",
            [_passed("file_exists", "Artifact file exists but is not a spatial data format.")],
        )
    }


def _inspect_geojson(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return _empty_result(path, f"Could not parse GeoJSON metadata: {exc}")

    features = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(features, list):
        return _empty_result(path, "GeoJSON does not contain a FeatureCollection features array.")

    geometry_types: set[str] = set()
    bbox_values: list[float] = []
    schema: dict[str, str] = {}
    null_geometry_count = 0

    for feature in features:
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            null_geometry_count += 1
        else:
            geometry_type = geometry.get("type")
            if geometry_type:
                geometry_types.add(str(geometry_type))
            bbox_values.extend(_flatten_coordinates(geometry.get("coordinates")))

        properties = feature.get("properties")
        if isinstance(properties, dict):
            for key, value in properties.items():
                schema.setdefault(str(key), type(value).__name__)

    bbox = _bbox_from_flat_coordinates(bbox_values)
    crs = _geojson_crs(payload)
    checks = [
        _passed("file_readable", "GeoJSON artifact is readable."),
        _passed("feature_count", f"GeoJSON contains {len(features)} feature(s).", feature_count=len(features)),
    ]
    if _is_valid_bbox(bbox):
        checks.append(_passed("bbox_validity", "Bounding box is numeric and ordered as [minx, miny, maxx, maxy]."))
    else:
        checks.append(_warning("bbox_validity", "Bounding box is missing or not ordered as [minx, miny, maxx, maxy]."))
    if null_geometry_count:
        checks.append(_warning("geometry_presence", f"{null_geometry_count} feature(s) have null or missing geometry."))
    else:
        checks.append(_passed("geometry_presence", "All features include geometry objects."))

    return {
        "spatial_metadata": {
            "type": "vector",
            "crs": crs,
            "bbox": bbox,
            "geometry_type": _single_or_sorted(geometry_types),
            "feature_count": len(features),
            "dimensions": None,
            "schema": schema,
            "raster": None,
        },
        "validation": _validation(_status_from_checks(checks), checks),
    }


def _inspect_vector_with_geopandas(path: Path) -> dict[str, Any]:
    try:
        import geopandas as gpd

        gdf = gpd.read_file(path)
    except Exception as exc:
        return _empty_result(path, f"Could not inspect vector artifact with GeoPandas: {exc}")

    checks = [
        _passed("file_readable", "Vector artifact is readable with GeoPandas."),
        _passed("feature_count", f"Vector artifact contains {len(gdf)} feature(s).", feature_count=len(gdf)),
    ]
    bbox = None
    if not gdf.empty:
        try:
            bbox = [float(value) for value in gdf.total_bounds]
            if _is_valid_bbox(bbox):
                checks.append(_passed("bbox_validity", "Bounding box is numeric and ordered as [minx, miny, maxx, maxy]."))
            else:
                checks.append(_warning("bbox_validity", "Bounding box is missing or not ordered as [minx, miny, maxx, maxy]."))
        except Exception as exc:
            checks.append(_warning("bbox_validity", f"Could not evaluate bounding box validity: {exc}"))
    else:
        checks.append(_warning("feature_count", "Vector artifact contains no features."))

    try:
        invalid_count = int((~gdf.geometry.is_valid).sum()) if "geometry" in gdf else 0
        if invalid_count:
            checks.append(_warning("geometry_validity", f"{invalid_count} invalid geometries detected."))
        else:
            checks.append(_passed("geometry_validity", "All geometries are valid."))
    except Exception as exc:
        checks.append(_warning("geometry_validity", f"Could not evaluate geometry validity: {exc}"))

    schema = {
        str(column): str(dtype)
        for column, dtype in gdf.drop(columns="geometry", errors="ignore").dtypes.items()
    }
    geometry_types = set(str(value) for value in gdf.geometry.geom_type.dropna().unique()) if "geometry" in gdf else set()
    return {
        "spatial_metadata": {
            "type": "vector",
            "crs": str(gdf.crs) if gdf.crs else None,
            "bbox": bbox,
            "geometry_type": _single_or_sorted(geometry_types),
            "feature_count": int(len(gdf)),
            "dimensions": None,
            "schema": schema,
            "raster": None,
        },
        "validation": _validation(_status_from_checks(checks), checks),
    }


def _inspect_raster(path: Path) -> dict[str, Any]:
    try:
        import rasterio

        with rasterio.open(path) as src:
            bbox = [float(src.bounds.left), float(src.bounds.bottom), float(src.bounds.right), float(src.bounds.top)]
            crs = str(src.crs) if src.crs else None
            raster = {
                "width": src.width,
                "height": src.height,
                "band_count": src.count,
                "resolution": [float(src.res[0]), float(src.res[1])],
                "dtype": list(src.dtypes),
                "nodata": src.nodata,
            }
    except Exception as exc:
        return _empty_result(path, f"Could not inspect raster artifact with Rasterio: {exc}")

    checks = [
        _passed("file_readable", "Raster artifact is readable with Rasterio."),
        _passed("bbox_validity", "Bounding box is numeric and ordered as [minx, miny, maxx, maxy]."),
        _passed("raster_dimensions", "Raster dimensions were extracted.", width=raster["width"], height=raster["height"]),
    ]
    if crs:
        checks.append(_passed("crs_present", f"Raster CRS is {crs}."))
    else:
        checks.append(_warning("crs_present", "Raster CRS is missing."))

    return {
        "spatial_metadata": {
            "type": "raster",
            "crs": crs,
            "bbox": bbox,
            "geometry_type": None,
            "feature_count": None,
            "dimensions": [raster["height"], raster["width"], raster["band_count"]],
            "schema": None,
            "raster": raster,
        },
        "validation": _validation(_status_from_checks(checks), checks),
    }


def _inspect_csv(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            fieldnames = reader.fieldnames or []
    except Exception as exc:
        return _empty_result(path, f"Could not inspect CSV artifact: {exc}")

    schema = {field: "unknown" for field in fieldnames}
    lon_field = _first_matching_field(fieldnames, {"lon", "lng", "longitude", "x"})
    lat_field = _first_matching_field(fieldnames, {"lat", "latitude", "y"})
    bbox = None
    checks = [_passed("file_readable", "CSV artifact is readable.")]
    if fieldnames and rows:
        checks.append(_passed("tabular_structure", "CSV has a header row and at least one data row."))
    elif fieldnames:
        checks.append(_warning("tabular_structure", "CSV has a header row but no data rows."))
    else:
        checks.append(_warning("tabular_structure", "CSV has no header row."))
    if lon_field and lat_field:
        bbox = _bbox_from_rows(rows, lon_field, lat_field)
        if bbox:
            checks.append(_passed("coordinate_columns", "Latitude/longitude columns were detected.", bbox=bbox))
        else:
            checks.append(_warning("coordinate_columns", "Coordinate columns were detected but numeric extent could not be computed."))
    else:
        checks.append(_warning("coordinate_columns", "No obvious latitude/longitude columns were detected."))

    return {
        "spatial_metadata": {
            "type": "table",
            "crs": "EPSG:4326" if bbox else None,
            "bbox": bbox,
            "geometry_type": "Point" if bbox else None,
            "feature_count": len(rows),
            "dimensions": [len(rows), len(fieldnames)],
            "schema": schema,
            "raster": None,
        },
        "validation": _validation(_status_from_checks(checks), checks),
    }


def _flatten_coordinates(value: Any) -> list[float]:
    if not isinstance(value, list):
        return []
    if len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
        return [float(value[0]), float(value[1])]
    flattened: list[float] = []
    for item in value:
        flattened.extend(_flatten_coordinates(item))
    return flattened


def _bbox_from_flat_coordinates(values: list[float]) -> list[float] | None:
    if len(values) < 2:
        return None
    xs = values[0::2]
    ys = values[1::2]
    if not xs or not ys:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def _bbox_from_rows(rows: list[dict[str, Any]], lon_field: str, lat_field: str) -> list[float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for row in rows:
        try:
            xs.append(float(row[lon_field]))
            ys.append(float(row[lat_field]))
        except (TypeError, ValueError, KeyError):
            continue
    if not xs or not ys:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def _geojson_crs(payload: dict[str, Any]) -> str | None:
    crs = payload.get("crs")
    if isinstance(crs, dict):
        properties = crs.get("properties")
        if isinstance(properties, dict) and properties.get("name"):
            return str(properties["name"])
    return "EPSG:4326"


def _first_matching_field(fields: list[str], candidates: set[str]) -> str | None:
    for field in fields:
        if field.lower() in candidates:
            return field
    return None


def _single_or_sorted(values: set[str]) -> str | list[str] | None:
    if not values:
        return None
    if len(values) == 1:
        return next(iter(values))
    return sorted(values)


def _status_from_checks(checks: list[dict[str, Any]]) -> str:
    if any(check.get("status") == "failed" for check in checks):
        return "failed"
    if any(check.get("status") == "warning" for check in checks):
        return "warning"
    return "passed"


def _is_valid_bbox(bbox: Any) -> bool:
    if not isinstance(bbox, list) or len(bbox) != 4:
        return False
    if not all(isinstance(value, (int, float)) for value in bbox):
        return False
    minx, miny, maxx, maxy = bbox
    return minx <= maxx and miny <= maxy
