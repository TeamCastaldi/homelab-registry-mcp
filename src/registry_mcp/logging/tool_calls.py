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
    from mcp.server.fastmcp import Context, FastMCP

_logger = get_logger("registry.tool_calls")


def install_tool_call_logging(server: FastMCP) -> None:
    """Wrap the tool manager's call_tool to log session/tool/outcome on every invocation.

    Deliberately patches `server._tool_manager.call_tool`, not
    `FastMCP.call_tool` itself: FastMCP.__init__ calls `_setup_handlers()`,
    which registers `self.call_tool` with the low-level Server by value
    (`self._mcp_server.call_tool(...)( self.call_tool)`) before any code
    calling this function can run. Reassigning `server.call_tool` afterward
    only shadows the instance attribute — the low-level Server already holds
    a direct reference to the original bound method and never looks up the
    new one, so wrapping it there is silently never invoked by real traffic.
    `FastMCP.call_tool()`'s body, by contrast, does a fresh attribute lookup
    on `self._tool_manager` (a stable instance, never reassigned) on every
    call, so patching the tool manager's method here reaches every real
    invocation regardless of when this function runs relative to __init__.

    Session identity comes from the ServerSession object's identity, not the
    streamable-http `mcp-session-id` header: the header only exists in the
    per-request HTTP task, while the tool call executes in the session's own
    persistent task (spawned once at session creation, upstream of any
    individual request) — a contextvar bound around the HTTP handler would
    never reach it. ServerSession is stable for the life of a session on
    every transport, including stdio (no HTTP header at all), so keying off
    it here works uniformly.
    """
    session_ids: WeakKeyDictionary[Any, str] = WeakKeyDictionary()
    tool_manager = server._tool_manager  # noqa: SLF001
    original = tool_manager.call_tool

    def _session_id(context: Context | None) -> str | None:
        if context is None:
            return None
        try:
            session = context.session
        except ValueError:
            # No live request context - e.g. call_tool() invoked directly in tests.
            return None
        return session_ids.setdefault(session, uuid4().hex)

    async def _call_tool_with_logging(
        name: str,
        arguments: dict[str, Any],
        context: Context | None = None,
        convert_result: bool = False,
    ) -> Any:
        session_id = _session_id(context)
        try:
            result = await original(name, arguments, context=context, convert_result=convert_result)
        except Exception:
            _logger.info("tool_call", tool_name=name, session_id=session_id, success=False)
            raise
        _logger.info("tool_call", tool_name=name, session_id=session_id, success=True)
        return result

    tool_manager.call_tool = _call_tool_with_logging  # type: ignore[method-assign]
