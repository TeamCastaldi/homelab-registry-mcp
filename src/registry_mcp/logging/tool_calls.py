"""Per-invocation tool-call logging: session, tool name, timestamp, success/failure.

Deliberately omits arguments and results: `_redact` in `logging/events.py`
catches secret-shaped *key names*, but not e.g. `secrets_add`'s `value`
param, which carries a secret under an innocuous name. Omitting args/results
entirely sidesteps that gap rather than relying on an exhaustive redaction list.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4
from weakref import WeakKeyDictionary

from registry_mcp.logging.events import get_logger

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

_logger = get_logger("registry.tool_calls")


def install_tool_call_logging(server: FastMCP) -> None:
    """Wrap FastMCP.call_tool to log session/tool/outcome on every invocation.

    call_tool() is the single handler the low-level Server dispatches every
    `tools/call` request to, on every transport (wired up in
    FastMCP._setup_handlers), so wrapping it here needs no per-tool change.

    Session identity comes from the ServerSession object's identity, not the
    streamable-http `mcp-session-id` header: the header only exists in the
    per-request HTTP task, while call_tool executes in the session's own
    persistent task (spawned once at session creation, upstream of any
    individual request) — a contextvar bound around the HTTP handler would
    never reach it. ServerSession is stable for the life of a session on
    every transport, including stdio (no HTTP header at all), so keying off
    it here works uniformly.
    """
    session_ids: WeakKeyDictionary[Any, str] = WeakKeyDictionary()
    original = server.call_tool

    def _session_id() -> str | None:
        try:
            session = server.get_context().session
        except ValueError:
            # No live request context - e.g. call_tool() invoked directly in tests.
            return None
        return session_ids.setdefault(session, uuid4().hex)

    async def _call_tool_with_logging(name: str, arguments: dict[str, Any]) -> Any:
        session_id = _session_id()
        try:
            result = await original(name, arguments)
        except Exception:
            _logger.info("tool_call", tool_name=name, session_id=session_id, success=False)
            raise
        _logger.info("tool_call", tool_name=name, session_id=session_id, success=True)
        return result

    server.call_tool = _call_tool_with_logging  # type: ignore[method-assign]
