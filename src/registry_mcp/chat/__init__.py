"""Chat interface — opt-in browser UI backed by a local/LAN Ollama instance.

Every module in this package takes only `(mcp, settings)` — no direct
reference to `RegistryStore`/`HardwareStore`/any engine. All lab data the
assistant can see flows through `mcp.list_tools()`/`mcp.call_tool()`, the
same surface any other MCP client uses, filtered by the explicit allowlist in
`registry_mcp.chat.bridge`. This keeps the chat backend from adding a third
object graph alongside the two `server.py` already builds (see
`docs/ruthless-reviews/review-2026-06-30.md`), and guarantees chat and MCP
clients can never observe different state.
"""

from __future__ import annotations

from registry_mcp.chat.routes import register_chat_routes

__all__ = ["register_chat_routes"]
