"""MCP tool registration."""

from registry_mcp.tools.discovery import register_discovery_tools
from registry_mcp.tools.events import register_event_tools
from registry_mcp.tools.hardware import register_hardware_tools
from registry_mcp.tools.linking import register_linking_tools
from registry_mcp.tools.proposal import register_proposal_tools
from registry_mcp.tools.registry import register_registry_tools

__all__ = [
    "register_discovery_tools",
    "register_event_tools",
    "register_hardware_tools",
    "register_linking_tools",
    "register_proposal_tools",
    "register_registry_tools",
]
