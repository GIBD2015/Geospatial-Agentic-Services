from pathlib import Path
import inspect
import re

from gas_server.agents.geospatial_data_retrieval_agent import GeospatialDataRetrievalAgent
from gas_server.core.geo_agent import GeoAgent, GeoAgentContext, RECOMMENDED_PROGRESS_STAGES
from gas_server.core.service_registry import SERVICE_REGISTRY


class DummyContextAgent(GeoAgent):
    agent_id = "dummy_context_agent"
    agent_name = "Dummy Context Agent"
    agent_version = "1.0.0"
    agent_description = "Test agent for the GeoAgent base class."

    def __init__(self):
        super().__init__(api_key="test-key", model="test-model")
        self.seen_context = None

    def run(self, query, input_dataset_paths=None, progress_callback=None):
        return {
            "query": query,
            "input_dataset_paths": input_dataset_paths,
            "progress_callback": progress_callback,
        }

    def run_context(self, context: GeoAgentContext):
        self.seen_context = context
        return self.run(
            context.query,
            context.input_dataset_paths,
            progress_callback=context.progress_callback,
        )


def test_geospatial_data_retrieval_implements_geo_agent_interface():
    agent = GeospatialDataRetrievalAgent(api_key="test-key")

    assert isinstance(agent, GeoAgent)
    assert agent.agent_id == "geospatial_data_retrieval_agent"
    assert agent.display_name == "Geospatial Data Retrieval Agent"
    assert agent.version == "1.0.0"


def test_registered_agents_implement_geo_agent_interface():
    for registration in SERVICE_REGISTRY.values():
        agent = registration.build_agent()

        assert isinstance(agent, GeoAgent), registration.agent_id
        assert agent.agent_id == registration.agent_id, registration.agent_id
        assert agent.display_name, registration.agent_id
        assert agent.version, registration.agent_id
        assert agent.agent_description, registration.agent_id


def test_agent_files_do_not_override_run_service():
    for path in Path("gas_server/agents").glob("*_agent.py"):
        assert "def run_service(" not in path.read_text(encoding="utf-8"), path


def test_registered_agents_use_standard_run_signature():
    expected = ["self", "query", "input_dataset_paths", "progress_callback"]

    for registration in SERVICE_REGISTRY.values():
        agent_class = registration.load_agent_class()
        signature = inspect.signature(agent_class.run)
        assert list(signature.parameters)[:4] == expected, registration.agent_id


def test_geo_agent_normalizes_dataset_paths():
    assert GeoAgent.normalize_dataset_paths(None) == []
    assert GeoAgent.normalize_dataset_paths("dataset.geojson") == ["dataset.geojson"]
    assert GeoAgent.normalize_dataset_paths(["a.geojson", None, "b.tif"]) == [
        "a.geojson",
        "b.tif",
    ]


def test_geo_agent_builds_standard_context_for_service_runs():
    events = []
    callback = events.append
    agent = DummyContextAgent()

    result = agent.run_service(
        "Map these inputs.",
        ("a.geojson", "b.tif"),
        progress_callback=callback,
    )

    assert agent.seen_context == GeoAgentContext(
        query="Map these inputs.",
        input_dataset_paths=["a.geojson", "b.tif"],
        progress_callback=callback,
        parameters={},
    )
    assert result["query"] == "Map these inputs."
    assert result["input_dataset_paths"] == ["a.geojson", "b.tif"]
    assert result["progress_callback"] is callback


def test_geo_agent_progress_and_metric_helpers():
    events = []
    agent = DummyContextAgent()

    agent.emit_progress(
        events.append,
        stage="input_inspection",
        message="Inspecting datasets.",
        data={"count": 2},
    )
    agent.increment_llm_calls()
    agent.increment_tool_calls(2)
    agent.increment_code_executions()
    agent.increment_retries()
    agent.set_artifact_count(3)

    assert events == [
        {
            "stage": "input_inspection",
            "message": "Inspecting datasets.",
            "data": {"count": 2},
        }
    ]
    assert agent.metrics(code_executions=agent.code_executions, retries=agent.retries) == {
        "llm_calls": 1,
        "tool_calls": 2,
        "number_of_artifacts": 3,
        "code_executions": 1,
        "retries": 1,
    }


def test_agent_metrics_use_standard_artifact_count_key():
    for path in Path("gas_server/agents").glob("*_agent.py"):
        assert '"number of artifacts"' not in path.read_text(encoding="utf-8"), path


def test_agents_use_recommended_progress_stage_vocabulary():
    stage_pattern = re.compile(r"stage=[\"']([a-z0-9_]+)[\"']")
    allowed = set(RECOMMENDED_PROGRESS_STAGES)

    for path in Path("gas_server/agents").glob("*_agent.py"):
        stages = stage_pattern.findall(path.read_text(encoding="utf-8"))
        unexpected = sorted(set(stages) - allowed)
        assert unexpected == [], f"{path}: {unexpected}"
