from __future__ import annotations

from http import HTTPStatus
import json
import os
from pathlib import Path
import sys

from flask import Flask, Response, request
from flask_cors import CORS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gas_server.core.config import SERVER_HOST, SERVER_PORT
from gas_server.core.service_registry import (
    SERVICE_REGISTRY,
    agent_ids,
    load_service_apps,
)


app = Flask("gas_server")
CORS(app)

INTERNAL_SERVICE_APPS = load_service_apps()
INTERNAL_SERVICE_CLIENTS = {
    agent_id: service_app.test_client()
    for agent_id, service_app in INTERNAL_SERVICE_APPS.items()
}
AGENT_IDS = agent_ids()

JSON_DIR = PROJECT_ROOT / "gas_server" / "capabilities"

DEFAULT_PUBLIC_BASE_URL = "https://www.geospatial-agentic-services.online"


def _query_arg(name: str, default: str = "") -> str:
    value = request.args.get(name)
    if value is not None:
        return value
    name_lower = name.lower()
    for key, candidate in request.args.items():
        if key.lower() == name_lower:
            return candidate
    return default


def _load_gas_json(file_name: str):
    file_path = JSON_DIR / file_name

    if not file_path.exists():
        return None

    with file_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _capabilities_base_url() -> str:
    return request.host_url.rstrip("/") if request else DEFAULT_PUBLIC_BASE_URL


def _capabilities_url(path: str, base_url: str | None = None) -> str:
    root = (base_url or _capabilities_base_url()).rstrip("/")
    return f"{root}{path}"


def _registry_capabilities_agents(base_url: str | None = None) -> list[dict[str, str]]:
    root = base_url or _capabilities_base_url()
    agents = []
    for registration in SERVICE_REGISTRY.values():
        agent_class = registration.load_agent_class()
        describe_path = f"/?SERVICE=GAS&VERSION=1.0.0&REQUEST=DescribeAgent&agent_id={registration.agent_id}"
        agents.append(
            {
                "agent_id": registration.agent_id,
                "name": getattr(agent_class, "agent_name", None) or registration.agent_id,
                "DescribeAgent": _capabilities_url(describe_path, root),
            }
        )
    return agents


def _capabilities_operations(payload: dict, base_url: str | None = None) -> list[dict]:
    operations = payload.get("operations")
    if not isinstance(operations, list):
        return []

    return [
        {
            **operation,
            "url": _capabilities_url(str(operation.get("path") or operation.get("url") or ""), base_url),
        }
        for operation in operations
        if isinstance(operation, dict)
    ]


def _load_get_capabilities_json():
    payload = _load_gas_json("capabilities.json")
    if payload is None:
        return None
    base_url = _capabilities_base_url()
    return {
        **payload,
        "base_url": base_url,
        "operations": _capabilities_operations(payload, base_url),
        "agents": _registry_capabilities_agents(base_url),
    }


def _json_response(payload, status=HTTPStatus.OK):
    return Response(
        json.dumps(payload, indent=2),
        status=status,
        mimetype="application/json",
    )


def _filter_headers(headers):
    excluded = {"content-length", "connection", "transfer-encoding", "content-encoding", "host"}
    return {key: value for key, value in headers.items() if key.lower() not in excluded}


def _forward_headers(agent_id: str):
    forwarded = _filter_headers(request.headers)
    forwarded["X-Forwarded-Proto"] = request.scheme
    forwarded["X-Forwarded-Host"] = request.host
    forwarded["X-Proxy-Base-Url"] = request.host_url.rstrip("/")
    forwarded["X-Proxy-Agent-Id"] = agent_id
    return forwarded


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y", "on"}
    return bool(value)


def _payload_requests_stream(payload) -> bool:
    if not isinstance(payload, dict):
        return False

    task = payload.get("task")
    if isinstance(task, dict) and str(task.get("mode", "")).strip().lower() == "stream":
        return True

    candidates = [payload]
    message = payload.get("message")
    if isinstance(message, dict):
        candidates.append(message)
        for key in ("Params", "params", "metadata"):
            value = message.get(key)
            if isinstance(value, dict):
                candidates.append(value)

    for key in ("Params", "params", "metadata"):
        value = payload.get(key)
        if isinstance(value, dict):
            candidates.append(value)

    for candidate in candidates:
        for key in ("stream", "Stream", "STREAM"):
            if key in candidate:
                return _truthy(candidate.get(key))
    return False


@app.route("/", methods=["GET"])
def root():
    service = _query_arg("SERVICE").upper()
    version = _query_arg("VERSION")
    gas_request = _query_arg("REQUEST")
    gas_request_key = gas_request.lower()

    if service == "GAS":
        if not version:
            return _json_response(
                {
                    "error": {
                        "code": "MISSING_VERSION",
                        "message": "GAS requests must include VERSION=1.0.0.",
                    }
                },
                HTTPStatus.BAD_REQUEST,
            )

        if version != "1.0.0":
            return _json_response(
                {
                    "error": {
                        "code": "INVALID_VERSION",
                        "message": f"Unsupported GAS version '{version}'.",
                    }
                },
                HTTPStatus.BAD_REQUEST,
            )

        if gas_request_key == "getcapabilities":
            payload = _load_get_capabilities_json()

            if payload is None:
                return _json_response(
                    {
                        "error": {
                            "code": "NOT_FOUND",
                            "message": "capabilities.json was not found.",
                        }
                    },
                    HTTPStatus.NOT_FOUND,
                )

            return _json_response(payload)

        if gas_request_key == "describeagent":
            agent_name = _query_arg("agent_id")
            if agent_name not in INTERNAL_SERVICE_CLIENTS:
                return _json_response(
                    {
                        "error": {
                            "code": "INVALID_ARGUMENT",
                            "message": f"Unknown agent '{agent_name}'.",
                        }
                    },
                    HTTPStatus.NOT_FOUND,
                )
            upstream = INTERNAL_SERVICE_CLIENTS[agent_name].open(
                path="/",
                method="GET",
                headers=_forward_headers(agent_name),
                query_string={
                    "SERVICE": "GAS",
                    "VERSION": "1.0.0",
                    "REQUEST": "DescribeAgent",
                    "agent_id": agent_name,
                },
            )
            return Response(
                upstream.get_data(),
                status=upstream.status_code,
                mimetype="application/json",
            )

        return _json_response(
            {
                "error": {
                    "code": "INVALID_ARGUMENT",
                    "message": f"Unknown GAS request '{gas_request}'.",
                }
            },
            HTTPStatus.BAD_REQUEST,
        )

    return _json_response(
        {
            "name": "GAS Server",
            "server": {"host": SERVER_HOST, "port": SERVER_PORT},
            "agents": {
                agent_id: {
                    "agent_id": agent_id,
                    "service_base": f"/agents/{agent_id}",
                }
                for agent_id in AGENT_IDS
            },
        }
    )


@app.route("/agents/<agent_id>", defaults={"subpath": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
@app.route("/agents/<agent_id>/<path:subpath>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def proxy(agent_id: str, subpath: str):
    if agent_id not in INTERNAL_SERVICE_APPS:
        return _json_response(
            {
                "error": {
                    "code": "INVALID_ARGUMENT",
                    "message": f"Unknown agent '{agent_id}'.",
                }
            },
            HTTPStatus.NOT_FOUND,
        )

    upstream_path = f"/{subpath}" if subpath else "/"
    upstream_client = INTERNAL_SERVICE_CLIENTS[agent_id]
    request_body = request.get_data() or None
    request_payload = request.get_json(silent=True) if request_body else None
    should_stream = subpath == "tasks" and _payload_requests_stream(request_payload)
    upstream = upstream_client.open(
        path=upstream_path,
        method=request.method,
        headers=_forward_headers(agent_id),
        data=request_body,
        query_string=request.query_string,
        buffered=not should_stream,
    )
    upstream_status = upstream.status_code
    upstream_headers = dict(upstream.headers.items())

    if should_stream:
        def _stream():
            try:
                for chunk in upstream.response:
                    if chunk:
                        if isinstance(chunk, str):
                            yield chunk.encode("utf-8")
                        else:
                            yield chunk
            finally:
                try:
                    upstream.close()
                except Exception:
                    pass

        response = Response(
            _stream(),
            status=upstream_status,
            mimetype=upstream_headers.get("Content-Type", "application/json"),
            direct_passthrough=True,
        )
        for key, value in _filter_headers(upstream_headers).items():
            response.headers[key] = value
        response.headers["X-Accel-Buffering"] = "no"
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Connection"] = "keep-alive"
        return response

    body = upstream.get_data()

    response = Response(body, status=upstream_status)
    for key, value in _filter_headers(upstream_headers).items():
        response.headers[key] = value
    return response


def main() -> None:
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)


if __name__ == "__main__":
    main()

