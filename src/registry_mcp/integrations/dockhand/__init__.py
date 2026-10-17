"""Dockhand integration: API client and MCP tools.

Reads are the default (ADR-013). The one write surface is `actions.py`: gated,
off-by-default container and stack actions (ADR-021), from a closed allowlist.
Every other Dockhand write endpoint is still never exposed.
"""

from registry_mcp.integrations.dockhand.actions import register_dockhand_action_tools
from registry_mcp.integrations.dockhand.client import DockhandClient, DockhandError
from registry_mcp.integrations.dockhand.gate import (
    DockhandActionGateError,
    DockhandActionGateStore,
)
from registry_mcp.integrations.dockhand.tools import register_dockhand_tools

__all__ = [
    "DockhandActionGateError",
    "DockhandActionGateStore",
    "DockhandClient",
    "DockhandError",
    "register_dockhand_action_tools",
    "register_dockhand_tools",
]
