"""Dozzle MCP client and the dozzle_* tools."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

import registry_mcp.integrations.dozzle.tools as dozzle_tools
from conftest import IsolatedSettings, tool_payload
from registry_mcp.integrations.dozzle import DozzleClient, DozzleError
from registry_mcp.server import build_server


class FakeSession:
    def __init__(self, result: CallToolResult | None = None, tools: list | None = None) -> None:
        self.result = result
        self.tools = tools or []
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def call_tool(self, name, arguments=None):
        self.calls.append((name, arguments))
        return self.result

    async def list_tools(self):
        return SimpleNamespace(tools=self.tools)


def _factory(session):
    @asynccontextmanager
    async def factory():
        yield session

    return factory


def _raising_factory(exc):
    @asynccontextmanager
    async def factory():
        raise exc
        yield  # pragma: no cover

    return factory


def _text(text, *, is_error=False):
    return CallToolResult(content=[TextContent(type="text", text=text)], isError=is_error)


# --- client -----------------------------------------------------------------


async def test_client_returns_text_and_passes_arguments():
    session = FakeSession(_text("plain text"))
    client = DozzleClient("http://d", session_factory=_factory(session))
    assert await client.call_tool("list_containers", {"state": "running"}) == "plain text"
    assert session.calls == [("list_containers", {"state": "running"})]


async def test_client_refuses_a_tool_outside_the_allowlist():
    session = FakeSession(_text("x"))
    client = DozzleClient("http://d", session_factory=_factory(session))
    with pytest.raises(DozzleError, match="not an allowed"):
        await client.call_tool("restart_container", {"id": "abc"})
    assert session.calls == []


async def test_client_raises_on_tool_error_and_empty_result():
    client = DozzleClient(
        "http://d", session_factory=_factory(FakeSession(_text("boom", is_error=True)))
    )
    with pytest.raises(DozzleError, match="boom"):
        await client.call_tool("list_hosts")
    empty = CallToolResult(content=[], isError=False)
    client = DozzleClient("http://d", session_factory=_factory(FakeSession(empty)))
    with pytest.raises(DozzleError, match="no content"):
        await client.call_tool("list_hosts")


async def test_client_wraps_transport_failure():
    client = DozzleClient("http://d", session_factory=_raising_factory(OSError("refused")))
    with pytest.raises(DozzleError, match="refused"):
        await client.call_tool("list_hosts")
    with pytest.raises(DozzleError, match="refused"):
        await client.list_tools()


async def test_client_lists_live_tool_schemas():
    tool = SimpleNamespace(name="get_container_logs", description="d", inputSchema={"a": 1})
    client = DozzleClient("http://d", session_factory=_factory(FakeSession(tools=[tool])))
    assert await client.list_tools() == [
        {"name": "get_container_logs", "description": "d", "input_schema": {"a": 1}}
    ]


# --- tools ------------------------------------------------------------------


@pytest.fixture
def wired(tmp_path, monkeypatch):
    session = FakeSession(_text('[{"name": "traefik"}]'))
    real = dozzle_tools.DozzleClient
    monkeypatch.setattr(
        dozzle_tools,
        "DozzleClient",
        lambda url, token=None, **kw: real(url, token, session_factory=_factory(session), **kw),
    )
    server = build_server(
        IsolatedSettings(
            registry_db_path=str(tmp_path / "r.db"),
            dozzle_mcp_url="http://dozzle.test/api/mcp",
            dozzle_max_response_chars=200,
        )
    )
    return server, session


async def test_tools_are_absent_when_unconfigured(tmp_path):
    server = build_server(IsolatedSettings(registry_db_path=str(tmp_path / "r.db")))
    assert not {t.name for t in await server.list_tools()} & {
        "dozzle_list_tools",
        "dozzle_call_tool",
    }


async def test_list_containers_parses_json_and_sends_state(wired):
    server, session = wired
    payload = tool_payload(await server.call_tool("dozzle_list_containers", {"state": "exited"}))
    assert payload == {"result": [{"name": "traefik"}]}
    assert session.calls == [("list_containers", {"state": "exited"})]


async def test_unallowed_tool_is_an_error_and_never_called(wired):
    server, session = wired
    payload = tool_payload(
        await server.call_tool("dozzle_call_tool", {"tool": "exec_shell", "arguments": {}})
    )
    assert "not an allowed" in payload["error"]
    assert session.calls == []


async def test_credentials_in_logs_are_scrubbed(wired):
    server, session = wired
    session.result = _text(
        "line one\nDB_PASSWORD=hunter2hunter2hunter2xyz\nAuthorization: Bearer abcdefghijklmnop1234"
    )
    payload = tool_payload(
        await server.call_tool("dozzle_call_tool", {"tool": "get_container_logs", "arguments": {}})
    )
    assert "hunter2" not in payload["result"]
    assert "abcdefghijklmnop1234" not in payload["result"]
    assert "line one" in payload["result"]


async def test_large_output_keeps_the_tail_and_says_so(wired):
    server, session = wired
    session.result = _text("\n".join(f"line {i}" for i in range(500)))
    payload = tool_payload(
        await server.call_tool("dozzle_call_tool", {"tool": "get_container_logs", "arguments": {}})
    )
    assert payload["truncated"] is True
    assert len(payload["result"]) == 200
    assert payload["result"].endswith("line 499")


async def test_upstream_failure_is_a_reported_error(wired):
    server, session = wired
    session.result = _text("no such container", is_error=True)
    payload = tool_payload(await server.call_tool("dozzle_list_hosts", {}))
    assert "no such container" in payload["error"]
