<img align="left" src="docs/assets/gas-logo.png" alt="Geospatial Agentic Services logo" width="125">

### GAS - Geospatial Agentic Services

<p>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
  <a href="pyproject.toml"><img src="https://img.shields.io/badge/Python-3.10%2B-blue.svg" alt="Python 3.10+"></a>
  <a href="https://pypi.org/project/gas-client/"><img src="https://img.shields.io/pypi/v/gas-client.svg" alt="GAS Client on PyPI"></a>
  <a href="http://geospatial-agentic-services.online/registry"><img src="https://img.shields.io/badge/GAS-Registry-256b7f.svg" alt="GAS Registry"></a>
  <a href="https://GIBD2015.github.io/geospatial-agentic-services/"><img src="https://img.shields.io/badge/GAS-Docs-0067b1.svg" alt="GAS Documentation"></a>
  <a href="https://www.researchgate.net/publication/404738967_Geospatial_Agentic_Services_A_Framework_for_Interoperable_Geospatial_Intelligence"><img src="https://img.shields.io/badge/GAS-Paper-green.svg" alt="GAS Paper"></a>
  <a href="https://giscience.psu.edu/"><img src="https://img.shields.io/badge/GIBD-Lab-lightgrey.svg" alt="GIBD Lab"></a>
</p>

<br clear="left">

Geospatial Agentic Services, or GAS, is a service-oriented framework for
publishing, discovering, invoking, and composing GIS agents as interoperable
geospatial services.

GAS extends traditional geospatial interoperability beyond access to data and
predefined operations toward geospatial intelligence interoperability, where
GIS agents function as discoverable, reusable, and collaborative service
entities that encapsulate spatial knowledge and reasoning.

GAS does not prescribe how geospatial agents should be designed, how they
should reason, how their performance should be improved, or how general
agentic systems should be built. Instead, it focuses on the shared service
contracts needed when different geospatial agents, applications, notebooks, and
AI orchestrators need to work together.

This repository provides a reference GAS server, a Python client SDK, a GAS
Registry web app, example notebooks, interface schemas, developer
documentation, and working reference agent implementations.

- Documentation: [GAS HTML docs](https://GIBD2015.github.io/geospatial-agentic-services/)
- Public registry: [GAS Registry](http://geospatial-agentic-services.online/registry)
- Paper: [Geospatial Agentic Services: A Framework for Interoperable Geospatial Intelligence](https://www.researchgate.net/publication/404738967_Geospatial_Agentic_Services_A_Framework_for_Interoperable_Geospatial_Intelligence)

## Getting Started

### Use GAS Services

Start here if you want to discover and call existing GAS services from
notebooks, applications, GIS workflows, or AI orchestrators.

- [Use GAS Services](docs/gas_client_sdk.md)
- [GAS Interfaces](docs/gas_interfaces.md)
- [GAS Registry](docs/gas_registry.md)
- [Included Agents](docs/included_agents.md)
- [Notebook Examples](docs/examples.md)

Install the published client SDK:

```powershell
python -m pip install gas-client
```

Call a GAS service:

```python
from gas_client import GasClient

client = GasClient("https://your-gas-server.com")
print(client.list_agents())
```

### Add an Agent Service

Start here if you want to publish a new geospatial capability into the GAS
ecosystem.

- [Add an Agent Service](docs/adding_an_agent_service.md)
- [Server Architecture](docs/gas_server_architecture.md)
- [GAS Interfaces](docs/gas_interfaces.md)
- [Included Agents](docs/included_agents.md)

In the common case, a new agent service adds three files:

```text
gas_server/agents/my_new_agent.py
gas_server/services/my_new_agent_service.py
gas_server/capabilities/my_new_agent.json
```

### Host a GAS Server

Start here if you want to operate a public or private GAS server.

- [Host a GAS Server](docs/development_and_deployment_environment.md)
- [Server Architecture](docs/gas_server_architecture.md)
- [GAS Registry](docs/gas_registry.md)
- [Security](SECURITY.md)

Run the local GAS server:

```powershell
python -m gas_server.entrypoints.gas_server
```

In local development, the GAS server uses port `4042` by default:

```text
http://127.0.0.1:4042
```

### Improve the Codebase

Start here if you want to contribute to the GAS server framework, registry,
client SDK, schemas, examples, tests, or documentation.

- [Contributing](CONTRIBUTING.md)
- [Server Architecture](docs/gas_server_architecture.md)
- [Use GAS Services](docs/gas_client_sdk.md)
- [GAS Registry](docs/gas_registry.md)

## Core Documentation

- [GAS Interfaces](docs/gas_interfaces.md) explains the discovery,
  description, task request, task response, and artifact metadata contracts.
- [Server Architecture](docs/gas_server_architecture.md) explains the server
  framework, plugin-style service structure, request flow, credentials, and
  artifacts.
- [GAS Registry](docs/gas_registry.md) explains the registry web app and API.
- [Included Agents](docs/included_agents.md) catalogs the reference agents and
  the implementation patterns they demonstrate.

## Examples

Example notebooks demonstrate raw HTTP requests, client SDK usage, streamed
execution, distributed service chains, and multi-agent workflows.

- [Notebook Examples](docs/examples.md)
- [Example notebook folder](examples_for_using_gas_services)
- [Raw HTTP usage](examples_for_using_gas_services/gas_raw_requests_usage.ipynb)
- [Streaming examples](examples_for_using_gas_services/agents_streaming_examples.ipynb)
- [Workflow planning demo](examples_for_using_gas_services/geospatial_workflow_planning_agent_demo.ipynb)

## Quick Commands

Run the GAS server:

```powershell
python -m gas_server.entrypoints.gas_server
```

Run the GAS Registry locally:

```powershell
python -m gas_registry.app
```

Run tests:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Build the HTML documentation:

```powershell
python -m pip install -e .[docs]
python -m mkdocs build --strict
```

Preview the documentation locally:

```powershell
python -m mkdocs serve
```

## Project

- [Contributing](CONTRIBUTING.md)
- [Security](SECURITY.md)
- [License](LICENSE)

Use `.env.example` as a safe template for local environment variables. Do not
commit real API keys, downloaded datasets, generated outputs, build artifacts,
or notebook execution outputs.

We thank the coauthors of the GAS paper and welcome contributions from the
broader geospatial community to advance GAS-compatible agent services,
software, documentation, validation methods, and interoperability models.

[Geoinformation and Big Data Research Laboratory
(GIBD)](https://giscience.psu.edu/), Department of Geography, Penn State.
