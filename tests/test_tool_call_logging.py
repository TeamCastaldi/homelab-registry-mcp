"""Tests for the tool-call logging wrapper installed on every FastMCP server."""

import pytest
import structlog
from mcp.server.fastmcp.exceptions import ToolError


async def test_successful_call_logs_tool_call_event(server):
    with structlog.testing.capture_logs() as logs:
        await server.call_tool("health", {})

    tool_calls = [entry for entry in logs if entry["event"] == "tool_call"]
    assert len(tool_calls) == 1
    assert tool_calls[0]["tool_name"] == "health"
    assert tool_calls[0]["success"] is True


async def test_failed_call_logs_failure_and_reraises(server):
    with structlog.testing.capture_logs() as logs, pytest.raises(ToolError):
        await server.call_tool("no-such-tool", {})

    tool_calls = [entry for entry in logs if entry["event"] == "tool_call"]
    assert len(tool_calls) == 1
    assert tool_calls[0]["tool_name"] == "no-such-tool"
    assert tool_calls[0]["success"] is False


async def test_logged_event_omits_arguments(server):
    with structlog.testing.capture_logs() as logs:
        await server.call_tool(
            "registry_add_service",
            {"name": "plex", "display_name": "Plex", "category": "media"},
        )

    tool_calls = [entry for entry in logs if entry["event"] == "tool_call"]
    assert len(tool_calls) == 1
    assert set(tool_calls[0]) <= {"event", "log_level", "tool_name", "session_id", "success"}
