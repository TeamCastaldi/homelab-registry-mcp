"""Inbound HTTP webhook receivers.

Each module here turns an external system's notification into a *staged
proposal* — a pull request a human reviews — never a direct change. The
registrar is called once from `build_server()`, alongside the tool registrars
and `register_chat_routes`.

Unlike `chat/`, modules here take `RegistryStore`/`ProposalEngine` references
directly. That rule exists in `chat/` to keep an LLM-facing surface from
growing its own object graph; a webhook is a control-plane trigger in the same
class as a tool registrar, and there is no MCP tool for "open an image-update
proposal" to route through.
"""

from registry_mcp.webhooks.dockhand import register_webhook_routes

__all__ = ["register_webhook_routes"]
