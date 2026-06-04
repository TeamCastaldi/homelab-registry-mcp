"""Traefik integration: API client and MCP tools."""

from registry_mcp.integrations.traefik.client import TraefikClient, TraefikError
from registry_mcp.integrations.traefik.tools import register_traefik_tools

__all__ = ["TraefikClient", "TraefikError", "register_traefik_tools"]
