# Contributing

This page summarizes where to start when improving the GAS codebase,
documentation, schemas, examples, registry, server framework, or client SDKs.

For the canonical repository contribution guide, see
[CONTRIBUTING.md](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/CONTRIBUTING.md).

## Start Here

1. Set up the local development environment.
2. Review the server, registry, client, and interface documentation.
3. Run the test suite.
4. Keep changes focused and aligned with the GAS service contracts.
5. Avoid committing secrets, generated outputs, downloaded datasets, or local
   build artifacts.

## Useful Documentation

- [Development and Deployment Environment](development_and_deployment_environment.md)
- [GAS Server Framework](gas_server_architecture.md)
- [GAS Interfaces](gas_interfaces.md)
- [GAS Client SDKs](gas_client_sdk.md)
- [GAS Registry](gas_registry.md)
- [Adding a GAS Agent Service](adding_an_agent_service.md)

## Test Command

```powershell
.\.venv\Scripts\python.exe -m pytest
```
