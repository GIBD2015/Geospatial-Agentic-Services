import json
from types import SimpleNamespace

from gas_server.agents import geospatial_data_inspection_agent
from gas_server.services.geospatial_data_inspection_agent_service import get_service_app
from gas_server.core.service_core import _build_task_payload


def test_geospatial_data_inspection_agent_reports_progress_and_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(geospatial_data_inspection_agent, "DATA_DIR", tmp_path / "Data")
    dataset_path = tmp_path / "sample.geojson"
    dataset_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"GEOID": "42001", "name": "A"},
                        "geometry": {"type": "Point", "coordinates": [-77.0, 40.0]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    events = []
    agent = geospatial_data_inspection_agent.GeospatialDataInspectionAgent(api_key=None)

    result = agent.run(
        "Check whether this dataset is ready for mapping and spatial joins.",
        [str(dataset_path)],
        progress_callback=events.append,
    )

    stages = {event["stage"] for event in events}
    assert "input_inspection" in stages
    assert "data_validation" in stages
    assert "method_selection" in stages
    assert "report_generation" in stages
    assert "artifact_generation" in stages
    assert "complete" in stages
    assert result["outputs"]["text_report_file"].endswith(".txt")
    assert result["outputs"]["html_report_file"].endswith(".html")
    assert result["outputs"]["inspection_assessment"]["dataset_count"] == 1
    inspected = result["outputs"]["inspection_assessment"]["datasets"][0]
    assert inspected["type"] == "vector"
    assert inspected["suitability"]["mapping_ready"] is True


def test_geospatial_data_inspection_agent_normalizes_to_standard_response(tmp_path, monkeypatch):
    monkeypatch.setattr(geospatial_data_inspection_agent, "DATA_DIR", tmp_path / "Data")
    dataset_path = tmp_path / "points.csv"
    dataset_path.write_text(
        "id,longitude,latitude,value\n1,-77.0,40.0,5\n2,-78.0,41.0,6\n",
        encoding="utf-8",
    )
    agent = geospatial_data_inspection_agent.GeospatialDataInspectionAgent(api_key=None)
    raw_result = agent.run("Review this CSV for mapping and joining.", [str(dataset_path)])

    payload = _build_task_payload(
        task_id="data-quality-test-task",
        agent_id="geospatial_data_inspection_agent",
        agent_name=raw_result["agent_name"],
        agent_version=raw_result["agent_version"],
        state="TASK_STATE_COMPLETED",
        query="Review this CSV for mapping and joining.",
        requested_skill=None,
        result=raw_result,
        error_message=None,
        agent_id_for_artifacts="geospatial_data_inspection_agent",
        output_delivery="url",
        public_base_url="http://testserver",
    )

    assert payload["agent"]["id"] == "geospatial_data_inspection_agent"
    assert payload["outputs"]["summary"]
    artifact_formats = {artifact.get("format") for artifact in payload["outputs"]["artifacts"]}
    assert {"txt", "html"} <= artifact_formats
    assert payload["execution"]["inputs"]["dataset_paths"] == [str(dataset_path)]
    assert payload["provenance"]["llm_calls"] == 0
    assert payload["reproducibility"]["stochasticity"] == {"used": False, "controls": []}
    assert payload["diagnostics"]["validation"]["quality_score"] >= 0


def test_geospatial_data_inspection_agent_rejects_service_requests_without_input_datasets():
    app = get_service_app()
    response = app.test_client().post(
        "/tasks",
        json={
            "task": {"instructions": "Run a data quality check.", "mode": "sync"},
            "credentials": {"OPENAI_API_KEY": "test-key"},
        },
    )

    assert response.status_code == 400
    assert "requires at least one dataset" in response.get_json()["error"]["message"]


def test_geospatial_data_inspection_agent_direct_run_marks_empty_inputs_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(geospatial_data_inspection_agent, "DATA_DIR", tmp_path / "Data")
    agent = geospatial_data_inspection_agent.GeospatialDataInspectionAgent(api_key=None)

    result = agent.run("Run a geospatial data quality check.")

    assessment = result["outputs"]["inspection_assessment"]
    assert assessment["dataset_count"] == 0
    assert assessment["overall_status"] == "failed"
    assert "No input datasets were provided" in result["outputs"]["text"]


def test_geospatial_data_inspection_agent_uses_llm_for_workflow_synthesis(tmp_path, monkeypatch):
    monkeypatch.setattr(geospatial_data_inspection_agent, "DATA_DIR", tmp_path / "Data")
    dataset_path = tmp_path / "sample.geojson"
    dataset_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"GEOID": "42001", "name": "A"},
                        "geometry": {"type": "Point", "coordinates": [-77.0, 40.0]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    class FakeCompletions:
        def create(self, **kwargs):
            content = json.dumps(
                {
                    "workflow_answer": "The dataset is ready for interactive mapping and needs review for spatial joining with another layer.",
                    "readiness": "needs_review",
                    "key_findings": ["The vector layer has CRS and valid point geometry."],
                    "recommended_actions": ["Confirm the join target CRS before running the spatial join."],
                    "assumptions": ["The requested workflow is a point-in-polygon or attribute-supported join."],
                    "limitations": ["Only one dataset was provided, so pairwise compatibility could not be checked."],
                }
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20),
            )

    agent = geospatial_data_inspection_agent.GeospatialDataInspectionAgent(api_key=None)
    agent.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    events = []

    result = agent.run(
        "Check whether these county boundaries and hospital points are ready for a spatial join and interactive mapping.",
        [str(dataset_path)],
        progress_callback=events.append,
    )

    workflow = result["outputs"]["inspection_assessment"]["workflow_assessment"]
    stages = {event["stage"] for event in events}
    assert workflow["readiness"] == "needs_review"
    assert "interactive mapping" in workflow["workflow_answer"]
    assert result["metrics"]["llm_calls"] == 1
    assert result["total_tokens"] == 30
    assert "planning" in stages
    assert "method_selection" in stages

