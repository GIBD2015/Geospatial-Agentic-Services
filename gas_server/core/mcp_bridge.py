"""Model Context Protocol (MCP) <-> GAS 1.0.0 Universal Bi-Directional Bridge.

Exposes every registered Geospatial Agentic Services (GAS 1.0.0) agent and the
Multi-Agent Workflow DAG Executor as standard MCP tools and resources over both:
- HTTP JSON-RPC 2.0 (`POST /mcp` and `GET /mcp/tools` on the GAS server)
- Standard I/O (`python -m gas_server.core.mcp_bridge`) for Claude Desktop,
  Cursor, Windsurf, OpenAI Agents SDK, and LangGraph.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from gas_server.core.service_registry import SERVICE_REGISTRY


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CAPABILITIES_DIR = PROJECT_ROOT / "gas_server" / "capabilities"
MCP_PROTOCOL_VERSION = "2024-11-05"


def _load_capability_doc(agent_id: str) -> dict[str, Any]:
    path = CAPABILITIES_DIR / f"{agent_id}.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


class GasMcpBridge:
    """Translates GAS 1.0.0 capabilities, descriptions, and task execution into MCP."""

    def __init__(self, *, internal_clients: dict[str, Any] | None = None) -> None:
        self._internal_clients = internal_clients

    def _get_internal_clients(self) -> dict[str, Any]:
        if self._internal_clients is None:
            from gas_server.core.service_registry import load_service_apps

            apps = load_service_apps()
            self._internal_clients = {aid: app.test_client() for aid, app in apps.items()}
        return self._internal_clients

    def list_tools(self) -> list[dict[str, Any]]:
        """Build MCP Tool definitions from all registered GAS 1.0.0 agents."""
        tools: list[dict[str, Any]] = []

        for agent_id in sorted(SERVICE_REGISTRY):
            doc = _load_capability_doc(agent_id)
            profile = doc.get("profile") if isinstance(doc.get("profile"), dict) else {}
            exec_spec = doc.get("execute_task") if isinstance(doc.get("execute_task"), dict) else {}
            inputs_spec = exec_spec.get("inputs", {}).get("input_datasets", {})
            requires_input = bool(inputs_spec.get("required", False))

            skills = doc.get("skills") if isinstance(doc.get("skills"), list) else []
            skill_names = [str(s.get("name") or s.get("skill_id")) for s in skills if isinstance(s, dict)]
            desc = str(profile.get("description") or exec_spec.get("description") or f"GAS agent {agent_id}")
            if skill_names:
                desc = f"{desc} Skills: {', '.join(skill_names[:6])}."

            required_props = ["instructions"]
            if requires_input:
                required_props.append("input_datasets")

            tools.append(
                {
                    "name": f"gas_{agent_id}",
                    "description": desc,
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "instructions": {
                                "type": "string",
                                "description": (
                                    exec_spec.get("task", {})
                                    .get("instructions", {})
                                    .get("description", "Natural-language task instructions for the GAS agent.")
                                ),
                            },
                            "input_datasets": {
                                "type": "array",
                                "description": inputs_spec.get(
                                    "description",
                                    "Optional input datasets (URLs, local server paths, or base64 encoded file objects).",
                                ),
                                "items": {
                                    "oneOf": [
                                        {"type": "string"},
                                        {
                                            "type": "object",
                                            "properties": {
                                                "filename": {"type": "string"},
                                                "encoding": {"type": "string"},
                                                "data": {"type": "string"},
                                                "mime_type": {"type": "string"},
                                            },
                                            "required": ["filename", "data"],
                                        },
                                    ]
                                },
                            },
                            "artifact_delivery": {
                                "type": "string",
                                "enum": ["URL", "Encoded"],
                                "default": "URL",
                                "description": "Delivery mode for generated spatial artifacts.",
                            },
                            "parameters": {
                                "type": "object",
                                "description": "Optional agent-specific execution parameters (e.g. model override).",
                            },
                            "credentials": {
                                "type": "object",
                                "description": "Optional runtime credentials (e.g. OPENAI_API_KEY, GIBD_API_KEY).",
                            },
                        },
                        "required": required_props,
                    },
                }
            )

        # Also expose the Autonomous Multi-Agent DAG Executor as an MCP tool
        tools.append(
            {
                "name": "gas_execute_workflow_dag",
                "description": (
                    "Validate, auto-heal CRS mismatches, and execute a multi-agent GAS 1.0.0 workflow "
                    "Directed Acyclic Graph (DAG) across multiple geospatial agents."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "steps": {
                            "type": "array",
                            "description": "Ordered or dependency-linked workflow steps.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "step_id": {"type": "string"},
                                    "agent_id": {"type": "string"},
                                    "instructions": {"type": "string"},
                                    "depends_on": {"type": "array", "items": {"type": "string"}},
                                    "input_datasets": {"type": "array"},
                                    "parameters": {"type": "object"},
                                },
                                "required": ["step_id", "agent_id", "instructions"],
                            },
                        },
                        "auto_heal_crs": {
                            "type": "boolean",
                            "default": True,
                            "description": "Automatically reproject geographic vector datasets to metric CRS when needed.",
                        },
                    },
                    "required": ["steps"],
                },
            }
        )

        return tools

    def list_resources(self) -> list[dict[str, Any]]:
        """List MCP resources exposing GetCapabilities and DescribeAgent JSON documents."""
        resources: list[dict[str, Any]] = [
            {
                "uri": "gas://capabilities",
                "name": "GAS 1.0.0 GetCapabilities Document",
                "description": "Service-level capabilities and published geospatial agents.",
                "mimeType": "application/json",
            }
        ]
        for agent_id in sorted(SERVICE_REGISTRY):
            doc = _load_capability_doc(agent_id)
            profile = doc.get("profile") if isinstance(doc.get("profile"), dict) else {}
            resources.append(
                {
                    "uri": f"gas://agents/{agent_id}/describe",
                    "name": f"DescribeAgent: {profile.get('name') or agent_id}",
                    "description": str(profile.get("description") or f"Capability schema for {agent_id}"),
                    "mimeType": "application/json",
                }
            )
        return resources

    def read_resource(self, uri: str) -> dict[str, Any]:
        """Read a GAS capability or agent description resource by URI."""
        if uri == "gas://capabilities":
            cap_path = CAPABILITIES_DIR / "capabilities.json"
            text = cap_path.read_text(encoding="utf-8") if cap_path.exists() else "{}"
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": text,
                    }
                ]
            }

        if uri.startswith("gas://agents/") and uri.endswith("/describe"):
            agent_id = uri.removeprefix("gas://agents/").removesuffix("/describe").strip("/")
            if agent_id not in SERVICE_REGISTRY:
                raise KeyError(f"Unknown GAS agent resource '{uri}'.")
            doc = _load_capability_doc(agent_id)
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(doc, indent=2),
                    }
                ]
            }

        raise KeyError(f"Unsupported MCP resource URI '{uri}'.")

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute an MCP tool call by dispatching to the corresponding GAS 1.0.0 agent or DAG executor."""
        args = dict(arguments or {})

        if name == "gas_execute_workflow_dag":
            from gas_server.core.workflow_dag_executor import GasWorkflowDagExecutor

            executor = GasWorkflowDagExecutor(auto_heal_crs=bool(args.get("auto_heal_crs", True)))
            dag_result = executor.execute({"steps": args.get("steps") or []})
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(dag_result, indent=2),
                    }
                ],
                "isError": dag_result.get("status") != "successful",
            }

        agent_id = name.removeprefix("gas_") if name.startswith("gas_") else name
        if agent_id not in SERVICE_REGISTRY:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({"error": f"Unknown GAS tool '{name}'."}),
                    }
                ],
                "isError": True,
            }

        instructions = str(args.get("instructions") or "").strip()
        request_body: dict[str, Any] = {
            "task": {
                "instructions": instructions,
                "mode": "sync",
            },
            "outputs": {
                "artifact_delivery": str(args.get("artifact_delivery") or "URL"),
            },
        }
        if "input_datasets" in args and args["input_datasets"] is not None:
            request_body["inputs"] = {"input_datasets": args["input_datasets"]}
        if isinstance(args.get("parameters"), dict) and args["parameters"]:
            request_body["parameters"] = args["parameters"]
        if isinstance(args.get("credentials"), dict) and args["credentials"]:
            request_body["credentials"] = args["credentials"]

        clients = self._get_internal_clients()
        resp = clients[agent_id].post("/tasks", json=request_body)
        payload = resp.get_json(silent=True) or {"raw": resp.get_data(as_text=True)}
        is_err = resp.status_code >= 400 or payload.get("task", {}).get("status") in {"failed", "rejected"}

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(payload, indent=2),
                }
            ],
            "isError": bool(is_err),
        }

    def handle_jsonrpc_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Handle a single JSON-RPC 2.0 MCP request and return the JSON-RPC response."""
        if not isinstance(message, dict):
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Invalid Request"},
            }

        method = str(message.get("method") or "")
        msg_id = message.get("id")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}

        # Notifications have no id and require no response
        if "id" not in message and method.startswith("notifications/"):
            return None

        try:
            if method == "initialize":
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {
                            "tools": {"listChanged": False},
                            "resources": {"subscribe": False, "listChanged": False},
                        },
                        "serverInfo": {
                            "name": "gas-mcp-bridge",
                            "version": "1.0.0",
                        },
                    },
                }

            if method == "ping":
                return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

            if method == "tools/list":
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"tools": self.list_tools()},
                }

            if method == "tools/call":
                tool_name = str(params.get("name") or "")
                tool_args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": self.call_tool(tool_name, tool_args),
                }

            if method == "resources/list":
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"resources": self.list_resources()},
                }

            if method == "resources/read":
                uri = str(params.get("uri") or "")
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": self.read_resource(uri),
                }

            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        except KeyError as kerr:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32602, "message": str(kerr)},
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32603, "message": str(exc)},
            }


def run_stdio_server() -> None:
    """Run the GAS <-> MCP bridge over stdin/stdout for Claude Desktop, Cursor, or Windsurf."""
    bridge = GasMcpBridge()
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
            continue

        resp = bridge.handle_jsonrpc_message(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    run_stdio_server()
