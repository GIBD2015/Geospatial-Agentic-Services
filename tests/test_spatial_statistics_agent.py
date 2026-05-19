import json

from gas_server.agents import spatial_statistics_agent
from gas_server.core.service_core import _call_agent_with_optional_progress
from gas_server.core.service_registry import get_service_registration


def test_spatial_statistics_agent_creates_fallback_report(tmp_path, monkeypatch):
    monkeypatch.setattr(spatial_statistics_agent, "DATA_DIR", tmp_path / "Data")
    dataset_path = tmp_path / "sample.geojson"
    dataset_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"value": 10},
                        "geometry": {"type": "Point", "coordinates": [-77.0, 40.0]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    events = []
    agent = spatial_statistics_agent.SpatialStatisticsAgent(api_key=None)

    result = agent.run(
        "Run spatial autocorrelation using PySAL",
        [str(dataset_path)],
        progress_callback=events.append,
    )

    stages = {event["stage"] for event in events}
    assert "input_inspection" in stages
    assert "data_validation" in stages
    assert "model_selection" in stages
    assert "planning" in stages
    assert "llm_generation" in stages
    assert "warning" in stages
    assert "fallback_start" in stages
    assert "fallback_complete" in stages
    assert "artifact_generation" in stages
    assert "complete" in stages
    assert result["outputs"]["text_report_file"].endswith(".txt")
    assert result["outputs"]["html_report_file"].endswith(".html")
    assert result["outputs"]["report_file"].endswith(".html")
    assert "results_file" not in result["outputs"]
    assert len(result["outputs"]["dataset_paths"]) == 2
    assert all(not path.endswith(".json") for path in result["outputs"]["dataset_paths"])
    html = open(result["outputs"]["html_report_file"], encoding="utf-8").read().lower()
    assert "<html" in html
    assert "spatial statistics modeling report" in html


def test_spatial_statistics_service_dispatch_forwards_progress_callback(tmp_path, monkeypatch):
    monkeypatch.setattr(spatial_statistics_agent, "DATA_DIR", tmp_path / "Data")
    dataset_path = tmp_path / "sample.geojson"
    dataset_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"value": 10},
                        "geometry": {"type": "Point", "coordinates": [-77.0, 40.0]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    registration = get_service_registration("spatial_statistics_agent")
    spec = registration.build_spec()
    agent = spatial_statistics_agent.SpatialStatisticsAgent(api_key=None)
    events = []

    _call_agent_with_optional_progress(
        spec,
        agent,
        "Run spatial autocorrelation using PySAL",
        [str(dataset_path)],
        progress_callback=events.append,
    )

    stages = {event["stage"] for event in events}
    assert "input_inspection" in stages
    assert "model_selection" in stages
    assert "fallback_complete" in stages
    assert "data_validation" in stages


