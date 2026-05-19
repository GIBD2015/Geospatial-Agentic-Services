import threading
import time

from gas_server.core.service_core import create_service_app
from gas_server.core.specs import AgentSpec, no_validation


class DummyAgent:
    pass


def test_execute_task_uses_request_model_override(monkeypatch):
    built_models = []

    def build_agent(model=None):
        built_models.append(model)
        agent = DummyAgent()
        agent.model = model
        return agent

    def run_agent(agent, text, input_dataset_paths, parameters=None):
        return {
            "agent_name": "Dummy Agent",
            "agent_version": "1.0.0",
            "model": agent.model,
            "outputs": {"text": "done"},
        }

    monkeypatch.setattr(
        "gas_server.core.service_core.configure_agent_client",
        lambda *args, **kwargs: None,
    )

    spec = AgentSpec(
        agent_id="dummy_agent",
        build_agent=build_agent,
        run_agent=run_agent,
        run_agent_with_progress=None,
        get_name=lambda agent: "Dummy Agent",
        get_version=lambda agent: "1.0.0",
        validate_inputs=no_validation,
    )
    app = create_service_app(spec)

    response = app.test_client().post(
        "/tasks",
        json={
            "task": {"instructions": "Run with override", "mode": "sync"},
            "credentials": {"OPENAI_API_KEY": "test-key"},
            "parameters": {"model": "gpt-test-model"},
        },
            )

    assert response.status_code == 200
    assert built_models == [None, "gpt-test-model"]
    assert response.get_json()["agent"]["model"] == "gpt-test-model"


def test_post_tasks_returns_submitted_task_before_background_work_finishes(monkeypatch):
    release_worker = threading.Event()
    worker_started = threading.Event()

    def run_agent(agent, text, input_dataset_paths, parameters=None):
        worker_started.set()
        assert text == "Run async analysis"
        assert input_dataset_paths == ["Data/example.geojson"]
        assert parameters["OPENAI_API_KEY"] == "test-key"
        release_worker.wait(timeout=5)
        return {
            "agent_name": "Dummy Agent",
            "agent_version": "1.0.0",
            "outputs": {"text": "Async analysis finished."},
        }

    monkeypatch.setattr(
        "gas_server.core.service_core.configure_agent_client",
        lambda *args, **kwargs: None,
    )

    spec = AgentSpec(
        agent_id="dummy_agent",
        build_agent=DummyAgent,
        run_agent=run_agent,
        run_agent_with_progress=None,
        get_name=lambda agent: "Dummy Agent",
        get_version=lambda agent: "1.0.0",
        validate_inputs=no_validation,
    )
    app = create_service_app(spec)
    client = app.test_client()

    response = client.post(
        "/tasks",
        json={
            "task": {"instructions": "Run async analysis", "mode": "async"},
            "inputs": {"input_datasets": ["Data/example.geojson"]},
            "credentials": {"OPENAI_API_KEY": "test-key"},
        },
            )

    assert response.status_code == 202
    submitted = response.get_json()
    task_id = submitted["task"]["id"]
    assert submitted["task"]["status"] == "accepted"
    assert submitted["task"]["terminal"] is False
    assert response.headers["Location"] == f"/tasks/{task_id}/status"

    assert worker_started.wait(timeout=2)
    running_response = client.get(f"/tasks/{task_id}/status")
    assert running_response.status_code == 200
    assert running_response.get_json()["task"]["status"] in {
        "accepted",
        "running",
    }

    release_worker.set()
    deadline = time.time() + 5
    completed = None
    while time.time() < deadline:
        completed_response = client.get(f"/tasks/{task_id}/status")
        completed_status = completed_response.get_json()
        if completed_status["task"]["status"] == "successful":
            completed = client.get(f"/tasks/{task_id}/result").get_json()
            break
        time.sleep(0.05)

    assert completed["task"]["status"] == "successful"
    assert completed["task"]["terminal"] is True
    assert completed["outputs"]["summary"] == "Async analysis finished."


def test_post_tasks_stores_failed_task_when_background_work_raises(monkeypatch):
    worker_started = threading.Event()

    def run_agent(agent, text, input_dataset_paths, parameters=None):
        worker_started.set()
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "gas_server.core.service_core.configure_agent_client",
        lambda *args, **kwargs: None,
    )

    spec = AgentSpec(
        agent_id="dummy_agent",
        build_agent=DummyAgent,
        run_agent=run_agent,
        run_agent_with_progress=None,
        get_name=lambda agent: "Dummy Agent",
        get_version=lambda agent: "1.0.0",
        validate_inputs=no_validation,
    )
    app = create_service_app(spec)
    client = app.test_client()

    response = client.post(
        "/tasks",
        json={
            "task": {"instructions": "Run async analysis", "mode": "async"},
            "credentials": {"OPENAI_API_KEY": "test-key"},
        },
            )

    assert response.status_code == 202
    task_id = response.get_json()["task"]["id"]
    assert worker_started.wait(timeout=2)

    deadline = time.time() + 5
    failed = None
    while time.time() < deadline:
        task_response = client.get(f"/tasks/{task_id}/status")
        failed_status = task_response.get_json()
        if failed_status["task"]["status"] == "failed":
            failed = client.get(f"/tasks/{task_id}/result").get_json()
            break
        time.sleep(0.05)

    assert failed["task"]["status"] == "failed"
    assert failed["task"]["terminal"] is True
    assert failed["execution"]["status"] == "failed"
    assert failed["diagnostics"]["has_error"] is True
    assert failed["diagnostics"]["error"] == "boom"

