from __future__ import annotations

import os
import pkgutil
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Dict

from flask import Flask

from gas_server.core.geo_agent import GeoAgent
from gas_server.core.specs import AgentSpec, NameFn, RunFn, RunWithProgressFn, ValidatorFn, VersionFn, no_validation
from gas_server.core.specs import require_input_datasets


# This module is the bridge between the tiny per-agent service wrapper files
# and the shared GAS framework. New agent developers usually do not edit this
# file. Instead, they create `gas_server/services/my_agent_service.py` and call
# `register_geo_agent(MyAgent, __name__)`; the registry discovers that wrapper
# automatically at import time.
@dataclass(frozen=True)
class ServiceRegistration:
    """Registration metadata for one published GeoAgent service.

    A ServiceRegistration records enough information to lazily import the agent
    class, build the agent, create its service spec, and load its Flask app.
    The service wrapper owns only the two things that differ by plugin:
    the imported GeoAgent subclass and this registration object.
    """

    agent_id: str
    service_module: str
    agent_module: str
    agent_class: str
    run_agent: RunFn
    get_name: NameFn
    get_version: VersionFn
    run_agent_with_progress: RunWithProgressFn | None = None
    validate_inputs: ValidatorFn = no_validation

    @property
    def capability_file(self) -> str:
        """DescribeAgent JSON file expected for this service."""
        return f"{self.agent_id}.json"

    @property
    def public_name(self) -> str:
        return self.agent_id

    def load_agent_class(self) -> type:
        """Import and return the registered GeoAgent class lazily."""
        module = import_module(self.agent_module)
        return getattr(module, self.agent_class)

    def build_agent(self, model: str | None = None) -> Any:
        """Create an agent instance for one request.

        The request-time model override is passed here. If model is None, the
        agent constructor should fall back to its developer-selected default.
        Request-time API keys are attached later by `configure_agent_client()`;
        the environment fallback mainly supports local development and tests.
        """
        return self.load_agent_class()(
            api_key=os.environ.get("OPENAI_API_KEY"),
            model=model,
        )

    def build_spec(self) -> AgentSpec:
        """Convert registration metadata into the lower-level service spec."""
        return AgentSpec(
            agent_id=self.agent_id,
            build_agent=self.build_agent,
            run_agent=self.run_agent,
            run_agent_with_progress=self.run_agent_with_progress,
            get_name=self.get_name,
            get_version=self.get_version,
            validate_inputs=self.validate_inputs,
            requires_model_credentials=getattr(self.load_agent_class(), "requires_model_credentials", True),
        )

    def load_app(self) -> Flask:
        """Load the Flask app published by this service wrapper."""
        module = import_module(self.service_module)
        if hasattr(module, "get_service_app"):
            return module.get_service_app()
        return module.app


def register_geo_agent(agent_class: type[GeoAgent], service_module: str) -> ServiceRegistration:
    """Create a GAS service registration from a GeoAgent subclass.

    This is the only function a normal service wrapper should call. It reads
    metadata from the agent class, wires the standard `run_service()` adapter,
    and turns `requires_input_datasets=True` into shared request validation.
    """
    agent_id = getattr(agent_class, "agent_id", "")
    if not agent_id:
        raise ValueError(f"{agent_class.__name__}.agent_id must be set.")

    validator = require_input_datasets(agent_id) if getattr(agent_class, "requires_input_datasets", False) else no_validation
    return ServiceRegistration(
        agent_id=agent_id,
        service_module=service_module,
        agent_module=agent_class.__module__,
        agent_class=agent_class.__name__,
        run_agent=lambda agent, text, input_dataset_paths, parameters=None: agent.run_service(
            text,
            input_dataset_paths,
            parameters=parameters,
        ),
        run_agent_with_progress=lambda agent, text, input_dataset_paths, progress_callback, parameters=None: agent.run_service(
            text,
            input_dataset_paths,
            progress_callback=progress_callback,
            parameters=parameters,
        ),
        get_name=lambda agent: agent.display_name,
        get_version=lambda agent: agent.version,
        validate_inputs=validator,
    )


def _iter_service_modules() -> list[str]:
    """Find all plugin-style service wrappers in gas_server.services."""
    package = import_module("gas_server.services")
    modules: list[str] = []
    for module_info in pkgutil.iter_modules(package.__path__):
        module_name = module_info.name
        if module_name.endswith("_agent_service"):
            modules.append(f"{package.__name__}.{module_name}")
    return sorted(modules)


def _discover_service_registrations() -> Dict[str, ServiceRegistration]:
    """Import service wrappers and collect their REGISTRATION objects."""
    registrations: Dict[str, ServiceRegistration] = {}
    for module_name in _iter_service_modules():
        module = import_module(module_name)
        registration = getattr(module, "REGISTRATION", None)
        if registration is None:
            continue
        if not isinstance(registration, ServiceRegistration):
            raise TypeError(f"{module_name}.REGISTRATION must be a ServiceRegistration.")
        if registration.agent_id in registrations:
            raise ValueError(f"Duplicate GAS agent id: {registration.agent_id}")
        if registration.service_module != module_name:
            raise ValueError(
                f"{module_name}.REGISTRATION service_module must be {module_name!r}."
            )
        registrations[registration.agent_id] = registration
    return registrations


# Import-time discovery means adding a new `*_agent_service.py` file is enough
# to publish a service, as long as the wrapper defines REGISTRATION.
SERVICE_REGISTRY: Dict[str, ServiceRegistration] = _discover_service_registrations()


def get_service_registration(agent_id: str) -> ServiceRegistration:
    return SERVICE_REGISTRY[agent_id]


def agent_ids() -> tuple[str, ...]:
    return tuple(SERVICE_REGISTRY)


def agent_specs_by_agent_id() -> Dict[str, AgentSpec]:
    return {
        agent_id: registration.build_spec()
        for agent_id, registration in SERVICE_REGISTRY.items()
    }


def load_service_apps() -> Dict[str, Flask]:
    return {
        agent_id: registration.load_app()
        for agent_id, registration in SERVICE_REGISTRY.items()
    }


def capability_files_by_agent_id() -> Dict[str, str]:
    return {
        registration.agent_id: registration.capability_file
        for registration in SERVICE_REGISTRY.values()
    }
