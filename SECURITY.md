# Security Policy

This repository is a research/reference implementation for Geospatial Agentic
Services. Please report security issues privately to the project maintainers
instead of opening a public issue with exploit details.

## Supported Versions

The public repository is currently in early development. Security fixes are
applied to the active main development line.

## Secret Handling

API keys and data-source credentials must be supplied at request time or through
local environment management. Do not place real credentials in source code,
capability documents, notebooks, handbooks, or generated artifacts.

If you believe a key has been exposed, revoke or rotate it immediately with the
credential provider.
