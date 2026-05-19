from pathlib import Path

from gas_server.core import config
from gas_server.core.service_registry import agent_ids


def test_ensure_runtime_dirs_creates_expected_directories(tmp_path, monkeypatch):
    data_dir = tmp_path / "Data"
    output_dir = tmp_path / "Output"

    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(config, "agent_data_folders", lambda: ("mapping_agent", "raster_agent"))

    config.ensure_runtime_dirs()

    assert data_dir.is_dir()
    assert (data_dir / "mapping_agent").is_dir()
    assert (data_dir / "raster_agent").is_dir()
    assert output_dir.is_dir()


def test_runtime_agent_data_folders_come_from_service_registry():
    assert config.agent_data_folders() == agent_ids()


def test_as_str_returns_platform_string():
    assert config.as_str(Path("Data") / "mapping_agent").endswith("Data/mapping_agent") or (
        config.as_str(Path("Data") / "mapping_agent").endswith("Data\\mapping_agent")
    )
