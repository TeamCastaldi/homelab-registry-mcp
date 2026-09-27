"""Inbound HTTP webhook receivers.

Each module here turns an external system's notification into something a
human decides on — a pull request to review (Dockhand, ADR-010) or an emailed
approval to confirm (Patchmon, ADR-020) — never a direct change. The
registrars are called once from `build_server()`, alongside the tool registrars.

Modules here take store and engine references directly. A webhook is a
control-plane trigger in the same class as a tool registrar, and there is no
MCP tool for "open an image-update proposal" to route through.
"""

from registry_mcp.webhooks.dockhand import register_webhook_routes
from registry_mcp.webhooks.patchmon import register_patchmon_routes

__all__ = ["register_patchmon_routes", "register_webhook_routes"]
