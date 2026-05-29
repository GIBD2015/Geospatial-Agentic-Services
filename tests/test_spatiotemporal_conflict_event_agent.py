import csv
import json
from pathlib import Path

from gas_server.agents.spatiotemporal_conflict_event_agent import (
    SpatiotemporalConflictEventAgent,
    _parse_llm_json,
)
from gas_server.core.service_core import _gas_execute_task_to_run_payload


def test_structured_csv_with_coordinates_creates_csv_geojson_and_report(tmp_path):
    dataset_path = tmp_path / "events.csv"
    dataset_path.write_text(
        "\n".join(
            [
                "location,date,category,description,latitude,longitude",
                "Kyiv, Kyiv Oblast, Ukraine,2026-05-01,Military operations (battle, shelling),Shelling reported,50.4501,30.5234",
            ]
        ),
        encoding="utf-8",
    )
    agent = SpatiotemporalConflictEventAgent(api_key=None)
    agent.output_dir = str(tmp_path / "outputs")

    result = agent.run(
        "Clean this event table and create a GIS-ready GeoJSON event layer.",
        [str(dataset_path)],
    )

    csv_path = Path(result["outputs"]["csv_output_file"])
    geojson_path = Path(result["outputs"]["geojson_output_file"])
    report_path = Path(result["outputs"]["text_report_file"])
    assert csv_path.is_file()
    assert geojson_path.is_file()
    assert report_path.is_file()

    payload = json.loads(geojson_path.read_text(encoding="utf-8"))
    assert len(payload["features"]) == 1
    assert payload["features"][0]["geometry"]["coordinates"] == [30.5234, 50.4501]
    assert result["metrics"]["llm_calls"] == 0


def test_deduplicates_structured_events_and_reports_count(tmp_path):
    dataset_path = tmp_path / "events.csv"
    dataset_path.write_text(
        "\n".join(
            [
                "location,date,category,description,latitude,longitude",
                '"Kyiv, Kyiv Oblast, Ukraine",2026-05-01,"Military operations (battle, shelling)",Shelling reported,50.4501,30.5234',
                '"Kyiv, Kyiv Oblast, Ukraine",2026-05-01,"Military operations (battle, shelling)",Shelling reported again,50.4501,30.5234',
            ]
        ),
        encoding="utf-8",
    )
    agent = SpatiotemporalConflictEventAgent(api_key=None)
    agent.output_dir = str(tmp_path / "outputs")

    result = agent.run("Clean and deduplicate this event table.", [str(dataset_path)])

    geojson = json.loads(Path(result["outputs"]["geojson_output_file"]).read_text(encoding="utf-8"))
    assert len(geojson["features"]) == 1
    assert result["outputs"]["event_summary"]["duplicate_records_removed"] == 1
    report = Path(result["outputs"]["text_report_file"]).read_text(encoding="utf-8")
    assert "Number of duplicate records removed: 1" in report


def test_missing_coordinates_without_geocoding_key_still_returns_csv_and_empty_geojson(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENCAGE_API_KEY", raising=False)
    dataset_path = tmp_path / "events.csv"
    dataset_path.write_text(
        "\n".join(
            [
                "location,date,category,description",
                '"Kyiv, Kyiv Oblast, Ukraine",2026-05-01,"Military operations (battle, shelling)",Shelling reported',
            ]
        ),
        encoding="utf-8",
    )
    agent = SpatiotemporalConflictEventAgent(api_key=None)
    agent.output_dir = str(tmp_path / "outputs")

    result = agent.run("Clean this event table without geocoding.", [str(dataset_path)])

    with Path(result["outputs"]["csv_output_file"]).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1

    geojson = json.loads(Path(result["outputs"]["geojson_output_file"]).read_text(encoding="utf-8"))
    assert geojson["features"] == []
    report = Path(result["outputs"]["text_report_file"]).read_text(encoding="utf-8")
    assert "OpenCage credentials were not supplied" in report
    assert result["outputs"]["event_summary"]["records_missing_coordinates"] == 1


def test_llm_json_parser_repairs_markdown_code_fence_and_trailing_comma():
    raw = """
```json
[
  {
    "location": "Kyiv, Kyiv Oblast, Ukraine",
    "date": "2026-05-01",
    "category": "Military operations (battle, shelling)",
  }
]
```
"""

    parsed = _parse_llm_json(raw)

    assert len(parsed) == 1
    assert parsed[0]["location"] == "Kyiv, Kyiv Oblast, Ukraine"


def test_capability_file_loads_with_gmu_stc_provider():
    path = Path("gas_server/capabilities/spatiotemporal_conflict_event_agent.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["profile"]["agent_id"] == "spatiotemporal_conflict_event_agent"
    assert payload["profile"]["provider"]["name"] == "Spatiotemporal Innovation Center at George Mason University"


def test_gas_native_payload_preserves_top_level_source_credentials():
    payload = _gas_execute_task_to_run_payload(
        {
            "task": {"instructions": "Clean conflict events."},
            "source_credentials": {"OPENCAGE": {"key": "test-opencage-key"}},
        }
    )

    assert payload["source_credentials"]["OPENCAGE"]["key"] == "test-opencage-key"
