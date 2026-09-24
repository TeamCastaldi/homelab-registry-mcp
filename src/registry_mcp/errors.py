"""The `{"error": ...}` convention tools and resources use to report failure.

Tool and resource functions return a dict with a non-empty top-level `error`
(context keys alongside are fine) rather than raising. The MCP layer then
turns that into the protocol's own signal: `isError: true` for a tool call
(`logging/tool_calls.py`), a JSON-RPC error for a resource read (below).
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp.exceptions import ResourceError


def reports_error(result: Any) -> bool:
    """True for a dict with a non-empty top-level `error`. A null or empty
    `error` field (a successful DiscoveryEvent record), a nested one, or a
    list is not a reported failure."""
    return isinstance(result, dict) and bool(result.get("error"))


def resource_or_raise(result: Any) -> Any:
    """Return `result`, or raise ResourceError for a reported failure.

    A resource read has no isError flag: returned as-is, `{"error": ...}`
    would reach the client as the resource's content, indistinguishable from
    a successful read. Raising makes it a JSON-RPC error response instead.
    """
    if reports_error(result):
        raise ResourceError(str(result["error"]))
    return result
