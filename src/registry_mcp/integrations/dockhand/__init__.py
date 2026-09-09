"""Dockhand integration: read-only API client and MCP tools.

Read-only by convention (see CLAUDE.md's "Upstream APIs are read-only" rule
and ADR-013) — no write/update-triggering Dockhand endpoint is ever exposed
as a tool, even though the API has one (`POST /api/containers/check-updates`,
`POST /api/stacks`).
"""

from registry_mcp.integrations.dockhand.client import DockhandClient, DockhandError
from registry_mcp.integrations.dockhand.tools import register_dockhand_tools

__all__ = ["DockhandClient", "DockhandError", "register_dockhand_tools"]
