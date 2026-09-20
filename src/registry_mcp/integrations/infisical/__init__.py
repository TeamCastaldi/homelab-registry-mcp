"""Infisical integration: read-only Universal Auth client and MCP tool (ADR-016).

Read-only by construction, not just convention -- `infisical_status` never
returns a secret's value, only which keys exist. See `client.py`'s module
docstring for the defensive value-leak gate this relies on.
"""

from registry_mcp.integrations.infisical.client import (
    InfisicalClient,
    InfisicalError,
    InfisicalSecretValueLeakedError,
)
from registry_mcp.integrations.infisical.tools import register_infisical_tools

__all__ = [
    "InfisicalClient",
    "InfisicalError",
    "InfisicalSecretValueLeakedError",
    "register_infisical_tools",
]
