"""Dozzle integration: MCP client, output parsers, and read-only log tools."""

from registry_mcp.integrations.dozzle.client import DozzleClient, DozzleError
from registry_mcp.integrations.dozzle.tools import register_dozzle_tools

__all__ = ["DozzleClient", "DozzleError", "register_dozzle_tools"]
