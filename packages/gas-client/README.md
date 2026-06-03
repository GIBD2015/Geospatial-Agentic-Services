# GAS Client

Python SDK for Geospatial Agentic Services (GAS).

This package contains only the lightweight client layer. It does not install the
GAS server, Flask, GeoPandas, Rasterio, PySAL, or other geospatial runtime
dependencies.

## Install

Install from PyPI:

```powershell
python -m pip install gas-client
```

For local development from this repository:

```powershell
cd packages/gas-client
python -m pip install -e .
```

## Quick Start

```python
from gas_client import GasClient

client = GasClient("https://your-gas-server.com")

print(client.list_agents())

agent = client.agent("geospatial_data_retrieval_agent")

result = agent.execute_task(
    "Download Pennsylvania county boundaries from Census Bureau.",
    mode="sync",
    credentials={"OPENAI_API_KEY": "YOUR_OPENAI_API_KEY"},
)

client.print_task_summary(result)
```

Use `print_artifacts()` for a lightweight artifact list, and use
`display_artifacts()` in notebooks to preview common outputs from any GAS
agent. The display helper shows PNG/JPEG/GIF images, HTML artifacts in a
light-mode iframe, CSV and JSON previews, GeoJSON/vector maps plus attribute
tables when possible, and GeoTIFF/GeoPackage artifacts when optional geospatial
display libraries such as `rasterio`, `matplotlib`, or `geopandas` are already
installed. Otherwise it falls back to clean artifact links.

```python
client.print_artifacts(result)
client.display_artifacts(result)

csv_artifacts = client.get_artifacts(result, format="csv")
csv_urls = client.get_artifact_urls(result, format="csv")
client.print_artifacts(result, format="csv")
client.display_artifacts(artifacts=csv_artifacts)
```

Some agents return several artifacts from one call. For example,
`geospatial_data_retrieval_agent` can decompose a multi-dataset request into
sub-tasks and return all dataset URLs via `client.get_artifact_urls(result)`.
Artifact `role` values are stable selectors for client code: simple outputs
may use generic roles such as `output` or `dataset`, while multi-artifact
workflows use semantic roles such as `ndvi_map_html_file` or
`validated_plan_json_file`.

Client-level credentials are optional defaults. You can omit them at client
creation and pass credentials per task, or provide `default_credentials` with
the provider-specific keys expected by your server, such as `GEMINI_API_KEY`.
Before choosing a credential field name, users and orchestrating agents should
inspect the selected agent's `DescribeAgent` JSON and use the key name that
agent advertises.
Task-level `credentials` override client defaults when needed.

```python
client = GasClient(
    "https://your-gas-server.com",
    default_credentials={
        "GEMINI_API_KEY": "YOUR_GEMINI_API_KEY",
    },
)
```

## Streaming Tasks

```python
for event in agent.execute_task(
    "Download Pennsylvania county boundaries from Census Bureau.",
    mode="stream",
):
    client.print_stream_event(event)
    if event.get("event") == "task_result":
        result = event.get("payload")

client.print_task_summary(result)
```

For notebooks, the SDK also provides the same pattern as one helper:

```python
result = client.run_streaming_task(
    agent,
    "Download Pennsylvania county boundaries from Census Bureau.",
)

# Or, with an agent-bound client:
result = agent.run_streaming_task(
    "Download Pennsylvania county boundaries from Census Bureau.",
)
```

## Canonical GAS Request Body

Credential requirements are defined by each service's `DescribeAgent`
capability document. Inspect the selected agent before submitting a task: one
service may require an OpenAI key, another may use a different model provider,
another may require data-source credentials, and deterministic services may not
need an LLM key.

```python
request_body = client.build_execute_task_request(
    "Create a web mapping app.",
    mode="stream",
    input_datasets=[
        "https://example.com/counties.geojson",
    ],
    artifact_delivery="URL",
    # Optional: include credentials here only when this call needs a key
    # and the client was not created with suitable default credentials.
    # Credential names are server- and agent-dependent.
    credentials={
        "OPENAI_API_KEY": "YOUR_OPENAI_API_KEY",
    },
)

for event in client.agent("web_mapping_app_agent").execute_task_request(request_body):
    client.print_stream_event(event)
```

## Public API

```python
from gas_client import (
    GASClient,
    GasAgentClient,
    GasClient,
    GasClientError,
    GasTaskTimeoutError,
)
```

Important methods:

- `get_capabilities()`
- `list_agents()`
- `describe_agent(agent_id)`
- `agent(agent_id)`
- `execute_task(agent_id, instructions, mode="sync")`
- `execute_task_request(agent_id, request_body)`
- `run_streaming_task(agent_or_agent_id, instructions)`
- `get_task_status(agent_id, task_id)`
- `get_task_result(agent_id, task_id)`
- `wait_for_task(agent_id, task_id)`
- `cancel_task(agent_id, task_id)`
- `encode_dataset_file(path)`
- `get_artifact_urls(result)`
- `get_artifacts(result)`
- `print_artifacts(result)`
- `display_artifacts(result)`
- `print_stream_event(event)`
- `print_task_summary(result)`

For the full SDK guide, including task modes, artifact handling, encoded input
datasets, and service chaining patterns, see:

https://github.com/GIBD2015/geospatial-agentic-services/blob/main/docs/gas_client_sdk.md
