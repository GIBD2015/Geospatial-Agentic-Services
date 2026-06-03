import json
from types import SimpleNamespace

import pytest

import gas_server.agents.google_earth_engine_agent as gee_module
from gas_server.agents.google_earth_engine_agent import GoogleEarthEngineAgent
from gas_server.core.service_registry import SERVICE_REGISTRY


class FakeCompletions:
    def __init__(self, payload, usage=None):
        self.payload = payload
        self.usage = usage

    def create(self, **kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(self.payload)),
                )
            ],
            usage=self.usage,
        )


def fake_client(payload, usage=None):
    return SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(payload, usage=usage)))


def test_google_earth_engine_agent_is_registered():
    registration = SERVICE_REGISTRY["google_earth_engine_agent"]
    agent = registration.build_agent()

    assert agent.agent_name == "Google Earth Engine Agent"
    assert agent.agent_version == "1.0.0"
    assert registration.build_spec().requires_model_credentials is True


def test_gee_plan_validation_maps_core_skill_and_named_region():
    agent = GoogleEarthEngineAgent(api_key=None)
    raw_plan = {
        "action": "compute_ndvi_summary",
        "dataset": "Sentinel-2",
        "region": {"type": "named_place", "name": "Centre County, Pennsylvania"},
        "date_range": {"start": "2024-06-01", "end": "2024-06-30"},
        "cloud_filter": {"max_cloud_percent": 15},
        "outputs": ["json", "csv"],
    }

    plan = agent._validate_plan(raw_plan)

    assert plan.action == "ndvi_summary"
    assert plan.dataset == "sentinel2_sr"
    assert plan.region["coordinates"] == [-78.36, 40.69, -77.13, 41.32]
    assert plan.scale == 10
    assert plan.max_cloud_percent == 15


def test_gee_agent_adds_semantic_artifact_roles_and_redacts_parameters(tmp_path):
    agent = GoogleEarthEngineAgent(api_key=None)
    agent.set_request_parameters(
        {
            "OPENAI_API_KEY": "sk-test-secret",
            "max_cloud_percent": 20,
            "nested": {"gibd_api_key": "gibd-secret"},
        }
    )
    summary_path = tmp_path / "gee_ndvi_summary_demo.json"
    plan_path = tmp_path / "gee_validated_plan_demo.json"

    semantic_outputs = agent._semantic_artifact_outputs([str(summary_path), str(plan_path)])

    assert semantic_outputs == {
        "ndvi_summary_json_file": str(summary_path),
        "validated_plan_json_file": str(plan_path),
    }
    safe_parameters = agent._safe_request_parameters()
    assert safe_parameters["OPENAI_API_KEY"] == "[REDACTED]"
    assert safe_parameters["nested"]["gibd_api_key"] == "[REDACTED]"
    assert safe_parameters["max_cloud_percent"] == 20


def test_gee_plan_validation_input_vector_overrides_place_name_by_default(tmp_path):
    gpd = pytest.importorskip("geopandas")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    vector_path = tmp_path / "region.geojson"
    gdf = gpd.GeoDataFrame(
        {"name": ["demo"], "geometry": [shapely_geometry.box(-78.0, 40.0, -77.5, 40.5)]},
        crs="EPSG:4326",
    )
    gdf.to_file(vector_path, driver="GeoJSON")

    agent = GoogleEarthEngineAgent(api_key=None)
    plan = agent._validate_plan(
        {
            "action": "create_ndvi_map",
            "dataset": "sentinel2_sr",
            "region": {"type": "named_place", "name": "Centre County, Pennsylvania"},
            "date_range": {"start": "2024-06-01", "end": "2024-06-30"},
            "outputs": ["html", "geojson"],
        },
        [str(vector_path)],
    )

    assert plan.action == "ndvi_map"
    assert plan.region["type"] == "input_vector"
    assert plan.region["coordinates"] == [-78.0, 40.0, -77.5, 40.5]
    assert plan.region["geojson"]["type"] == "Polygon"


def test_gee_plan_validation_can_opt_out_of_input_vector_region(tmp_path):
    gpd = pytest.importorskip("geopandas")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    vector_path = tmp_path / "region.geojson"
    gdf = gpd.GeoDataFrame(
        {"name": ["demo"], "geometry": [shapely_geometry.box(-78.0, 40.0, -77.5, 40.5)]},
        crs="EPSG:4326",
    )
    gdf.to_file(vector_path, driver="GeoJSON")

    agent = GoogleEarthEngineAgent(api_key=None)
    agent.set_request_parameters({"use_input_region": False})
    plan = agent._validate_plan(
        {
            "action": "summarize_chirps_precipitation",
            "dataset": "chirps_daily",
            "region": {"type": "named_place", "name": "Centre County, Pennsylvania"},
            "date_range": {"start": "2024-07-01", "end": "2024-07-31"},
        },
        [str(vector_path)],
    )

    assert plan.action == "chirps_precipitation_summary"
    assert plan.region["type"] == "bbox"
    assert plan.region["coordinates"] == [-78.36, 40.69, -77.13, 41.32]


def test_gee_plan_validation_bbox_parameter_overrides_place_name_without_vector():
    agent = GoogleEarthEngineAgent(api_key=None)
    agent.set_request_parameters({"bbox": [-80.0, 39.0, -79.0, 40.0]})

    plan = agent._validate_plan(
        {
            "action": "compute_ndvi_summary",
            "dataset": "sentinel2_sr",
            "region": {"type": "named_place", "name": "Centre County, Pennsylvania"},
            "date_range": {"start": "2024-06-01", "end": "2024-06-30"},
        }
    )

    assert plan.region["type"] == "bbox"
    assert plan.region["coordinates"] == [-80.0, 39.0, -79.0, 40.0]


def test_gee_plan_validation_input_vector_overrides_bbox_parameter(tmp_path):
    gpd = pytest.importorskip("geopandas")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    vector_path = tmp_path / "region.geojson"
    gdf = gpd.GeoDataFrame(
        {"name": ["demo"], "geometry": [shapely_geometry.box(-78.0, 40.0, -77.5, 40.5)]},
        crs="EPSG:4326",
    )
    gdf.to_file(vector_path, driver="GeoJSON")

    agent = GoogleEarthEngineAgent(api_key=None)
    agent.set_request_parameters({"bbox": [-80.0, 39.0, -79.0, 40.0]})
    plan = agent._validate_plan(
        {
            "action": "create_ndvi_map",
            "dataset": "sentinel2_sr",
            "region": {"type": "named_place", "name": "Centre County, Pennsylvania"},
            "date_range": {"start": "2024-06-01", "end": "2024-06-30"},
        },
        [str(vector_path)],
    )

    assert plan.region["type"] == "input_vector"
    assert plan.region["coordinates"] == [-78.0, 40.0, -77.5, 40.5]


def test_gee_plan_validation_supports_climate_time_series_variables():
    agent = GoogleEarthEngineAgent(api_key=None)
    plan = agent._validate_plan(
        {
            "action": "weather_time_series",
            "dataset": "gridmet",
            "region": {"type": "named_place", "name": "Centre County, Pennsylvania"},
            "date_range": {"start": "2024-07-01", "end": "2024-07-31"},
            "variables": ["precipitation", "max temperature", "min temperature", "wind"],
        }
    )
    variables = agent._normalize_climate_variables(plan)

    assert plan.action == "climate_time_series"
    assert plan.dataset == "gridmet_daily"
    assert [variable["name"] for variable in variables] == ["precipitation", "tmax", "tmin", "wind_speed"]
    assert variables[1]["band"] == "tmmx"


def test_gee_climate_chart_artifact_and_summary_text(tmp_path):
    agent = GoogleEarthEngineAgent(api_key=None)
    agent.output_dir = str(tmp_path)
    plan = agent._validate_plan(
        {
            "action": "climate_time_series",
            "dataset": "gridmet_daily",
            "region": {"type": "bbox", "coordinates": [-78.0, 40.0, -77.5, 40.5], "name": "demo"},
            "date_range": {"start": "2024-07-01", "end": "2024-07-03"},
            "variables": ["precipitation", "tmax"],
        }
    )
    variables = agent._normalize_climate_variables(plan)
    rows = [
        {"date": "2024-07-01", "precipitation": 1.0, "tmax": 28.2},
        {"date": "2024-07-02", "precipitation": 0.0, "tmax": 30.1},
    ]
    chart_path = agent._save_climate_chart_html("Create climate time series", rows, variables, "Demo Climate")
    summary = {
        "temporal_resolution": "daily",
        "variables": [{"name": "precipitation", "label": "Precipitation"}, {"name": "tmax", "label": "Maximum temperature"}],
        "row_count": 2,
    }

    text = agent._result_summary_text(plan, summary)

    assert chart_path.endswith(".html")
    assert (tmp_path / __import__("pathlib").Path(chart_path).name).is_file()
    assert "Demo Climate" in (tmp_path / __import__("pathlib").Path(chart_path).name).read_text(encoding="utf-8")
    assert "Created a daily climate time series" in text
    assert "Precipitation, Maximum temperature" in text


def test_gee_plan_validation_supports_ndvi_time_series():
    agent = GoogleEarthEngineAgent(api_key=None)
    plan = agent._validate_plan(
        {
            "action": "daily_ndvi",
            "dataset": "Sentinel-2",
            "region": {"type": "named_place", "name": "Centre County, Pennsylvania"},
            "date_range": {"start": "2024-06-01", "end": "2024-06-30"},
            "outputs": ["csv", "chart"],
        }
    )

    assert plan.action == "ndvi_time_series"
    assert plan.dataset == "sentinel2_sr"
    assert plan.region["coordinates"] == [-78.36, 40.69, -77.13, 41.32]
    assert plan.temporal_resolution == "daily"


def test_gee_plan_validation_detects_monthly_ndvi_time_series_request():
    agent = GoogleEarthEngineAgent(api_key=None)
    plan = agent._validate_plan(
        {
            "action": "ndvi_time_series",
            "dataset": "Sentinel-2",
            "region": {"type": "named_place", "name": "Centre County, Pennsylvania"},
            "date_range": {"start": "2021-09-01", "end": "2022-03-31"},
            "outputs": ["csv", "html"],
        },
        query=(
            "Compute ONLY monthly mean NDVI. Return one CSV row per month and an "
            "HTML line chart; do not compute daily values."
        ),
    )

    assert plan.action == "ndvi_time_series"
    assert plan.temporal_resolution == "monthly"
    assert set(plan.outputs) == {"csv", "html"}


def test_gee_ndvi_time_series_routes_monthly_plan_to_monthly_executor(monkeypatch):
    agent = GoogleEarthEngineAgent(api_key=None)
    plan = agent._validate_plan(
        {
            "action": "ndvi_time_series",
            "dataset": "sentinel2_sr",
            "region": {"type": "bbox", "coordinates": [-78.0, 40.0, -77.5, 40.5], "name": "demo"},
            "date_range": {"start": "2021-09-01", "end": "2022-03-31"},
            "temporal_resolution": "monthly",
        }
    )

    called = {}

    def fake_monthly(query, monthly_plan):
        called["temporal_resolution"] = monthly_plan.temporal_resolution
        return {"temporal_resolution": "monthly"}, []

    monkeypatch.setattr(agent, "run_monthly_ndvi_time_series", fake_monthly)

    summary, artifacts = agent.run_ndvi_time_series("monthly NDVI", plan)

    assert called == {"temporal_resolution": "monthly"}
    assert summary == {"temporal_resolution": "monthly"}
    assert artifacts == []


def test_gee_plan_validation_uses_explicit_output_format_from_request():
    agent = GoogleEarthEngineAgent(api_key=None)
    plan = agent._validate_plan(
        {
            "action": "ndvi_time_series",
            "dataset": "sentinel2_sr",
            "region": {"type": "named_place", "name": "Centre County, Pennsylvania"},
            "date_range": {"start": "2024-06-01", "end": "2024-06-30"},
            "outputs": ["csv", "html"],
        },
        query="Return an HTML line chart of daily mean NDVI.",
    )

    assert plan.outputs == ["html"]


def test_gee_plan_validation_respects_csv_only_request():
    agent = GoogleEarthEngineAgent(api_key=None)
    plan = agent._validate_plan(
        {
            "action": "ndvi_summary",
            "dataset": "sentinel2_sr",
            "region": {"type": "named_place", "name": "Centre County, Pennsylvania"},
            "date_range": {"start": "2024-06-01", "end": "2024-06-30"},
            "outputs": ["json", "csv"],
        },
        query="Return CSV summary artifacts with mean, min, max, and standard deviation.",
    )

    assert plan.outputs == ["csv"]
    assert agent._wants_output(plan, "csv") is True
    assert agent._wants_output(plan, "json") is False


def test_gee_plan_validation_maps_static_image_request_to_thumbnail_output():
    agent = GoogleEarthEngineAgent(api_key=None)
    plan = agent._validate_plan(
        {
            "action": "create_cloud_filtered_composite",
            "dataset": "sentinel2_sr",
            "region": {"type": "bbox", "coordinates": [-78.0, 40.0, -77.5, 40.5], "name": "demo"},
            "date_range": {"start": "2025-06-01", "end": "2025-06-30"},
            "outputs": ["html"],
        },
        query="Return an interactive HTML map and a static image.",
    )

    assert "html" in plan.outputs
    assert "thumbnail" in plan.outputs
    assert agent._wants_output(plan, "html") is True
    assert agent._wants_output(plan, "thumbnail") is True


def test_gee_ndvi_time_series_chart_artifact_and_summary_text(tmp_path):
    agent = GoogleEarthEngineAgent(api_key=None)
    agent.output_dir = str(tmp_path)
    plan = agent._validate_plan(
        {
            "action": "ndvi_time_series",
            "dataset": "sentinel2_sr",
            "region": {"type": "bbox", "coordinates": [-78.0, 40.0, -77.5, 40.5], "name": "demo"},
            "date_range": {"start": "2024-06-01", "end": "2024-06-03"},
        }
    )
    rows = [
        {"date": "2024-06-01", "image_count": 2, "ndvi_mean": 0.61},
        {"date": "2024-06-02", "image_count": 1, "ndvi_mean": 0.64},
    ]
    chart_path = agent._save_line_chart_html(
        "Create daily NDVI",
        "gee_ndvi_time_series",
        rows,
        [{"field": "ndvi_mean", "label": "Mean NDVI"}],
        "Demo NDVI",
    )
    summary = {"row_count": 2}

    text = agent._result_summary_text(plan, summary)

    assert agent._artifact_role_for_path(chart_path) == "ndvi_time_series_html_file"
    assert (tmp_path / __import__("pathlib").Path(chart_path).name).is_file()
    assert "Demo NDVI" in (tmp_path / __import__("pathlib").Path(chart_path).name).read_text(encoding="utf-8")
    assert "Created a daily NDVI time series" in text
    assert "Returned 2 observation date(s)" in text


def test_gee_monthly_ndvi_summary_text_and_chart_labels(tmp_path):
    agent = GoogleEarthEngineAgent(api_key=None)
    agent.output_dir = str(tmp_path)
    plan = agent._validate_plan(
        {
            "action": "ndvi_time_series",
            "dataset": "sentinel2_sr",
            "region": {"type": "bbox", "coordinates": [-78.0, 40.0, -77.5, 40.5], "name": "demo"},
            "date_range": {"start": "2021-09-01", "end": "2022-03-31"},
            "temporal_resolution": "monthly",
        }
    )
    chart_path = agent._save_line_chart_html(
        "Create monthly NDVI",
        "gee_ndvi_time_series",
        [{"month": "2021-09", "ndvi_mean": 0.31}, {"month": "2021-10", "ndvi_mean": 0.35}],
        [{"field": "ndvi_mean", "label": "Mean NDVI"}],
        "Monthly Demo NDVI",
    )
    summary = {"row_count": 2, "temporal_resolution": "monthly"}

    text = agent._result_summary_text(plan, summary)
    html = (tmp_path / __import__("pathlib").Path(chart_path).name).read_text(encoding="utf-8")

    assert "Created a monthly NDVI time series" in text
    assert "Returned 2 month(s)" in text
    assert "2021-09" in html


def test_gee_plan_validation_supports_land_cover_map():
    agent = GoogleEarthEngineAgent(api_key=None)
    plan = agent._validate_plan(
        {
            "action": "summarize_land_cover_area",
            "dataset": "esa_worldcover",
            "region": {"type": "named_place", "name": "Centre County, Pennsylvania"},
            "date_range": {"start": "2024-06-01", "end": "2024-06-30"},
            "outputs": ["map", "html"],
        }
    )

    assert plan.action == "land_cover_map"
    assert plan.dataset == "esa_worldcover"


def test_gee_land_cover_map_summary_text_and_thumbnail_artifact_metadata():
    agent = GoogleEarthEngineAgent(api_key=None)
    plan = agent._validate_plan(
        {
            "action": "create_land_cover_map",
            "dataset": "esa_worldcover",
            "region": {"type": "bbox", "coordinates": [-78.0, 40.0, -77.5, 40.5], "name": "demo"},
            "date_range": {"start": "2024-06-01", "end": "2024-06-30"},
        }
    )
    summary = {"earth_engine_visualization": {"thumbnail_url": "https://example.test/thumb", "tile_fetcher_url_format": "https://example.test/{z}/{x}/{y}"}}

    text = agent._result_summary_text(plan, summary)
    artifact = agent._external_url_artifact(
        url="https://example.test/thumb",
        filename="gee_land_cover_thumbnail.png",
        role="land_cover_thumbnail_png_url",
        label="Earth Engine Land-Cover Preview",
        mime_type="image/png",
        format_name="png",
        description="preview",
    )

    assert "Created a land-cover map" in text
    assert "thumbnail artifact and map tile URL" in text
    assert artifact["_artifact_role"] == "land_cover_thumbnail_png_url"
    assert artifact["_artifact_label"] == "Earth Engine Land-Cover Preview"


def test_gee_plan_validation_supports_surface_water_occurrence_map():
    agent = GoogleEarthEngineAgent(api_key=None)
    plan = agent._validate_plan(
        {
            "action": "map_surface_water",
            "dataset": "JRC Global Surface Water",
            "region": {"type": "named_place", "name": "Centre County, Pennsylvania"},
            "outputs": ["html", "geojson", "csv"],
            "scale": 30,
        },
        query="Map surface water occurrence using JRC Global Surface Water. Return an HTML map and GeoJSON/CSV outputs.",
    )

    assert plan.action == "surface_water_map"
    assert plan.dataset == "jrc_global_surface_water"
    assert plan.scale == 30
    assert set(plan.outputs) == {"html", "geojson", "csv"}


def test_gee_plan_validation_supports_sentinel1_flood_water_map():
    agent = GoogleEarthEngineAgent(api_key=None)
    plan = agent._validate_plan(
        {
            "action": "detect_flood_water",
            "dataset": "Sentinel-1",
            "region": {"type": "named_place", "name": "Centre County, Pennsylvania"},
            "date_range": {"start": "2024-05-01", "end": "2024-05-15"},
            "outputs": ["html", "csv"],
        },
        query="Use Sentinel-1 to map recent flood water and return an HTML map plus CSV.",
    )

    assert plan.action == "surface_water_map"
    assert plan.dataset == "sentinel1_grd"
    assert plan.date_range == {"start": "2024-05-01", "end": "2024-05-15"}


def test_gee_surface_water_summary_text_and_artifact_roles(tmp_path):
    agent = GoogleEarthEngineAgent(api_key=None)
    agent.output_dir = str(tmp_path)
    plan = agent._validate_plan(
        {
            "action": "water_occurrence_map",
            "dataset": "jrc_global_surface_water",
            "region": {"type": "bbox", "coordinates": [-78.0, 40.0, -77.5, 40.5], "name": "demo"},
            "outputs": ["html", "geojson", "csv"],
        }
    )
    summary = {
        "earth_engine_visualization": {"thumbnail_url": "https://example.test/thumb", "tile_fetcher_url_format": "https://example.test/{z}/{x}/{y}"},
        "water_area_sq_km": 12.34,
    }
    geojson_path = agent._write_geojson_artifact(
        "surface water demo",
        "gee_surface_water_polygons",
        {"type": "FeatureCollection", "features": []},
    )

    text = agent._result_summary_text(plan, summary)

    assert "Created a surface-water map" in text
    assert "12.34 sq km" in text
    assert agent._artifact_role_for_path(geojson_path) == "surface_water_polygons_geojson_file"


@pytest.mark.parametrize(
    ("action", "dataset"),
    [
        ("compute_ndvi_summary", "sentinel2_sr"),
        ("daily_ndvi", "sentinel2_sr"),
        ("create_cloud_filtered_composite", "sentinel2_sr"),
        ("summarize_chirps_precipitation", "chirps_daily"),
        ("summarize_land_cover_area", "esa_worldcover"),
        ("create_land_cover_map", "esa_worldcover"),
        ("map_surface_water", "jrc_global_surface_water"),
        ("create_earth_engine_export_task", "sentinel2_sr"),
    ],
)
def test_gee_plan_validation_input_vector_region_applies_to_all_skills(action, dataset, tmp_path):
    gpd = pytest.importorskip("geopandas")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    vector_path = tmp_path / "region.geojson"
    gdf = gpd.GeoDataFrame(
        {"name": ["demo"], "geometry": [shapely_geometry.box(-78.0, 40.0, -77.5, 40.5)]},
        crs="EPSG:4326",
    )
    gdf.to_file(vector_path, driver="GeoJSON")

    agent = GoogleEarthEngineAgent(api_key=None)
    plan = agent._validate_plan(
        {
            "action": action,
            "dataset": dataset,
            "region": {"type": "named_place", "name": "Centre County, Pennsylvania"},
            "date_range": {"start": "2024-06-01", "end": "2024-06-30"},
        },
        [str(vector_path)],
    )

    assert plan.region["type"] == "input_vector"
    assert plan.region["coordinates"] == [-78.0, 40.0, -77.5, 40.5]


def test_gee_visualization_urls_capture_thumbnail_and_tile_metadata(monkeypatch):
    agent = GoogleEarthEngineAgent(api_key=None)
    plan = agent._validate_plan(
        {
            "action": "create_ndvi_map",
            "dataset": "sentinel2_sr",
            "region": {"type": "bbox", "coordinates": [-78.0, 40.0, -77.5, 40.5], "name": "demo"},
            "date_range": {"start": "2024-06-01", "end": "2024-06-30"},
        }
    )

    class FakeTileFetcher:
        url_format = "https://tiles.example/{z}/{x}/{y}"

    class FakeImage:
        def getThumbURL(self, params):
            return "https://thumb.example/preview.png"

        def getMapId(self, params):
            return {"mapid": "map-123", "tile_fetcher": FakeTileFetcher()}

    class FakeGeometry:
        @staticmethod
        def Rectangle(*args, **kwargs):
            return "geometry"

    class FakeEe:
        Geometry = FakeGeometry

    monkeypatch.setattr(gee_module, "ee", FakeEe)

    result = agent._earth_engine_visualization_urls(FakeImage(), plan, {"min": -0.2, "max": 0.9})

    assert result["thumbnail_url"] == "https://thumb.example/preview.png"
    assert result["map_id"] == "map-123"
    assert result["tile_fetcher_url_format"] == "https://tiles.example/{z}/{x}/{y}"


def test_gee_composite_preview_html_artifact(tmp_path):
    agent = GoogleEarthEngineAgent(api_key=None)
    agent.output_dir = str(tmp_path)
    plan = agent._validate_plan(
        {
            "action": "create_cloud_filtered_composite",
            "dataset": "sentinel2_sr",
            "region": {"type": "bbox", "coordinates": [-78.0, 40.0, -77.5, 40.5], "name": "demo"},
            "date_range": {"start": "2024-06-01", "end": "2024-06-30"},
            "outputs": ["html"],
        }
    )
    summary = {
        "dataset_id": "COPERNICUS/S2_SR_HARMONIZED",
        "thumbnail_url": "https://thumb.example/composite.png",
        "tile_fetcher_url_format": "https://tiles.example/{z}/{x}/{y}",
    }

    html_path = agent._save_composite_preview_html("Create composite HTML", plan, summary)
    html_text = (tmp_path / __import__("pathlib").Path(html_path).name).read_text(encoding="utf-8")

    assert agent._artifact_role_for_path(html_path) == "cloud_filtered_composite_preview_html_file"
    assert "Earth Engine Composite Preview" in html_text
    assert "https://tiles.example/{z}/{x}/{y}" in html_text
    assert "Earth Engine true-color composite" in html_text
    assert "https://thumb.example/composite.png" not in html_text


def test_gee_ndvi_map_summary_text_and_thumbnail_artifact_metadata():
    agent = GoogleEarthEngineAgent(api_key=None)
    plan = agent._validate_plan(
        {
            "action": "create_ndvi_map",
            "dataset": "sentinel2_sr",
            "region": {"type": "bbox", "coordinates": [-78.0, 40.0, -77.5, 40.5], "name": "demo"},
            "date_range": {"start": "2024-06-01", "end": "2024-06-30"},
        }
    )
    summary = {
        "earth_engine_visualization": {
            "thumbnail_url": "https://thumb.example/preview.png",
            "tile_fetcher_url_format": "https://tiles.example/{z}/{x}/{y}",
        },
    }

    text = agent._result_summary_text(plan, summary)
    artifact = agent._external_url_artifact(
        url=summary["earth_engine_visualization"]["thumbnail_url"],
        filename="gee_ndvi_thumbnail.png",
        role="ndvi_thumbnail_png_url",
        label="Earth Engine NDVI Preview",
        mime_type="image/png",
        format_name="png",
        description="Earth Engine thumbnail URL for visual inspection of the NDVI raster.",
    )

    assert "Created an NDVI map" in text
    assert "actual clipped NDVI raster" in text
    assert "interactive HTML map" in text
    assert artifact["kind"] == "downloadable_file"
    assert artifact["url"] == "https://thumb.example/preview.png"
    assert artifact["_artifact_role"] == "ndvi_thumbnail_png_url"


def test_gee_plan_validation_geocodes_unknown_place(monkeypatch, tmp_path):
    agent = GoogleEarthEngineAgent(api_key=None)
    agent.output_dir = str(tmp_path)

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return [
                {
                    "display_name": "Test County, Example State",
                    "boundingbox": ["40.0", "40.5", "-78.0", "-77.5"],
                    "geojson": {
                        "type": "Polygon",
                        "coordinates": [[[-78.0, 40.0], [-77.5, 40.0], [-77.5, 40.5], [-78.0, 40.5], [-78.0, 40.0]]],
                    },
                }
            ]

    calls = []

    def fake_get(url, params, headers, timeout):
        calls.append((url, params, headers, timeout))
        return FakeResponse()

    monkeypatch.setattr(gee_module.requests, "get", fake_get)

    plan = agent._validate_plan(
        {
            "action": "ndvi_summary",
            "dataset": "sentinel2_sr",
            "region": {"type": "named_place", "name": "Test County, Example State"},
            "date_range": {"start": "2024-06-01", "end": "2024-06-30"},
        }
    )

    assert plan.region["type"] == "geocoded_place"
    assert plan.region["coordinates"] == [-78.0, 40.0, -77.5, 40.5]
    assert plan.region["source"] == "OpenStreetMap Nominatim"
    assert calls[0][1]["q"] == "Test County, Example State"

    cached = agent._validate_plan(
        {
            "action": "ndvi_summary",
            "dataset": "sentinel2_sr",
            "region": {"type": "named_place", "name": "Test County, Example State"},
            "date_range": {"start": "2024-06-01", "end": "2024-06-30"},
        }
    )
    assert cached.region["coordinates"] == [-78.0, 40.0, -77.5, 40.5]
    assert len(calls) == 1


def test_gee_gcs_export_returns_raster_output_metadata(monkeypatch):
    agent = GoogleEarthEngineAgent(api_key=None)
    plan = agent._validate_plan(
        {
            "action": "create_export_task",
            "dataset": "sentinel2_sr",
            "region": {"type": "bbox", "coordinates": [-78.0, 40.0, -77.5, 40.5], "name": "demo"},
            "date_range": {"start": "2024-06-01", "end": "2024-06-30"},
            "export": {"enabled": True, "destination": "gcs"},
        }
    )
    monkeypatch.setenv("GEE_EXPORT_BUCKET", "gas-gee-exports-geospatialagenticservice")
    monkeypatch.setenv("GEE_EXPORT_PREFIX", "gee_exports")
    calls = {}

    class FakeTask:
        def start(self):
            calls["started"] = True

        def status(self):
            return {"id": "task-123", "state": "READY"}

    class FakeImageExport:
        @staticmethod
        def toCloudStorage(**kwargs):
            calls["kwargs"] = kwargs
            return FakeTask()

    class FakeExport:
        image = FakeImageExport

    class FakeBatch:
        Export = FakeExport

    class FakeGeometry:
        @staticmethod
        def Rectangle(*args, **kwargs):
            return "geometry"

    class FakeEe:
        batch = FakeBatch
        Geometry = FakeGeometry

    monkeypatch.setattr(gee_module, "ee", FakeEe)

    result = agent._export_image_to_gcs("image", plan, "gee_export_task")

    assert calls["started"] is True
    assert calls["kwargs"]["bucket"] == "gas-gee-exports-geospatialagenticservice"
    assert result["status"]["id"] == "task-123"
    assert result["raster_output"]["uri"].startswith("gs://gas-gee-exports-geospatialagenticservice/gee_exports/")
    assert result["raster_output"]["format"] == "GeoTIFF"
    assert result["raster_output"]["https_url"].startswith("https://storage.googleapis.com/")


def test_gee_export_defaults_to_gcs_when_bucket_is_configured(monkeypatch):
    agent = GoogleEarthEngineAgent(api_key=None)
    monkeypatch.setenv("GEE_EXPORT_BUCKET", "gas-gee-exports-geospatialagenticservice")
    plan = agent._validate_plan(
        {
            "action": "create_export_task",
            "dataset": "sentinel2_sr",
            "region": {"type": "bbox", "coordinates": [-78.0, 40.0, -77.5, 40.5], "name": "demo"},
            "date_range": {"start": "2024-06-01", "end": "2024-06-30"},
            "export": {"enabled": True},
        }
    )

    assert plan.export["destination"] == "gcs"


def test_gee_export_clips_image_to_resolved_region_before_export(monkeypatch):
    agent = GoogleEarthEngineAgent(api_key=None)
    plan = agent._validate_plan(
        {
            "action": "create_export_task",
            "dataset": "sentinel2_sr",
            "region": {"type": "bbox", "coordinates": [-78.0, 40.0, -77.5, 40.5], "name": "demo"},
            "date_range": {"start": "2024-06-01", "end": "2024-06-30"},
            "export": {"enabled": True, "destination": "gcs"},
        }
    )
    calls = {}

    class FakeImage:
        def clip(self, region):
            calls["clip_region"] = region
            return "clipped-image"

    monkeypatch.setattr(agent, "_region_geometry", lambda active_plan: "resolved-region")

    def fake_export_to_gcs(image, active_plan, description):
        calls["export_image"] = image
        return {"status": {"id": "task-123"}, "raster_output": None}

    monkeypatch.setattr(agent, "_export_image_to_gcs", fake_export_to_gcs)

    result = agent._export_image(FakeImage(), plan, "gee_export_task")

    assert calls["clip_region"] == "resolved-region"
    assert calls["export_image"] == "clipped-image"
    assert result["status"]["id"] == "task-123"


def test_gee_export_waits_for_completion_and_updates_status(monkeypatch):
    agent = GoogleEarthEngineAgent(api_key=None)
    agent.set_request_parameters(
        {
            "wait_timeout_seconds": 30,
            "poll_interval_seconds": 1,
        }
    )
    statuses = [
        {"id": "task-123", "state": "RUNNING"},
        {"id": "task-123", "state": "COMPLETED"},
    ]
    sleeps = []

    class FakeTask:
        def status(self):
            return statuses.pop(0) if statuses else {"id": "task-123", "state": "COMPLETED"}

    monkeypatch.setattr(gee_module.time, "sleep", lambda seconds: sleeps.append(seconds))
    augment_calls = {"count": 0}

    def fake_augment(raster_output):
        augment_calls["count"] += 1
        raster_output["object_exists"] = augment_calls["count"] >= 2

    monkeypatch.setattr(agent, "_augment_gcs_raster_output", fake_augment)

    export_result = {
        "status": {"id": "task-123", "state": "READY"},
        "raster_output": {"destination": "gcs"},
        "_task": FakeTask(),
    }
    wait_result = agent._wait_for_export(export_result)

    assert wait_result["completed"] is True
    assert wait_result["timed_out"] is False
    assert wait_result["final_status"]["state"] == "COMPLETED"
    assert export_result["status"]["state"] == "COMPLETED"
    assert export_result["raster_output"]["object_exists"] is True
    assert sleeps == [1]


def test_gee_export_wait_returns_when_gcs_object_is_available(monkeypatch):
    agent = GoogleEarthEngineAgent(api_key=None)
    agent.set_request_parameters(
        {
            "wait_timeout_seconds": 30,
            "poll_interval_seconds": 1,
        }
    )

    class FakeTask:
        def status(self):
            return {"id": "task-123", "state": "RUNNING"}

    monkeypatch.setattr(gee_module.time, "sleep", lambda seconds: pytest.fail("sleep should not run when GCS object is already visible"))
    monkeypatch.setattr(agent, "_augment_gcs_raster_output", lambda raster_output: raster_output.update({"object_exists": True}))

    export_result = {
        "status": {"id": "task-123", "state": "READY"},
        "raster_output": {"destination": "gcs", "uri": "gs://bucket/object.tif"},
        "_task": FakeTask(),
    }
    wait_result = agent._wait_for_export(export_result)

    assert wait_result["completed"] is True
    assert wait_result["timed_out"] is False
    assert wait_result["completion_source"] == "gcs_object"
    assert wait_result["final_status"]["state"] == "RUNNING"
    assert export_result["status"]["state"] == "RUNNING"
    assert export_result["raster_output"]["object_exists"] is True


def test_gee_gcs_augment_matches_prefixed_tif_and_generates_signed_url(monkeypatch):
    agent = GoogleEarthEngineAgent(api_key=None)
    agent.set_request_parameters({"signed_url_expiration_seconds": 600})
    monkeypatch.setenv("GEE_SERVICE_ACCOUNT_KEY", "")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "")

    class FakeBlob:
        def __init__(self, name, exists=False):
            self.name = name
            self._exists = exists

        def exists(self):
            return self._exists

        def generate_signed_url(self, **kwargs):
            return f"https://signed.example/{self.name}"

    class FakeBucket:
        def blob(self, name):
            return FakeBlob(name, exists=False)

        def list_blobs(self, prefix):
            assert prefix == "gee_exports/demo"
            return [FakeBlob("gee_exports/demo-0000000000-0000000000.tif")]

    class FakeClient:
        def bucket(self, name):
            assert name == "bucket"
            return FakeBucket()

    class FakeStorage:
        class Client:
            @staticmethod
            def from_service_account_json(key_file):
                return FakeClient()

            def __new__(cls):
                return FakeClient()

    monkeypatch.setitem(__import__("sys").modules, "google.cloud.storage", FakeStorage)

    raster_output = {
        "destination": "gcs",
        "bucket": "bucket",
        "file_prefix": "gee_exports/demo",
        "uri": "gs://bucket/gee_exports/demo.tif",
        "https_url": "https://storage.googleapis.com/bucket/gee_exports/demo.tif",
    }

    agent._augment_gcs_raster_output(raster_output)

    assert raster_output["object_exists"] is True
    assert raster_output["file_prefix_matched_object"] == "gee_exports/demo-0000000000-0000000000.tif"
    assert raster_output["uri"] == "gs://bucket/gee_exports/demo-0000000000-0000000000.tif"
    assert raster_output["signed_url"] == "https://signed.example/gee_exports/demo-0000000000-0000000000.tif"


def test_gee_gcs_augment_preserves_object_exists_when_signed_url_fails(monkeypatch):
    agent = GoogleEarthEngineAgent(api_key=None)
    monkeypatch.setenv("GEE_SERVICE_ACCOUNT_KEY", "")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "")

    class FakeBlob:
        name = "gee_exports/demo.tif"

        def exists(self):
            return True

        def generate_signed_url(self, **kwargs):
            raise RuntimeError("signing unavailable")

    class FakeBucket:
        def blob(self, name):
            return FakeBlob()

    class FakeClient:
        def bucket(self, name):
            return FakeBucket()

    class FakeStorage:
        class Client:
            @staticmethod
            def from_service_account_json(key_file):
                return FakeClient()

            def __new__(cls):
                return FakeClient()

    monkeypatch.setitem(__import__("sys").modules, "google.cloud.storage", FakeStorage)

    raster_output = {
        "destination": "gcs",
        "bucket": "bucket",
        "file_prefix": "gee_exports/demo",
    }

    agent._augment_gcs_raster_output(raster_output)

    assert raster_output["object_exists"] is True
    assert raster_output["signed_url"] is None
    assert "signed URL generation failed" in raster_output["signed_url_note"]


def test_gee_export_wait_timeout(monkeypatch):
    agent = GoogleEarthEngineAgent(api_key=None)
    agent.set_request_parameters(
        {
            "wait_timeout_seconds": 1,
            "poll_interval_seconds": 1,
        }
    )
    clock = {"value": 0}

    class FakeTask:
        def status(self):
            return {"id": "task-123", "state": "RUNNING"}

    def fake_time():
        clock["value"] += 1
        return clock["value"]

    monkeypatch.setattr(gee_module.time, "time", fake_time)
    monkeypatch.setattr(gee_module.time, "sleep", lambda seconds: None)

    export_result = {
        "status": {"id": "task-123", "state": "READY"},
        "raster_output": {"destination": "gcs"},
        "_task": FakeTask(),
    }
    wait_result = agent._wait_for_export(export_result)

    assert wait_result["completed"] is False
    assert wait_result["timed_out"] is True
    assert wait_result["final_status"]["state"] == "RUNNING"


def test_gee_agent_run_uses_llm_plan_and_trusted_tool_without_live_gee(monkeypatch, tmp_path):
    plan_payload = {
        "action": "ndvi_summary",
        "dataset": "sentinel2_sr",
        "region": {
            "type": "bbox",
            "coordinates": [-78.36, 40.69, -77.13, 41.32],
            "name": "Centre County, Pennsylvania",
        },
        "date_range": {"start": "2024-06-01", "end": "2024-06-30"},
        "cloud_filter": {"max_cloud_percent": 20},
        "outputs": ["json", "csv"],
    }
    agent = GoogleEarthEngineAgent(api_key=None)
    agent.client = fake_client(plan_payload, usage=SimpleNamespace(prompt_tokens=123, completion_tokens=45))
    agent.output_dir = str(tmp_path)

    monkeypatch.setattr(agent, "_initialize_ee", lambda: None)

    def fake_ndvi(query, plan):
        summary = {"action": plan.action, "dataset": plan.dataset, "ndvi": {"NDVI_mean": 0.42}}
        return summary, [
            agent._write_json_artifact(query, "gee_ndvi_summary", summary),
            agent._write_csv_artifact(query, "gee_ndvi_summary", [{"metric": "NDVI_mean", "value": 0.42}]),
        ]

    monkeypatch.setattr(agent, "run_ndvi_summary", fake_ndvi)

    result = agent.run("Compute average NDVI for Centre County, PA from Sentinel-2 during June 2024.")

    provenance = result["complementary"]["Provenance"]
    assert provenance["Validated Plan"]["action"] == "ndvi_summary"
    assert provenance["GEE Summary"]["ndvi"]["NDVI_mean"] == 0.42
    assert result["metrics"]["llm_calls"] == 1
    assert result["total_input_tokens"] == 123
    assert result["total_output_tokens"] == 45
    assert result["total_tokens"] == 168
    assert len(result["outputs"]["dataset_paths"]) == 2
    assert "validated_plan_json_file" not in result["outputs"]
    assert "gee_plan" not in result["outputs"]
    assert "gee_summary" not in result["outputs"]
    assert provenance["Raw LLM Plan"] == plan_payload
    assert result["stochasticity"] == {
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
    }
    assert result["reproducibility_notes"] == [
        "The GEE agent does not execute LLM-generated Python code; execution.code.available is false by design.",
        "The reproducible workflow specification is the validated plan in provenance.details.validated_plan plus execution inputs, parameters, and artifact references.",
    ]
    assert all(tmp_path in path.parents for path in map(lambda value: __import__("pathlib").Path(value), result["outputs"]["dataset_paths"]))


def test_gee_initialize_uses_explicit_service_account_credentials(monkeypatch, tmp_path):
    key_path = tmp_path / "service-account.json"
    key_path.write_text(
        json.dumps({"client_email": "gas-service@example.iam.gserviceaccount.com"}),
        encoding="utf-8",
    )
    calls = {}

    class FakeEe:
        @staticmethod
        def ServiceAccountCredentials(email, key_file):
            calls["credentials"] = (email, key_file)
            return "credentials-object"

        @staticmethod
        def Initialize(credentials, project):
            calls["initialize"] = (credentials, project)

    monkeypatch.setattr(gee_module, "ee", FakeEe)
    monkeypatch.setenv("GEE_SERVICE_ACCOUNT_KEY", str(key_path))
    monkeypatch.setenv("GEE_PROJECT", "geospatialagenticservice")

    agent = GoogleEarthEngineAgent(api_key=None)
    agent._initialize_ee()

    assert calls["credentials"] == ("gas-service@example.iam.gserviceaccount.com", str(key_path))
    assert calls["initialize"] == ("credentials-object", "geospatialagenticservice")
    assert agent.tool_trace[0]["tool"] == "initialize_earth_engine"


def test_gee_plan_requires_bbox_when_named_region_is_unknown():
    agent = GoogleEarthEngineAgent(api_key=None)
    agent._geocode_place = lambda name: None

    with pytest.raises(ValueError, match="bounding box region"):
        agent._validate_plan(
            {
                "action": "ndvi_summary",
                "dataset": "sentinel2_sr",
                "region": {"type": "named_place", "name": "Unknown Place"},
                "date_range": {"start": "2024-06-01", "end": "2024-06-30"},
            }
        )
