from __future__ import annotations

import os
import re
from types import SimpleNamespace
from typing import Any

import requests
from openai import OpenAI


GIBD_API_URL = os.getenv("GIBD_API_URL", "https://www.gibd.online").rstrip("/")


def format_service_name(name: str | None) -> str:
    """Normalize an agent name for LLM-provider logging/metadata."""
    if not name:
        return "Agent"

    normalized = name.replace("_", " ").strip()
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _to_jsonable(value: Any) -> Any:
    """Convert OpenAI-style SDK objects into JSON-compatible values.

    The GIBD compatibility endpoint receives ordinary JSON, so request payloads
    and response-like helper objects are recursively converted here.
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, SimpleNamespace):
        return {key: _to_jsonable(item) for key, item in vars(value).items()}
    if hasattr(value, "model_dump"):
        return _to_jsonable(value.model_dump(exclude_none=True))
    if hasattr(value, "to_dict"):
        return _to_jsonable(value.to_dict())
    if hasattr(value, "__dict__"):
        return {
            key: _to_jsonable(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return value


def _to_namespace(value: Any) -> Any:
    """Convert dictionaries back into attribute-style objects.

    This lets agents use the same `response.choices[0].message.content` style
    whether the backend is OpenAI directly or the GIBD OpenAI-compatible route.
    """
    if isinstance(value, list):
        return [_to_namespace(item) for item in value]
    if isinstance(value, dict):
        return SimpleNamespace(**{key: _to_namespace(item) for key, item in value.items()})
    return value


class GibdChatCompletions:
    """OpenAI-compatible chat.completions adapter for GIBD API keys.

    Agents should not need provider-specific branches. They can call
    `self.client.chat.completions.create(...)`; this adapter translates that
    call into the GIBD request-question-id and inference flow.
    """

    def __init__(self, *, user_key: str, service_name: str, api_url: str):
        self.user_key = user_key
        self.service_name = service_name
        self.api_url = api_url.rstrip("/")

    def _request_question_id(self) -> str:
        response = requests.post(
            f"{self.api_url}/api/request-question-id",
            json={
                "user_api_key": self.user_key,
                "service_name": self.service_name,
            },
            timeout=60,
        )

        try:
            payload = response.json()
        except ValueError:
            payload = {"message": response.text}

        if response.status_code != 201:
            raise RuntimeError(
                f"GIBD handshake failed for service '{self.service_name}': {payload}"
            )

        question_id = payload.get("question_id")
        if not question_id:
            raise RuntimeError(
                f"GIBD handshake succeeded but no question_id was returned for service '{self.service_name}'."
            )
        return question_id

    def create(self, **kwargs: Any) -> Any:
        question_id = self._request_question_id()
        payload = _to_jsonable(kwargs)
        payload["question_id"] = question_id
        payload["service_name"] = payload.get("service_name") or self.service_name
        payload["stream"] = bool(payload.get("stream", False))

        response = requests.post(
            f"{self.api_url}/api/openai/{self.user_key}",
            json=payload,
            timeout=1800,
        )

        try:
            response_payload = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"GIBD inference failed for service '{self.service_name}': {response.text}"
            ) from exc

        if response.status_code != 200:
            raise RuntimeError(
                f"GIBD inference failed for service '{self.service_name}': {response_payload}"
            )

        return _to_namespace(response_payload)


class GibdOpenAICompatClient:
    """Small client object with the same shape agents expect from OpenAI."""

    def __init__(self, *, user_key: str, service_name: str, api_url: str = GIBD_API_URL):
        self.chat = SimpleNamespace(
            completions=GibdChatCompletions(
                user_key=user_key,
                service_name=service_name,
                api_url=api_url,
            )
        )


def build_llm_client(
    *,
    service_name: str,
    openai_api_key: str | None = None,
    gibd_api_key: str | None = None,
) -> Any | None:
    """Create the request-time LLM client.

    If the client supplies `GIBD_API_KEY`, the agent receives a GIBD-backed
    OpenAI-compatible client. If the client supplies `OPENAI_API_KEY`, the
    agent receives the official OpenAI client. If no key is present, None is
    returned and the service layer rejects model-backed requests before run().
    """
    if gibd_api_key:
        return GibdOpenAICompatClient(
            user_key=gibd_api_key,
            service_name=format_service_name(service_name),
        )
    if openai_api_key:
        return OpenAI(api_key=openai_api_key)
    return None


def configure_agent_client(
    agent: Any,
    *,
    service_name: str,
    openai_api_key: str | None = None,
    gibd_api_key: str | None = None,
) -> Any:
    """Attach request-time LLM credentials to an agent instance.

    The service creates a fresh agent for each request, then calls this function
    before execution. Agent code can therefore use `self.client` without caring
    whether the caller chose OpenAI or GIBD credentials.
    """
    client = build_llm_client(
        service_name=service_name,
        openai_api_key=openai_api_key,
        gibd_api_key=gibd_api_key,
    )
    if client is None:
        raise ValueError(
            "Request must include either 'OPENAI API KEY' or 'GIBD API KEY'."
        )

    if hasattr(agent, "api_key"):
        agent.api_key = openai_api_key
    agent.client = client
    agent.service_name = format_service_name(service_name)
    agent.gibd_api_key = gibd_api_key
    return client
