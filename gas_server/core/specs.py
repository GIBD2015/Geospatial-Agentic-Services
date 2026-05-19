from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


# AgentSpec is the internal service framework interface. Plugin authors normally
# do not create this by hand; `register_geo_agent()` builds it from a GeoAgent
# subclass. Keeping the interface explicit makes the service layer independent
# from any one concrete agent implementation.
RunFn = Callable[[Any, str, List[str], Dict[str, Any] | None], Dict[str, Any]]
RunWithProgressFn = Callable[[Any, str, List[str], Callable[[Dict[str, Any]], None], Dict[str, Any] | None], Dict[str, Any]]
BuildAgentFn = Callable[..., Any]
NameFn = Callable[[Any], str]
VersionFn = Callable[[Any], str]
ValidatorFn = Callable[[List[str]], Optional[str]]


@dataclass(frozen=True)
class AgentSpec:
    """Runtime specification used by the shared Flask service factory."""

    # Public agent ID, e.g. "web_mapping_app_agent".
    agent_id: str

    # Factory for creating a fresh agent instance. It may receive a request-time
    # model override and should otherwise use the agent's default model.
    build_agent: BuildAgentFn

    # Standard non-streaming execution adapter.
    run_agent: RunFn

    # Optional streaming execution adapter. All GeoAgent registrations provide
    # this through GeoAgent.run_service().
    run_agent_with_progress: RunWithProgressFn | None

    # Metadata accessors keep the service layer from depending on exact agent
    # attribute names.
    get_name: NameFn
    get_version: VersionFn

    # Request validator. For example, dataset-dependent agents reject requests
    # with no input_datasets before any LLM call or code execution starts.
    validate_inputs: ValidatorFn

    # Whether the service should reject requests that do not include model
    # credentials. Deterministic-first agents may set this False.
    requires_model_credentials: bool = True


def no_validation(input_dataset_paths: List[str]) -> Optional[str]:
    """Validator for agents that can run without input datasets."""
    return None


def require_input_datasets(agent_id: str) -> ValidatorFn:
    """Build a validator for agents that require at least one dataset."""

    def validate(input_dataset_paths: List[str]) -> Optional[str]:
        if input_dataset_paths:
            return None
        return f"{agent_id} requires at least one dataset in inputs.input_datasets."

    return validate
