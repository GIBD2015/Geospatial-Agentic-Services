# Security

For the canonical security policy, supported versions, secret handling, and
vulnerability reporting guidance, see
[SECURITY.md](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/SECURITY.md).

## Secret Handling

Use `.env.example` as a template for local configuration. Do not commit real
API keys, data-source credentials, generated outputs, downloaded datasets, or
notebook execution outputs.

Credential requirements are agent-specific and should be documented in each
agent's `DescribeAgent` capability document.

## Related Documentation

- [Development and Deployment Environment](development_and_deployment_environment.md)
- [GAS Server Framework](gas_server_architecture.md)
- [GAS Interfaces](gas_interfaces.md)
