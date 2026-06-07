import json
import re
from pathlib import Path
from urllib.parse import urlparse

from gas_server.agents.web_mapping_app_agent import WebMappingAppAgent
from gas_server.agents.geospatial_data_inspection_agent import GeospatialDataInspectionAgent
from gas_server.agents.spatial_statistics_agent import SpatialStatisticsAgent
from gas_server.core.service_registry import SERVICE_REGISTRY
from gas_server.core.service_core import _build_task_payload
import gas_server.core.service_core as service_core


CAPABILITY_DIR = Path("gas_server") / "capabilities"
SCHEMA_DIR = Path("gas_server") / "schemas"
AGENT_CAPABILITY_FILES = sorted(CAPABILITY_DIR.glob("*_agent.json"))
CAPABILITIES_FILE = CAPABILITY_DIR / "capabilities.json"
REQUIRED_RESPONSE_SECTIONS = {
    "response",
    "task",
    "agent",
    "outputs",
    "execution",
    "provenance",
    "reproducibility",
    "diagnostics",
}
REQUIRED_OPERATION_FIELDS = {
    "operation_id",
    "name",
    "description",
    "request_schema",
    "response_schema",
    "modes",
    "task",
    "inputs",
    "outputs",
    "parameters",
    "credentials",
}
ALLOWED_DEPLOYMENT_HOSTS = {
    "www.geospatial-agentic-services.online",
}
DISALLOWED_MACHINE_PATTERNS = (
    re.compile(r"[A-Za-z]:\\"),
    re.compile(r"(^|[\"'\s])/(home|Users|var|tmp)/"),
    re.compile(r"\b(localhost|127\.0\.0\.1|0\.0\.0\.0|128\.118\.54\.16)\b"),
)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema_type_matches(value, expected_type) -> bool:
    if isinstance(expected_type, list):
        return any(_schema_type_matches(value, item) for item in expected_type)
    if expected_type == "null":
        return value is None
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    return True


def _resolve_schema_ref(schema, ref):
    assert ref.startswith("#/$defs/"), ref
    current = schema
    for part in ref.removeprefix("#/").split("/"):
        current = current[part]
    return current


def _validate_schema(instance, schema, path="$", root_schema=None):
    root_schema = root_schema or schema
    if "$ref" in schema:
        return _validate_schema(
            instance,
            _resolve_schema_ref(root_schema, schema["$ref"]),
            path,
            root_schema,
        )

    if "oneOf" in schema:
        matches = 0
        errors = []
        for option in schema["oneOf"]:
            try:
                _validate_schema(instance, option, path, root_schema)
                matches += 1
            except AssertionError as exc:
                errors.append(exc)
        assert matches == 1, (path, f"expected exactly one oneOf match, got {matches}", errors)
        return

    expected_type = schema.get("type")
    if expected_type is not None:
        assert _schema_type_matches(instance, expected_type), (
            path,
            f"expected {expected_type}, got {type(instance).__name__}",
        )

    if "enum" in schema and instance is not None:
        assert instance in schema["enum"], (path, f"{instance!r} not in {schema['enum']}")

    if isinstance(instance, str) and "minLength" in schema:
        assert len(instance) >= schema["minLength"], (
            path,
            f"expected string length >= {schema['minLength']}",
        )

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            assert key in instance, (path, f"missing required property {key!r}")

        properties = schema.get("properties", {})
        for key, item_schema in properties.items():
            if key in instance:
                _validate_schema(instance[key], item_schema, f"{path}.{key}", root_schema)

    if isinstance(instance, list):
        min_items = schema.get("minItems")
        if min_items is not None:
            assert len(instance) >= min_items, (path, f"expected at least {min_items} item(s)")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(instance):
                _validate_schema(item, item_schema, f"{path}[{index}]", root_schema)


def _walk_strings(value, ancestry=()):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_strings(item, ancestry + (str(key),))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_strings(item, ancestry + (str(index),))
    elif isinstance(value, str):
        yield ancestry, value


def test_capability_documents_validate_against_json_schema():
    schema = _load_json(SCHEMA_DIR / "describe_agent.schema.json")

    for path in AGENT_CAPABILITY_FILES:
        _validate_schema(_load_json(path), schema, path.name)


def test_get_capabilities_document_validates_against_json_schema():
    payload = _load_json(CAPABILITIES_FILE)
    schema = _load_json(SCHEMA_DIR / "capabilities.schema.json")

    _validate_schema(payload, schema, CAPABILITIES_FILE.name)


def test_execute_task_request_examples_validate_against_json_schema():
    schema = _load_json(SCHEMA_DIR / "execute_task_request.schema.json")
    url_request = {
        "task": {
            "instructions": "Create a web mapping app from these datasets.",
            "mode": "stream",
        },
        "inputs": {
            "input_datasets": [
                "https://example.com/pa_counties.geojson",
                {
                    "filename": "pa_hospitals.geojson",
                    "encoding": "base64",
                    "mime_type": "application/geo+json",
                    "data": "BASE64_ENCODED_FILE_CONTENT",
                },
            ]
        },
        "outputs": {
            "artifact_delivery": "URL",
        },
        "parameters": {
            "model": "gpt-5.2",
        },
        "credentials": {
            "OPENAI_API_KEY": "test-openai-key",
            "agent_specific_credentials": {
                "EPA_AQS": {
                    "email": "user@example.com",
                    "key": "aqs-test-key",
                }
            },
        },
        "metadata": {
            "client_id": "pytest",
            "request_id": "request-001",
        },
    }
    simple_request = {
        "task": {
            "instructions": "Download Pennsylvania county boundaries from the Census Bureau."
        },
        "outputs": {
            "artifact_delivery": "Encoded",
        },
        "credentials": {
            "GIBD_API_KEY": "test-gibd-key",
        },
    }

    _validate_schema(url_request, schema, "execute_task_request.url")
    _validate_schema(simple_request, schema, "execute_task_request.simple")


def test_execute_task_request_schema_requires_task_instructions():
    schema = _load_json(SCHEMA_DIR / "execute_task_request.schema.json")

    try:
        _validate_schema({"task": {"mode": "sync"}}, schema, "execute_task_request.invalid")
    except AssertionError as exc:
        assert "instructions" in str(exc)
    else:
        raise AssertionError("ExecuteTask request without task.instructions should fail schema validation.")


def test_get_capabilities_document_uses_agent_ids_and_describe_agent_links():
    payload = _load_json(CAPABILITIES_FILE)

    assert payload["base_url"].startswith("https://")
    assert "endpoints" not in payload
    operations = {operation["operation_id"]: operation for operation in payload["operations"]}
    assert set(operations) >= {
        "get_capabilities",
        "describe_agent",
        "execute_task",
        "get_task_status",
        "get_task_result",
        "cancel_task",
        "get_agent_status",
    }
    assert operations["get_capabilities"]["url"].startswith(payload["base_url"])
    assert operations["describe_agent"]["url"].startswith(payload["base_url"])
    assert "agent_id={agent_id}" in operations["describe_agent"]["url"]
    assert operations["execute_task"]["path"] == "/agents/{agent_id}/tasks"
    assert operations["execute_task"]["url"].startswith(payload["base_url"])
    assert "{agent_id}" in operations["execute_task"]["url"]
    assert "{task_id}" in operations["get_task_result"]["url"]
    for agent in payload["agents"]:
        assert agent["agent_id"]
        assert agent["name"]
        assert agent["name"] != agent["agent_id"]
        assert "describeUrl" not in agent
        assert agent["DescribeAgent"].startswith(payload["base_url"])
        assert agent["DescribeAgent"].endswith(f"REQUEST=DescribeAgent&agent_id={agent['agent_id']}")


def test_agent_capability_documents_have_required_top_level_sections():
    assert AGENT_CAPABILITY_FILES

    for path in AGENT_CAPABILITY_FILES:
        payload = _load_json(path)

        assert isinstance(payload.get("profile"), dict), path
        assert payload["profile"].get("agent_id") == path.stem, path
        assert isinstance(payload["profile"].get("name"), str) and payload["profile"]["name"], path
        assert payload["profile"]["name"] != payload["profile"]["agent_id"], path
        assert isinstance(payload["profile"]["provider"].get("contacts"), list), path
        assert isinstance(payload.get("keywords"), list) and payload["keywords"], path
        assert isinstance(payload.get("skills"), list) and payload["skills"], path
        assert "outputs" not in payload, path
        assert isinstance(payload.get("execute_task"), dict), path
        assert isinstance(payload.get("conformance"), dict), path
        assert isinstance(payload.get("provenance_and_reproducibility"), dict), path
        assert isinstance(payload.get("governance"), dict), path
        assert isinstance(payload.get("extensions"), dict), path
        for skill in payload["skills"]:
            assert isinstance(skill.get("skill_id"), str) and skill["skill_id"], (path, skill)


def test_geospatial_data_retrieval_describes_source_credentials_in_extensions():
    payload = _load_json(CAPABILITY_DIR / "geospatial_data_retrieval_agent.json")
    extensions = payload["extensions"]
    credential_info = extensions["data_source_credentials"]
    sources = {
        source["source_id"]: source
        for source in extensions["data_sources"]
    }

    assert credential_info["parameter"] == "credentials.source_credentials"
    assert sources["EPA_AQS"]["credential_required"] is True
    assert sources["EPA_AQS"]["required_credential_fields"] == ["email", "key"]
    assert sources["OpenWeather"]["credential_required"] is True
    assert sources["US_Census_boundary"]["credential_required"] is False


def test_agent_capability_support_flags_are_booleans():
    for path in AGENT_CAPABILITY_FILES:
        payload = _load_json(path)
        support_section = payload["provenance_and_reproducibility"]

        for key in ("provenance", "reproducibility", "validation"):
            statement = support_section.get(key)
            assert isinstance(statement, dict), (path, key)
            assert isinstance(statement.get("supported"), bool), (path, key, statement)


def test_agent_capability_profile_versions_match_registered_agents():
    for path in AGENT_CAPABILITY_FILES:
        payload = _load_json(path)
        agent_id = payload["profile"]["agent_id"]
        registration = SERVICE_REGISTRY[agent_id]
        agent = registration.build_agent()

        assert payload["profile"]["version"] == registration.get_version(agent), path


def test_agent_capability_documents_advertise_default_and_request_model():
    for path in AGENT_CAPABILITY_FILES:
        payload = _load_json(path)
        agent_id = payload["profile"]["agent_id"]
        registration = SERVICE_REGISTRY[agent_id]
        default_agent = registration.build_agent()
        default_model = getattr(default_agent, "model", None)

        assert payload["profile"]["default_model"] == default_model, path

        model_doc = payload["execute_task"]["parameters"]["model"]
        assert model_doc["default"] == default_model, path
        assert "Optional model override" in model_doc["description"], path
        assert payload["profile"]["default_model"] == default_model, path


def test_agent_capability_execute_task_has_required_contract_fields():
    for path in AGENT_CAPABILITY_FILES:
        payload = _load_json(path)
        operation = payload["execute_task"]
        agent_id = payload["profile"]["agent_id"]
        registration = SERVICE_REGISTRY[agent_id]
        requires_input = getattr(registration.load_agent_class(), "requires_input_datasets", False)

        assert REQUIRED_OPERATION_FIELDS <= set(operation), (path, operation)
        assert operation["operation_id"] == "execute_task", path
        assert {"sync", "async", "stream"} <= set(operation["modes"]), path
        assert operation["request_schema"].endswith("/execute_task_request.schema.json"), path
        assert operation["response_schema"].endswith("/task_response.schema.json"), path
        assert operation["inputs"]["input_datasets"]["required"] is bool(requires_input), path
        assert "artifact_delivery" in operation["outputs"], path
        assert operation["outputs"]["primary_artifacts"], path


def test_agent_capability_authentication_is_documented_once():
    for path in AGENT_CAPABILITY_FILES:
        payload = _load_json(path)

        credentials = payload["execute_task"]["credentials"]
        if credentials["required"]:
            assert "OPENAI_API_KEY" in credentials["one_of"], path
            assert set(credentials["one_of"]) <= {"OPENAI_API_KEY", "GIBD_API_KEY"}, path
        else:
            assert "none" in credentials["one_of"], path
            assert "No API key is required" in credentials["description"], path


def test_agent_capability_documents_advertise_async_task_operations():
    for path in AGENT_CAPABILITY_FILES:
        payload = _load_json(path)

        assert payload["execute_task"]["operation_id"] == "execute_task", path
        assert {"sync", "async", "stream"} <= set(payload["execute_task"]["modes"]), path
        assert "data_download" != payload["execute_task"]["operation_id"], path


def test_agent_capability_documents_advertise_outputs_and_conformance():
    for path in AGENT_CAPABILITY_FILES:
        payload = _load_json(path)

        outputs = payload["execute_task"]["outputs"]
        assert {"URL", "Encoded"} <= set(outputs["artifact_delivery"]["allowed_values"]), path
        assert outputs["artifact_delivery"]["default"] == "URL", path
        assert outputs["primary_artifacts"], path

        conformance = payload["conformance"]
        assert conformance["gas_version"] == "1.0.0", path
        assert conformance["schemas"]["describe_agent"].endswith("/describe_agent.schema.json"), path
        assert conformance["schemas"]["execute_task_request"].endswith("/execute_task_request.schema.json"), path
        assert conformance["schemas"]["task_response"].endswith("/task_response.schema.json"), path
        assert "supports_async_tasks" not in conformance, path
        assert "supports_streaming" not in conformance, path
        assert "supports_push_notifications" not in conformance, path


def test_normalized_service_response_has_required_sections():
    payload = _build_task_payload(
        task_id="test-task-id",
        agent_id="test_agent",
        agent_name="Test Agent",
        agent_version="1.0.0",
        state="TASK_STATE_COMPLETED",
        query="Run a test GIS task",
        requested_skill=None,
        result={
            "agent_name": "Test Agent",
            "agent_version": "1.0.0",
            "outputs": {"text": "Task completed."},
        },
        error_message=None,
        agent_id_for_artifacts="mapping_agent",
        output_delivery="url",
    )

    assert REQUIRED_RESPONSE_SECTIONS <= set(payload)


def test_normalized_service_response_validates_against_json_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(service_core, "DATA_DIR", tmp_path / "Data")
    output_dir = tmp_path / "generated"
    output_dir.mkdir()
    geojson_path = output_dir / "points.geojson"
    geojson_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"name": "A"},
                        "geometry": {"type": "Point", "coordinates": [-77.0, 40.0]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    payload = _build_task_payload(
        task_id="schema-test-task",
        agent_id="schema_agent",
        agent_name="Schema Agent",
        agent_version="1.0.0",
        state="TASK_STATE_COMPLETED",
        query="Return schema-valid output",
        requested_skill=None,
        result={
            "agent_name": "Schema Agent",
            "agent_version": "1.0.0",
            "outputs": {
                "text": "Created points.",
                "output_file": str(geojson_path),
            },
        },
        error_message=None,
        agent_id_for_artifacts="schema_agent",
        output_delivery="url",
        public_base_url="http://testserver",
    )

    _validate_schema(payload, _load_json(SCHEMA_DIR / "task_response.schema.json"))


def test_mapping_agent_multiple_output_paths_are_returned_as_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(service_core, "DATA_DIR", tmp_path / "Data")
    output_dir = tmp_path / "generated"
    output_dir.mkdir()
    output_paths = []
    for name in ("gwr_std_residual.png", "local_r2.png", "coef_income.png"):
        path = output_dir / name
        path.write_bytes(b"png placeholder")
        output_paths.append(str(path))

    payload = _build_task_payload(
        task_id="mapping-multi-output-task",
        agent_id="mapping_agent",
        agent_name="Mapping Agent",
        agent_version="2.0.1",
        state="TASK_STATE_COMPLETED",
        query="Create multiple GWR maps.",
        requested_skill=None,
        result={
            "agent_name": "Mapping Agent",
            "agent_version": "2.0.1",
            "outputs": {
                "text": "Successfully generated visualization.",
                "dataset_path": output_paths[0],
                "dataset_paths": output_paths[1:],
            },
            "metrics": {"number_of_artifacts": 3},
        },
        error_message=None,
        agent_id_for_artifacts="mapping_agent",
        output_delivery="url",
        public_base_url="http://testserver",
    )

    artifacts = payload["outputs"]["artifacts"]
    assert len(artifacts) == 3
    assert {artifact["original_filename"] for artifact in artifacts} == {
        "gwr_std_residual.png",
        "local_r2.png",
        "coef_income.png",
    }
    assert payload["provenance"]["artifacts_created"] == 3


def test_new_agent_results_normalize_to_standard_service_response(tmp_path, monkeypatch):
    import gas_server.agents.web_mapping_app_agent as web_mapping_module
    import gas_server.agents.spatial_statistics_agent as spatial_module

    monkeypatch.setattr(web_mapping_module, "DATA_DIR", tmp_path / "Data")
    monkeypatch.setattr(spatial_module, "DATA_DIR", tmp_path / "Data")
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

    cases = [
        (
            "web_mapping_app_agent",
            "web_mapping_app_agent",
            WebMappingAppAgent(api_key=None).run("Create a web mapping app", [str(dataset_path)]),
        ),
        (
            "geospatial_data_inspection_agent",
            "geospatial_data_inspection_agent",
            GeospatialDataInspectionAgent(api_key=None).run("Check this dataset for quality issues", [str(dataset_path)]),
        ),
        (
            "spatial_statistics_agent",
            "spatial_statistics_agent",
            SpatialStatisticsAgent(api_key=None).run("Run spatial autocorrelation using PySAL", [str(dataset_path)]),
        ),
    ]

    for agent_id, artifact_agent_id, raw_result in cases:
        payload = _build_task_payload(
            task_id=f"{artifact_agent_id}-test-task",
            agent_id=agent_id,
            agent_name=raw_result["agent_name"],
            agent_version=raw_result["agent_version"],
            state="TASK_STATE_COMPLETED",
            query="Run a test task",
            requested_skill=None,
            result=raw_result,
            error_message=None,
            agent_id_for_artifacts=artifact_agent_id,
            output_delivery="url",
            public_base_url="http://testserver",
        )

        assert REQUIRED_RESPONSE_SECTIONS <= set(payload), artifact_agent_id
        assert payload["response"]["type"] == "task_result"
        assert payload["task"]["status"] == "successful"
        assert payload["agent"]["id"] == agent_id
        assert payload["outputs"]["summary"]
        assert payload["outputs"]["artifacts"], artifact_agent_id
        for artifact in payload["outputs"]["artifacts"]:
            assert artifact.get("name"), artifact
            assert artifact.get("role"), artifact
            assert artifact.get("format"), artifact
            assert artifact.get("description"), artifact
            assert artifact.get("filename"), artifact
        assert "data_summary" in payload["outputs"]
        assert "inputs" in payload["execution"]
        assert "code" in payload["execution"]
        assert "runtime" in payload["execution"]
        assert "code_available" in payload["reproducibility"]
        assert "stochasticity" in payload["reproducibility"]
        assert "llm_calls" in payload["provenance"]
        assert "tool_calls" in payload["provenance"]
        assert "validation" in payload["diagnostics"]
        assert "assumptions" in payload["diagnostics"]
        assert "limitations" in payload["diagnostics"]
        if artifact_agent_id == "spatial_statistics_agent":
            artifact_formats = {artifact.get("format") for artifact in payload["outputs"]["artifacts"]}
            assert {"txt", "html"} <= artifact_formats
            assert "json" not in artifact_formats


def test_html_artifact_references_are_rewritten_to_public_artifact_urls(tmp_path, monkeypatch):
    monkeypatch.setattr(service_core, "DATA_DIR", tmp_path / "Data")
    output_dir = tmp_path / "generated"
    output_dir.mkdir()
    html_path = output_dir / "report.html"
    txt_path = output_dir / "report.txt"
    chart_path = output_dir / "chart.png"
    txt_path.write_text("Report", encoding="utf-8")
    chart_path.write_bytes(b"fake-png")
    html_path.write_text(
        '<html><body><img src="chart.png"><a href="chart.png">chart</a></body></html>',
        encoding="utf-8",
    )

    payload = _build_task_payload(
        task_id="spatial-statistics-test-task",
        agent_id="spatial_statistics_agent",
        agent_name="Spatial Statistics Agent",
        agent_version="1.0.0",
        state="TASK_STATE_COMPLETED",
        query="Create a report with a chart",
        requested_skill=None,
        result={
            "agent_name": "Spatial Statistics Agent",
            "agent_version": "1.0.0",
            "outputs": {
                "text": "Created report.",
                "text_report_file": str(txt_path),
                "html_report_file": str(html_path),
                "media_artifact_files": [str(chart_path)],
            },
        },
        error_message=None,
        agent_id_for_artifacts="spatial_statistics_agent",
        output_delivery="url",
        public_base_url="http://testserver",
    )

    artifact_formats = {artifact.get("format") for artifact in payload["outputs"]["artifacts"]}
    assert {"txt", "html", "png"} <= artifact_formats
    html_artifact = next(artifact for artifact in payload["outputs"]["artifacts"] if artifact.get("format") == "html")
    assert html_artifact["name"] == "HTML Report"
    assert html_artifact["role"] == "html_report_file"
    rewritten_html = (tmp_path / "Data" / "spatial_statistics_agent" / html_artifact["filename"]).read_text(encoding="utf-8")
    assert 'src="http://testserver/agents/spatial_statistics_agent/data/' in rewritten_html
    assert 'href="http://testserver/agents/spatial_statistics_agent/data/' in rewritten_html


def test_vector_artifacts_include_spatial_metadata_and_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(service_core, "DATA_DIR", tmp_path / "Data")
    output_dir = tmp_path / "generated"
    output_dir.mkdir()
    geojson_path = output_dir / "points.geojson"
    geojson_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"name": "A", "value": 1},
                        "geometry": {"type": "Point", "coordinates": [-77.0, 40.0]},
                    },
                    {
                        "type": "Feature",
                        "properties": {"name": "B", "value": 2},
                        "geometry": {"type": "Point", "coordinates": [-78.0, 41.0]},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = _build_task_payload(
        task_id="metadata-test-task",
        agent_id="metadata_agent",
        agent_name="Metadata Agent",
        agent_version="1.0.0",
        state="TASK_STATE_COMPLETED",
        query="Return points",
        requested_skill=None,
        result={
            "agent_name": "Metadata Agent",
            "agent_version": "1.0.0",
            "outputs": {
                "text": "Created points.",
                "output_file": str(geojson_path),
            },
        },
        error_message=None,
        agent_id_for_artifacts="metadata_agent",
        output_delivery="url",
        public_base_url="http://testserver",
    )

    artifact = payload["outputs"]["artifacts"][0]
    assert artifact["spatial_metadata"]["type"] == "vector"
    assert artifact["spatial_metadata"]["crs"] == "EPSG:4326"
    assert artifact["spatial_metadata"]["bbox"] == [-78.0, 40.0, -77.0, 41.0]
    assert artifact["spatial_metadata"]["geometry_type"] == "Point"
    assert artifact["spatial_metadata"]["feature_count"] == 2
    assert artifact["spatial_metadata"]["schema"] == {"name": "str", "value": "int"}
    assert artifact["validation"]["status"] == "passed"
    checks = {check["name"]: check for check in artifact["validation"]["checks"]}
    assert checks["bbox_validity"]["status"] == "passed"
    assert "bbox" not in checks["bbox_validity"]


def test_csv_artifact_validation_uses_structure_not_row_count(tmp_path, monkeypatch):
    monkeypatch.setattr(service_core, "DATA_DIR", tmp_path / "Data")
    output_dir = tmp_path / "generated"
    output_dir.mkdir()
    csv_path = output_dir / "points.csv"
    csv_path.write_text(
        "id,longitude,latitude\n1,-77.0,40.0\n2,-78.0,41.0\n",
        encoding="utf-8",
    )

    payload = _build_task_payload(
        task_id="csv-metadata-test-task",
        agent_id="metadata_agent",
        agent_name="Metadata Agent",
        agent_version="1.0.0",
        state="TASK_STATE_COMPLETED",
        query="Return CSV points",
        requested_skill=None,
        result={
            "agent_name": "Metadata Agent",
            "agent_version": "1.0.0",
            "outputs": {
                "text": "Created CSV.",
                "output_file": str(csv_path),
            },
        },
        error_message=None,
        agent_id_for_artifacts="metadata_agent",
        output_delivery="url",
        public_base_url="http://testserver",
    )

    artifact = payload["outputs"]["artifacts"][0]
    assert artifact["spatial_metadata"]["type"] == "table"
    assert artifact["spatial_metadata"]["feature_count"] == 2
    assert artifact["spatial_metadata"]["dimensions"] == [2, 3]
    assert artifact["spatial_metadata"]["bbox"] == [-78.0, 40.0, -77.0, 41.0]
    checks = {check["name"]: check for check in artifact["validation"]["checks"]}
    assert "row_count" not in checks
    assert checks["tabular_structure"]["status"] == "passed"


def test_data_summary_aggregates_all_artifact_spatial_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(service_core, "DATA_DIR", tmp_path / "Data")
    output_dir = tmp_path / "generated"
    output_dir.mkdir()
    geojson_path = output_dir / "points.geojson"
    csv_path = output_dir / "points.csv"
    geojson_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"name": "A"},
                        "geometry": {"type": "Point", "coordinates": [-77.0, 40.0]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    csv_path.write_text(
        "id,longitude,latitude\n1,-78.0,41.0\n2,-79.0,42.0\n",
        encoding="utf-8",
    )

    payload = _build_task_payload(
        task_id="summary-metadata-test-task",
        agent_id="metadata_agent",
        agent_name="Metadata Agent",
        agent_version="1.0.0",
        state="TASK_STATE_COMPLETED",
        query="Return mixed artifacts",
        requested_skill=None,
        result={
            "agent_name": "Metadata Agent",
            "agent_version": "1.0.0",
            "outputs": {
                "text": "Created mixed artifacts.",
                "vector_output_file": str(geojson_path),
                "table_output_file": str(csv_path),
            },
        },
        error_message=None,
        agent_id_for_artifacts="metadata_agent",
        output_delivery="url",
        public_base_url="http://testserver",
    )

    summary = payload["outputs"]["data_summary"]
    assert summary["artifact_count"] == 2
    assert summary["artifact_types"] == ["table", "vector"]
    assert summary["formats"] == ["csv", "geojson"]
    assert summary["crs"] == ["EPSG:4326"]
    assert summary["combined_bbox"] == [-79.0, 40.0, -77.0, 42.0]
    assert summary["has_vector"] is True
    assert summary["has_raster"] is False
    assert summary["has_table"] is True
    assert summary["feature_count_total"] == 3


def test_standard_response_preserves_provenance_details_without_agent_specific_core(tmp_path, monkeypatch):
    monkeypatch.setattr(service_core, "DATA_DIR", tmp_path / "Data")

    gee_summary = {"action": "ndvi_time_series", "row_count": 8}
    gee_plan = {
        "action": "ndvi_time_series",
        "dataset": "sentinel2_sr",
        "region": {"type": "bbox", "coordinates": [-78.0, 40.0, -77.0, 41.0]},
    }
    raw_llm_plan = {"action": "daily_ndvi"}

    payload = _build_task_payload(
        task_id="gee-structured-output-test",
        agent_id="google_earth_engine_agent",
        agent_name="Google Earth Engine Agent",
        agent_version="1.0.0",
        state="TASK_STATE_COMPLETED",
        query="Return NDVI time series",
        requested_skill=None,
        result={
            "agent_name": "Google Earth Engine Agent",
            "agent_version": "1.0.0",
            "outputs": {
                "text": "Created NDVI time series.",
                "custom_output_file": str(tmp_path / "should_be_an_artifact.txt"),
            },
            "complementary": {
                "Provenance": {
                    "Lineage": ["Validated and executed a trusted workflow."],
                    "GEE Summary": gee_summary,
                    "Validated Plan": gee_plan,
                    "Raw LLM Plan": raw_llm_plan,
                }
            },
            "stochasticity": {
                "used": True,
                "controls": ["temperature=0.1", {"name": "validated_plan"}],
            },
            "reproducibility_notes": [
                "The workflow is reproduced from the validated plan rather than generated code."
            ],
        },
        error_message=None,
        agent_id_for_artifacts="google_earth_engine_agent",
        output_delivery="url",
        public_base_url="http://testserver",
    )

    assert "gee_summary" not in payload["outputs"]
    assert "gee_plan" not in payload["outputs"]
    assert payload["provenance"]["details"]["gee_summary"] == gee_summary
    assert payload["provenance"]["details"]["validated_plan"] == gee_plan
    assert payload["provenance"]["details"]["raw_llm_plan"] == raw_llm_plan
    assert "custom_output_file" not in payload["outputs"]
    assert payload["execution"]["code"] == {"available": False, "language": None, "script": None}
    assert payload["reproducibility"]["code_available"] is False
    assert payload["reproducibility"]["environment_available"] is True
    assert payload["reproducibility"]["parameters_available"] is False
    assert payload["reproducibility"]["stochasticity"] == {
        "used": True,
        "controls": [{"description": "temperature=0.1"}, {"name": "validated_plan"}],
    }
    assert payload["reproducibility"]["notes"] == [
        "The workflow is reproduced from the validated plan rather than generated code."
    ]


def test_token_usage_normalizes_string_none_to_json_null(tmp_path, monkeypatch):
    monkeypatch.setattr(service_core, "DATA_DIR", tmp_path / "Data")

    payload = _build_task_payload(
        task_id="token-null-test-task",
        agent_id="metadata_agent",
        agent_name="Metadata Agent",
        agent_version="1.0.0",
        state="TASK_STATE_COMPLETED",
        query="Return token metadata",
        requested_skill=None,
        result={
            "agent_name": "Metadata Agent",
            "agent_version": "1.0.0",
            "outputs": {"text": "Done."},
            "total_input_tokens": "None",
            "total_output_tokens": "null",
            "total_tokens": "-",
        },
        error_message=None,
        agent_id_for_artifacts="metadata_agent",
        output_delivery="url",
        public_base_url="http://testserver",
    )

    assert payload["provenance"]["token_usage"] == {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }


def test_token_usage_normalizes_numeric_strings_and_sums_total(tmp_path, monkeypatch):
    monkeypatch.setattr(service_core, "DATA_DIR", tmp_path / "Data")

    payload = _build_task_payload(
        task_id="token-number-test-task",
        agent_id="metadata_agent",
        agent_name="Metadata Agent",
        agent_version="1.0.0",
        state="TASK_STATE_COMPLETED",
        query="Return token metadata",
        requested_skill=None,
        result={
            "agent_name": "Metadata Agent",
            "agent_version": "1.0.0",
            "outputs": {"text": "Done."},
            "total_input_tokens": "12",
            "total_output_tokens": "8",
        },
        error_message=None,
        agent_id_for_artifacts="metadata_agent",
        output_delivery="url",
        public_base_url="http://testserver",
    )

    assert payload["provenance"]["token_usage"] == {
        "input_tokens": 12,
        "output_tokens": 8,
        "total_tokens": 20,
    }


def test_reproducibility_section_indexes_inputs_outputs_and_stochasticity(tmp_path, monkeypatch):
    monkeypatch.setattr(service_core, "DATA_DIR", tmp_path / "Data")
    output_dir = tmp_path / "generated"
    output_dir.mkdir()
    geojson_path = output_dir / "points.geojson"
    geojson_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"name": "A"},
                        "geometry": {"type": "Point", "coordinates": [-77.0, 40.0]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = _build_task_payload(
        task_id="reproducibility-test-task",
        agent_id="metadata_agent",
        agent_name="Metadata Agent",
        agent_version="1.0.0",
        state="TASK_STATE_COMPLETED",
        query="Return reproducible output",
        requested_skill=None,
        result={
            "agent_name": "Metadata Agent",
            "agent_version": "1.0.0",
            "script": "print('hello')",
            "environment": {"python_version": "3.12", "domain-specific_libraries": ["geopandas"]},
            "inputs": {
                "dataset_paths": ["input.geojson"],
                "parameters": {"buffer_distance": 1000},
            },
            "outputs": {
                "text": "Created points.",
                "output_file": str(geojson_path),
            },
            "stochasticity": {
                "used": False,
                "controls": [],
            },
        },
        error_message=None,
        agent_id_for_artifacts="metadata_agent",
        output_delivery="url",
        public_base_url="http://testserver",
    )

    reproducibility = payload["reproducibility"]
    assert reproducibility["code_available"] is True
    assert reproducibility["environment_available"] is True
    assert reproducibility["parameters_available"] is True
    assert reproducibility["input_artifacts"] == [{"role": "input", "path": "input.geojson"}]
    assert reproducibility["output_artifacts"][0]["role"] == "output"
    assert reproducibility["parameters"] == {"buffer_distance": 1000}
    assert reproducibility["stochasticity"] == {"used": False, "controls": []}


def test_normalized_service_response_redacts_sensitive_request_parameters(tmp_path, monkeypatch):
    monkeypatch.setattr(service_core, "DATA_DIR", tmp_path / "Data")

    payload = _build_task_payload(
        task_id="redaction-test-task",
        agent_id="metadata_agent",
        agent_name="Metadata Agent",
        agent_version="1.0.0",
        state="TASK_STATE_COMPLETED",
        query="Return redacted parameters",
        requested_skill=None,
        result={
            "agent_name": "Metadata Agent",
            "agent_version": "1.0.0",
            "inputs": {
                "dataset_paths": [],
                "parameters": {
                    "OPENAI_API_KEY": "sk-test-secret",
                    "parameters": {"GIBD_API_KEY": "gibd-secret"},
                    "source_credentials": {"EPA_AQS": {"email": "user@example.com", "key": "aqs-secret"}},
                    "max_cloud_percent": 20,
                },
            },
            "complementary": {
                "Execution": {
                    "Inputs": {
                        "dataset_paths": [],
                        "parameters": {"openai_api_key": "sk-complementary-secret"},
                    }
                }
            },
            "outputs": {"text": "Done."},
        },
        error_message=None,
        agent_id_for_artifacts="metadata_agent",
        output_delivery="url",
        public_base_url="http://testserver",
    )

    serialized = json.dumps(payload)
    assert "sk-test-secret" not in serialized
    assert "sk-complementary-secret" not in serialized
    assert "gibd-secret" not in serialized
    assert "aqs-secret" not in serialized
    parameters = payload["execution"]["inputs"]["parameters"]
    assert parameters["OPENAI_API_KEY"] == "[REDACTED]"
    assert parameters["parameters"]["GIBD_API_KEY"] == "[REDACTED]"
    assert parameters["source_credentials"] == "[REDACTED]"
    assert parameters["max_cloud_percent"] == 20
    assert payload["reproducibility"]["parameters"] == parameters


def test_artifacts_include_semantic_role_labels_and_original_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(service_core, "DATA_DIR", tmp_path / "Data")
    output_dir = tmp_path / "generated"
    output_dir.mkdir()
    summary_path = output_dir / "gee_ndvi_summary_example.json"
    plan_path = output_dir / "gee_validated_plan_example.json"
    summary_path.write_text(json.dumps({"ndvi": {"mean": 0.5}}), encoding="utf-8")
    plan_path.write_text(json.dumps({"plan": {"action": "ndvi_summary"}}), encoding="utf-8")

    payload = _build_task_payload(
        task_id="artifact-role-test-task",
        agent_id="google_earth_engine_agent",
        agent_name="Google Earth Engine Agent",
        agent_version="1.0.0",
        state="TASK_STATE_COMPLETED",
        query="Summarize NDVI",
        requested_skill=None,
        result={
            "agent_name": "Google Earth Engine Agent",
            "agent_version": "1.0.0",
            "outputs": {
                "text": "Done.",
                "ndvi_summary_json_file": str(summary_path),
                "validated_plan_json_file": str(plan_path),
                "output_files": [str(summary_path), str(plan_path)],
            },
        },
        error_message=None,
        agent_id_for_artifacts="google_earth_engine_agent",
        output_delivery="url",
        public_base_url="http://testserver",
    )

    artifacts = payload["outputs"]["artifacts"]
    assert [artifact["role"] for artifact in artifacts] == ["ndvi_summary_json_file", "validated_plan_json_file"]
    assert artifacts[0]["label"] == "Ndvi Summary Json"
    assert artifacts[0]["original_filename"] == summary_path.name
    assert payload["reproducibility"]["output_artifacts"][0]["role"] == "ndvi_summary_json_file"
    assert payload["reproducibility"]["output_artifacts"][1]["label"] == "Validated Plan Json"


def test_generic_artifact_keys_use_clean_roles_while_semantic_roles_are_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(service_core, "DATA_DIR", tmp_path / "Data")
    output_dir = tmp_path / "generated"
    output_dir.mkdir()
    output_path = output_dir / "generic_output.geojson"
    dataset_path = output_dir / "dataset_output.gpkg"
    semantic_path = output_dir / "gee_ndvi_map.html"
    output_path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    dataset_path.write_text("placeholder", encoding="utf-8")
    semantic_path.write_text("<html></html>", encoding="utf-8")

    payload = _build_task_payload(
        task_id="generic-artifact-role-test-task",
        agent_id="metadata_agent",
        agent_name="Metadata Agent",
        agent_version="1.0.0",
        state="TASK_STATE_COMPLETED",
        query="Return mixed outputs",
        requested_skill=None,
        result={
            "agent_name": "Metadata Agent",
            "agent_version": "1.0.0",
            "outputs": {
                "text": "Done.",
                "output_file": str(output_path),
                "dataset_path": str(dataset_path),
                "ndvi_map_html_file": str(semantic_path),
            },
        },
        error_message=None,
        agent_id_for_artifacts="metadata_agent",
        output_delivery="url",
        public_base_url="http://testserver",
    )

    roles = [artifact["role"] for artifact in payload["outputs"]["artifacts"]]
    assert roles == ["output", "dataset", "ndvi_map_html_file"]
    assert [artifact["role"] for artifact in payload["reproducibility"]["output_artifacts"]] == roles


def test_external_url_artifacts_include_semantic_role_labels(tmp_path, monkeypatch):
    monkeypatch.setattr(service_core, "DATA_DIR", tmp_path / "Data")

    payload = _build_task_payload(
        task_id="external-artifact-test-task",
        agent_id="google_earth_engine_agent",
        agent_name="Google Earth Engine Agent",
        agent_version="1.0.0",
        state="TASK_STATE_COMPLETED",
        query="Create NDVI thumbnail",
        requested_skill=None,
        result={
            "agent_name": "Google Earth Engine Agent",
            "agent_version": "1.0.0",
            "outputs": {
                "text": "Created thumbnail.",
                "ndvi_thumbnail_png_url": {
                    "kind": "downloadable_file",
                    "filename": "gee_ndvi_thumbnail.png",
                    "format": "png",
                    "mime_type": "image/png",
                    "size_bytes": None,
                    "url": "https://earthengine.example/thumbnail.png",
                    "_artifact_role": "ndvi_thumbnail_png_url",
                    "_artifact_label": "Earth Engine NDVI Preview",
                    "_original_filename": "gee_ndvi_thumbnail.png",
                },
            },
        },
        error_message=None,
        agent_id_for_artifacts="google_earth_engine_agent",
        output_delivery="url",
        public_base_url="http://testserver",
    )

    artifact = payload["outputs"]["artifacts"][0]
    assert artifact["role"] == "ndvi_thumbnail_png_url"
    assert artifact["label"] == "Earth Engine NDVI Preview"
    assert artifact["name"] == "Earth Engine NDVI Preview PNG"
    assert artifact["filename"] == "gee_ndvi_thumbnail.png"
    assert artifact["description"]
    assert artifact["url"] == "https://earthengine.example/thumbnail.png"


def test_weak_generated_filenames_do_not_drive_semantic_artifact_names(tmp_path, monkeypatch):
    monkeypatch.setattr(service_core, "DATA_DIR", tmp_path / "Data")
    output_dir = tmp_path / "generated"
    output_dir.mkdir()
    output_path = output_dir / "find_out_208141.geojson"
    output_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"county": "Example County", "earthquake_count": 3},
                        "geometry": {"type": "Point", "coordinates": [-77.0, 40.0]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = _build_task_payload(
        task_id="weak-filename-artifact-test-task",
        agent_id="vector_analysis_agent",
        agent_name="Vector Analysis Agent",
        agent_version="1.0.0",
        state="TASK_STATE_COMPLETED",
        query="Find out how many earthquake in each county. Return geojson",
        requested_skill=None,
        result={
            "outputs": {
                "text": "Created a county-level earthquake count GeoJSON.",
                "dataset_path": str(output_path),
            },
            "metrics": {"number_of_artifacts": 1},
        },
        error_message=None,
        agent_id_for_artifacts="vector_analysis_agent",
        output_delivery="url",
        public_base_url="http://testserver",
    )

    artifact = payload["outputs"]["artifacts"][0]
    assert artifact["filename"].startswith("vector_analysis_agent-")
    assert artifact["original_filename"] == "find_out_208141.geojson"
    assert artifact["name"] == "Earthquake Count by County GeoJSON"
    assert "Find Out" not in artifact["name"]
    assert "representing Earthquake Count by County" in artifact["description"]


def test_download_action_filenames_use_dataset_subject_from_context(tmp_path, monkeypatch):
    monkeypatch.setattr(service_core, "DATA_DIR", tmp_path / "Data")
    output_dir = tmp_path / "generated"
    output_dir.mkdir()
    output_path = output_dir / "download_ca_3422342343.gpkg"
    output_path.write_text("placeholder", encoding="utf-8")

    payload = _build_task_payload(
        task_id="download-action-filename-test-task",
        agent_id="geospatial_data_retrieval_agent",
        agent_name="Geospatial Data Retrieval Agent",
        agent_version="1.0.0",
        state="TASK_STATE_COMPLETED",
        query="Download CA counties as GeoPackage",
        requested_skill=None,
        result={
            "outputs": {
                "text": "Downloaded California county boundaries.",
                "dataset_path": str(output_path),
            },
            "metrics": {"number_of_artifacts": 1},
        },
        error_message=None,
        agent_id_for_artifacts="geospatial_data_retrieval_agent",
        output_delivery="url",
        public_base_url="http://testserver",
    )

    artifact = payload["outputs"]["artifacts"][0]
    assert artifact["name"] == "California Counties GeoPackage"
    assert "Download" not in artifact["name"]
    assert "3422342343" not in artifact["name"]
    assert artifact["original_filename"] == "download_ca_3422342343.gpkg"


def test_multiple_state_outputs_use_each_artifacts_filename_subject(tmp_path, monkeypatch):
    monkeypatch.setattr(service_core, "DATA_DIR", tmp_path / "Data")
    output_dir = tmp_path / "generated"
    output_dir.mkdir()
    ca_path = output_dir / "california_counties.geojson"
    pa_path = output_dir / "pennsylvania_counties.geojson"
    empty_geojson = json.dumps({"type": "FeatureCollection", "features": []})
    ca_path.write_text(empty_geojson, encoding="utf-8")
    pa_path.write_text(empty_geojson, encoding="utf-8")

    payload = _build_task_payload(
        task_id="multi-state-artifact-name-test-task",
        agent_id="geospatial_data_retrieval_agent",
        agent_name="Geospatial Data Retrieval Agent",
        agent_version="1.0.0",
        state="TASK_STATE_COMPLETED",
        query="Download the CA counties and PA counties, save to two geojson files",
        requested_skill=None,
        result={
            "outputs": {
                "text": "Downloaded county boundaries for California and Pennsylvania.",
                "output_files": [str(ca_path), str(pa_path)],
            },
            "metrics": {"number_of_artifacts": 2},
        },
        error_message=None,
        agent_id_for_artifacts="geospatial_data_retrieval_agent",
        output_delivery="url",
        public_base_url="http://testserver",
    )

    artifacts = payload["outputs"]["artifacts"]
    names_by_original = {artifact["original_filename"]: artifact["name"] for artifact in artifacts}

    assert names_by_original["california_counties.geojson"] == "California Counties GeoJSON"
    assert names_by_original["pennsylvania_counties.geojson"] == "Pennsylvania Counties GeoJSON"


def test_agent_capability_documents_do_not_contain_unconfigured_machine_paths_or_hosts():
    for path in AGENT_CAPABILITY_FILES:
        for ancestry, value in _walk_strings(_load_json(path)):
            for pattern in DISALLOWED_MACHINE_PATTERNS:
                assert not pattern.search(value), (path, ".".join(ancestry), value)

            field_name = ancestry[-1].lower() if ancestry else ""
            if field_name not in {"url", "base_url", "describeurl"}:
                continue

            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"}:
                continue
            assert parsed.hostname in ALLOWED_DEPLOYMENT_HOSTS, (
                path,
                ".".join(ancestry),
                value,
            )



