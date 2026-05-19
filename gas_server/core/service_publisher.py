from __future__ import annotations

from importlib import import_module

from flask import Flask

from gas_server.core.agent_specs import SPECS
from gas_server.core.service_core import create_service_app
from gas_server.core.specs import AgentSpec


def publish_service(agent_id: str) -> tuple[Flask, AgentSpec, str]:
    """Create the Flask service app for a registered agent.

    Per-agent service wrapper files call this lazily from their `_publish()`
    helper. Developers normally do not call it directly. The function resolves
    the agent's AgentSpec and passes it into `create_service_app()`, which adds
    the standard GAS routes such as /tasks, /tasks/{task_id}/status,
    /tasks/{task_id}/result, /tasks/{task_id}/cancel, /status, and
    /data/<filename>.
    """
    spec = SPECS.get(agent_id)
    if spec is None:
        service_module = import_module(f"gas_server.services.{agent_id}_service")
        registration = getattr(service_module, "REGISTRATION")
        spec = registration.build_spec()
    return create_service_app(spec), spec, spec.agent_id
