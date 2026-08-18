"""Tests for the tool-call logging wrapper installed on every FastMCP server."""

import pytest
import structlog
from mcp.server.fastmcp.exceptions import ToolError
from starlette.testclient import TestClient

_HEADERS = {"Accept": "application/json, text/event-stream", "Host": "localhost:80"}


def _initialize(client: TestClient) -> str:
    resp = client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        },
        headers=_HEADERS,
    )
    return resp.headers["mcp-session-id"]


def _call_tool(client: TestClient, session_id: str, req_id: int, name: str) -> None:
    client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": {}},
        },
        headers={**_HEADERS, "mcp-session-id": session_id},
    )


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


def test_real_client_session_populates_and_correlates_session_id(server):
    """Regression test: server.call_tool() alone can't catch this class of bug.

    FastMCP.__init__ registers `self.call_tool` with the low-level Server by
    value before any wrapping code runs, so a naive `server.call_tool = ...`
    patch is silently never invoked by real dispatched traffic - only by
    someone calling `server.call_tool(...)` directly, which every other test
    here does. Driving actual tools/call requests through a real client
    session is the only way to catch that: this failed with zero tool_call
    log lines at all (not even with a null session_id) before the fix.
    """
    app = server.streamable_http_app()

    with structlog.testing.capture_logs() as logs, TestClient(app) as client:
        session_a = _initialize(client)
        _call_tool(client, session_a, 2, "health")
        _call_tool(client, session_a, 3, "health")

        session_b = _initialize(client)
        _call_tool(client, session_b, 2, "health")

    tool_calls = [entry for entry in logs if entry["event"] == "tool_call"]
    assert len(tool_calls) == 3

    session_ids = [entry["session_id"] for entry in tool_calls]
    assert all(session_ids)
    assert session_ids[0] == session_ids[1]
    assert session_ids[2] != session_ids[0]
