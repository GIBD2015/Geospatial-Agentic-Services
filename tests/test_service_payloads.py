import base64
from io import BytesIO

from gas_server.core import service_core
from gas_server.core.service_core import _extract_stream_requested, _extract_text_and_input_dataset_paths


def test_extracts_plain_query_payload_for_service_clients():
    query, input_dataset_paths, metadata, message = _extract_text_and_input_dataset_paths(
        {"query": "Download PA counties", "stream": True, "output_delivery": "Encoded"}
    )

    assert query == "Download PA counties"
    assert input_dataset_paths == []
    assert metadata["stream"] is True
    assert metadata["output_delivery"] == "Encoded"
    assert message["parts"] == [{"kind": "text", "text": "Download PA counties"}]
    assert _extract_stream_requested(metadata) is True


def test_extracts_string_message_payload_for_service_clients():
    query, input_dataset_paths, metadata, message = _extract_text_and_input_dataset_paths(
        {"message": "Download PA counties"}
    )

    assert query == "Download PA counties"
    assert input_dataset_paths == []
    assert metadata["requested_skill"] is None
    assert message["parts"] == [{"kind": "text", "text": "Download PA counties"}]


def test_extracts_encoded_input_dataset_object(tmp_path, monkeypatch):
    monkeypatch.setattr(service_core, "DATA_DIR", tmp_path / "Data")

    query, input_dataset_paths, metadata, message = _extract_text_and_input_dataset_paths(
        {
            "query": "Map this dataset",
            "input_datasets": [
                {
                    "filename": "sample.geojson",
                    "data": base64.b64encode(b'{"type":"FeatureCollection","features":[]}').decode("ascii"),
                }
            ],
        }
    )

    assert query == "Map this dataset"
    assert len(input_dataset_paths) == 1
    assert input_dataset_paths[0].endswith("sample.geojson")
    assert (tmp_path / "Data" / "_uploads").is_dir()


def test_extracts_input_dataset_url_or_path_string(tmp_path, monkeypatch):
    monkeypatch.setattr(service_core, "DATA_DIR", tmp_path / "Data")

    query, input_dataset_paths, metadata, message = _extract_text_and_input_dataset_paths(
        {
            "query": "Analyze this dataset",
            "input_datasets": ["Data/example.geojson"],
        }
    )

    assert query == "Analyze this dataset"
    assert input_dataset_paths == ["Data/example.geojson"]


def test_extracts_mixed_top_level_input_datasets(tmp_path, monkeypatch):
    monkeypatch.setattr(service_core, "DATA_DIR", tmp_path / "Data")

    query, input_dataset_paths, metadata, message = _extract_text_and_input_dataset_paths(
        {
            "query": "Analyze mixed inputs",
            "input_datasets": [
                "Data/example.geojson",
                {
                    "filename": "sample.csv",
                    "data": base64.b64encode(b"id,value\n1,10\n").decode("ascii"),
                },
            ],
        }
    )

    assert query == "Analyze mixed inputs"
    assert input_dataset_paths[0] == "Data/example.geojson"
    assert input_dataset_paths[1].endswith("sample.csv")


def test_request_payload_converts_multipart_upload_to_dataset_file():
    app = service_core.Flask("payload-test")

    with app.test_request_context(
        "/tasks",
        method="POST",
        data={
            "query": "Map uploaded data",
            "OPENAI_API_KEY": "test-key",
            "file": (BytesIO(b'{"type":"FeatureCollection","features":[]}'), "upload.geojson"),
        },
        content_type="multipart/form-data",
    ):
        payload = service_core._request_payload()

    assert payload["query"] == "Map uploaded data"
    assert payload["OPENAI_API_KEY"] == "test-key"
    input_datasets = payload["input_datasets"]
    assert input_datasets[0]["filename"] == "upload.geojson"
    assert input_datasets[0]["encoding"] == "base64"
    assert base64.b64decode(input_datasets[0]["data"]).startswith(b'{"type"')

