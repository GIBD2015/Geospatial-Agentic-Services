from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any, Callable, ClassVar


ProgressCallback = Callable[[dict[str, Any]], None]

# Progress stage names are intentionally shared across agents. They give
# streaming clients and AI orchestrators a stable vocabulary for interpreting
# long-running work without requiring every agent to expose the same internal
# implementation details.
COMMON_PROGRESS_STAGES = (
    "start",
    "input_inspection",
    "data_validation",
    "validation",
    "method_selection",
    "planning",
    "llm_generation",
    "code_execution",
    "analysis_execution",
    "artifact_generation",
    "response_preparation",
    "complete",
    "retry",
    "fallback_start",
    "fallback_complete",
    "warning",
    "error",
)

DOMAIN_PROGRESS_STAGES = (
    "source_selection",
    "source_validation",
    "download_start",
    "download_complete",
    "normalization",
    "map_design",
    "layer_preparation",
    "symbology",
    "html_generation",
    "model_selection",
    "weights_construction",
    "model_execution",
    "diagnostics_generation",
    "report_generation",
    "export_started",
    "export_wait",
)

RECOMMENDED_PROGRESS_STAGES = COMMON_PROGRESS_STAGES + DOMAIN_PROGRESS_STAGES


@dataclass(frozen=True)
class GeoAgentContext:
    """Standard execution context passed from a GAS service to a GeoAgent.

    Most new agent developers do not need to instantiate this class directly.
    The shared service layer creates it inside ``GeoAgent.run_service()`` after
    it has parsed the request, materialized input datasets, and collected
    request parameters. It is useful when an advanced agent wants one object
    containing everything about the current service call.
    """

    # Natural-language user instruction extracted from task.instructions.
    query: str

    # Local filesystem paths prepared by the service layer. Public clients may
    # submit URLs or encoded files, but agent implementations should work with
    # these already-materialized paths.
    input_dataset_paths: list[str] = field(default_factory=list)

    # Callback used only during streaming requests. In non-streaming mode this
    # is normally None, and emit_progress() safely becomes a no-op.
    progress_callback: ProgressCallback | None = None

    # Original request parameters such as model, artifact_delivery,
    # source_credentials, requested_skill, and other agent-specific options.
    parameters: dict[str, Any] = field(default_factory=dict)


class GeoAgent(ABC):
    """Abstract base class for geospatial agents published by the GAS server.

    Developer interface
    -------------------
    For a normal new agent, subclass ``GeoAgent`` and implement only ``run()``.
    Do not override ``run_service()``. The service layer calls ``run_service()``
    for every published agent, and this base implementation adapts GAS HTTP
    requests into the simple ``run(query, input_dataset_paths, progress_callback)``
    shape that agent developers work with.

    A subclass should also set the class metadata fields below. These fields
    are used by registration, health checks, standard responses, and tests.
    """

    # Public machine-readable ID. This must match the capability document file
    # name, e.g. agent_id="my_agent" -> gas_server/capabilities/my_agent.json.
    agent_id: ClassVar[str] = ""

    # Human-readable name shown in responses and DescribeAgent documents.
    agent_name: ClassVar[str] = ""

    # Agent implementation version, not the GAS protocol version.
    agent_version: ClassVar[str] = ""

    # Short summary used by health/registry style views.
    agent_description: ClassVar[str] = ""

    # Set True when the service must reject requests with no input_datasets
    # before the agent starts running.
    requires_input_datasets: ClassVar[bool] = False

    # Most built-in agents are model-backed and require request credentials.
    # Deterministic-first agents can set this False and optionally use an LLM
    # only when credentials are supplied.
    requires_model_credentials: ClassVar[bool] = True

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        output_dir: str | Path | None = None,
    ) -> None:
        # The service layer later replaces the client/API key with request-time
        # credentials. The environment fallback is useful for tests and local
        # developer runs.
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")

        # Agents choose their default model in their own constructor by passing
        # model=model or "<default-model>". The service may override that value
        # per request with parameters.model.
        if model is not None:
            self.model = model

        # Agents that produce files should pass their output directory here.
        # The standard response builder uses returned artifact paths to expose
        # downloadable URLs or encoded artifacts.
        if output_dir is not None:
            self.output_dir = str(output_dir)

        # Raw request parameters for the current run. This is how an agent can
        # read optional fields beyond the standard query/input datasets, for
        # example source_credentials or a provider-specific model key.
        self.request_parameters: dict[str, Any] = {}
        self.reset_metrics()

    @abstractmethod
    def run(
        self,
        query: str,
        input_dataset_paths: list[str] | str | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Execute a geospatial task and return an agent result dictionary.

        This is the main method new developers implement.

        Parameters
        ----------
        query:
            Natural-language user request.
        input_dataset_paths:
            Local file paths prepared by the service layer. Do not expect raw
            base64 payloads here.
        progress_callback:
            Callback for streaming progress events. Pass it to emit_progress().

        Returns
        -------
        dict
            Agent-level result. The shared service layer normalizes this into
            the standard GAS task response with response/task/agent/outputs/
            execution/provenance/reproducibility/diagnostics sections.
        """


    def run_service(
        self,
        query: str,
        input_dataset_paths: list[str] | str | None = None,
        progress_callback: Any | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute the agent through the standard GAS service interface.

        New agents should not override this method. It is the stable adapter
        between the HTTP service framework and the agent implementation:

        1. Save request parameters on ``self.request_parameters``.
        2. Normalize datasets into a list of paths.
        3. Build a ``GeoAgentContext``.
        4. Call ``run_context()``, which normally calls the developer's ``run()``.
        """
        self.set_request_parameters(parameters)
        context = self.build_context(
            query=query,
            input_dataset_paths=input_dataset_paths,
            progress_callback=progress_callback,
            parameters=self.request_parameters,
        )
        return self.run_context(context)

    def set_request_parameters(self, parameters: dict[str, Any] | None = None) -> None:
        """Store the raw request parameters for the current service call."""
        self.request_parameters = dict(parameters or {})

    def build_context(
        self,
        *,
        query: str,
        input_dataset_paths: list[str] | tuple[str, ...] | str | None = None,
        progress_callback: ProgressCallback | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> GeoAgentContext:
        """Create the standard GAS execution context for this agent.

        This method keeps input normalization centralized. That means every
        agent receives input datasets the same way, regardless of whether the
        client originally sent a single string, a list, URLs, or encoded files.
        """
        return GeoAgentContext(
            query=query,
            input_dataset_paths=self.normalize_dataset_paths(input_dataset_paths),
            progress_callback=progress_callback,
            parameters=parameters or {},
        )

    def run_context(self, context: GeoAgentContext) -> dict[str, Any]:
        """Run this agent from a GeoAgentContext.

        The default implementation calls ``run()``. Advanced agents may
        override this method if they prefer to consume ``GeoAgentContext``
        directly, but plugin-style agents should normally keep the simpler
        ``run()`` method as their only execution hook.
        """
        return self.run(
            context.query,
            context.input_dataset_paths,
            progress_callback=context.progress_callback,
        )

    @staticmethod
    def normalize_dataset_paths(input_dataset_paths: list[str] | tuple[str, ...] | str | None) -> list[str]:
        """Normalize optional path input into a clean list of strings."""
        if input_dataset_paths is None:
            return []
        if isinstance(input_dataset_paths, str):
            return [input_dataset_paths]
        return [str(path) for path in input_dataset_paths if path]

    @staticmethod
    def ensure_directory(path: str | Path) -> Path:
        """Create a directory if needed and return it as a Path."""
        directory = Path(path)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def emit_progress(
        self,
        progress_callback: ProgressCallback | None,
        *,
        stage: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Send a best-effort progress event.

        Use this in long-running agents to keep streaming users informed:

        ``self.emit_progress(progress_callback, stage="planning", message="...")``

        In non-streaming mode ``progress_callback`` is normally None, so this
        method safely does nothing. Callback errors are intentionally swallowed
        because a UI/transport progress failure should not break the actual
        geospatial task.
        """
        if progress_callback is None:
            return
        try:
            event: dict[str, Any] = {"stage": stage, "message": message}
            if data:
                event["data"] = data
            progress_callback(event)
        except Exception:
            pass

    def _emit_progress(
        self,
        progress_callback: ProgressCallback | None,
        *,
        stage: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Alias for emit_progress().

        Some included agents still use ``_emit_progress`` in a few
        places. New agents can call ``emit_progress`` directly.
        """
        self.emit_progress(
            progress_callback,
            stage=stage,
            message=message,
            data=data,
        )

    def reset_metrics(self) -> None:
        """Reset per-run counters used in response provenance."""
        self.llm_calls = 0
        self.tool_calls = 0
        self.code_executions = 0
        self.retries = 0
        self.number_of_artifacts = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def increment_llm_calls(self, amount: int = 1) -> int:
        """Increment and return the LLM call count for this run."""
        self.llm_calls = int(getattr(self, "llm_calls", 0)) + amount
        return self.llm_calls

    def increment_tool_calls(self, amount: int = 1) -> int:
        """Increment and return non-LLM tool/library call count."""
        self.tool_calls = int(getattr(self, "tool_calls", 0)) + amount
        return self.tool_calls

    def increment_code_executions(self, amount: int = 1) -> int:
        """Increment and return generated-code execution count."""
        self.code_executions = int(getattr(self, "code_executions", 0)) + amount
        return self.code_executions

    def increment_retries(self, amount: int = 1) -> int:
        """Increment and return retry count for repair/fallback loops."""
        self.retries = int(getattr(self, "retries", 0)) + amount
        return self.retries

    def set_artifact_count(self, count: int) -> None:
        """Set the number of artifacts created by this run."""
        self.number_of_artifacts = count

    def metrics(self, *, number_of_artifacts: int | None = None, **extra: Any) -> dict[str, Any]:
        """Build the standard metrics block consumed by response normalization."""
        artifact_count = (
            self.number_of_artifacts
            if number_of_artifacts is None
            else number_of_artifacts
        )
        return {
            "llm_calls": getattr(self, "llm_calls", 0),
            "tool_calls": getattr(self, "tool_calls", 0),
            "number_of_artifacts": artifact_count,
            **extra,
        }

    def success_result(
        self,
        text: str,
        *,
        outputs: dict[str, Any] | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """Build a compact successful agent result.

        Agents with complex outputs may return their own richer dictionary, but
        this helper is useful for simple agents and tests. The service layer
        will still wrap it into the full standard task response.
        """
        payload_outputs = {"text": text}
        if outputs:
            payload_outputs.update(outputs)
        return {
            "agent_name": self.display_name,
            "agent_version": self.version,
            "outputs": payload_outputs,
            **extra,
        }

    def error_result(self, message: str, **extra: Any) -> dict[str, Any]:
        """Build an agent result that carries an error message.

        This helper does not raise. Use it when the agent wants to return a
        controlled diagnostic response instead of failing the whole service call.
        """
        return self.success_result(message, error=message, **extra)

    @property
    def display_name(self) -> str:
        """Human-readable agent name with a class-name fallback."""
        return self.agent_name or self.__class__.__name__

    @property
    def version(self) -> str:
        """Agent version with a conservative fallback."""
        return self.agent_version or "0.0.0"
