from __future__ import annotations

from gas_server.core.specs import AgentSpec
from gas_server.core.service_registry import agent_specs_by_agent_id


SPECS: dict[str, AgentSpec] = agent_specs_by_agent_id()

