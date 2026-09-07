"""Tests for the documentation-mcp client and the get_service_documentation tool."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

import registry_mcp.integrations.docs.tools as docs_tools
from conftest import IsolatedSettings
from registry_mcp.integrations.docs import DocsMcpClient, DocsMcpError
from registry_mcp.server import build_server


class FakeSession:
    """Stands in for `mcp.ClientSession`: records call_tool arguments and
    returns a pre-built `CallToolResult`."""

    def __init__(self, result: CallToolResult) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> CallToolResult:
        self.calls.append((name, arguments))
        return self.result


def _factory(session: FakeSession):
    """Async context manager standing in for streamablehttp_client + ClientSession."""

    @asynccontextmanager
    async def factory() -> AsyncIterator[FakeSession]:
        yield session

    return factory


def _raising_factory(exc: Exception):
    @asynccontextmanager
    async def factory() -> AsyncIterator[FakeSession]:
        raise exc
        yield  # pragma: no cover - unreachable, required for asynccontextmanager

    return factory


def _text_result(text: str, *, is_error: bool = False) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], isError=is_error)


# --- client -----------------------------------------------------------------


async def test_client_returns_content_verbatim():
    session = FakeSession(_text_result("# Traefik v3 docs\n..."))
    client = DocsMcpClient("http://docs.test", "tok", session_factory=_factory(session))
    text = await client.get_homelab_docs("traefik", "3.0")
    assert text == "# Traefik v3 docs\n..."


async def test_client_omits_topic_when_none():
    session = FakeSession(_text_result("ok"))
    client = DocsMcpClient("http://docs.test", "tok", session_factory=_factory(session))
    await client.get_homelab_docs("traefik", "3.0")
    name, arguments = session.calls[0]
    assert name == "get_homelab_docs"
    assert arguments == {"service_name": "traefik", "version": "3.0"}
    assert "topic" not in arguments


async def test_client_includes_topic_when_given():
    session = FakeSession(_text_result("ok"))
    client = DocsMcpClient("http://docs.test", "tok", session_factory=_factory(session))
    await client.get_homelab_docs("traefik", "3.0", topic="middlewares")
    _, arguments = session.calls[0]
    assert arguments["topic"] == "middlewares"


async def test_client_raises_on_error_result():
    session = FakeSession(_text_result("boom", is_error=True))
    client = DocsMcpClient("http://docs.test", "tok", session_factory=_factory(session))
    with pytest.raises(DocsMcpError):
        await client.get_homelab_docs("traefik", "3.0")


async def test_client_raises_on_empty_content():
    session = FakeSession(CallToolResult(content=[], isError=False))
    client = DocsMcpClient("http://docs.test", "tok", session_factory=_factory(session))
    with pytest.raises(DocsMcpError):
        await client.get_homelab_docs("traefik", "3.0")


async def test_client_wraps_session_factory_failure():
    original = ConnectionRefusedError("connection refused")
    client = DocsMcpClient("http://docs.test", "tok", session_factory=_raising_factory(original))
    with pytest.raises(DocsMcpError) as excinfo:
        await client.get_homelab_docs("traefik", "3.0")
    assert excinfo.value.__cause__ is original


# --- tool ---------------------------------------------------------------


class FakeDocsMcpClient:
    """Stands in for `DocsMcpClient` at the tool layer — client internals
    (session handling, argument shaping) are covered by the tests above."""

    def __init__(self, text: str | None = None, error: DocsMcpError | None = None) -> None:
        self._text = text
        self._error = error

    async def get_homelab_docs(
        self, service_name: str, version: str, topic: str | None = None
    ) -> str:
        if self._error is not None:
            raise self._error
        assert self._text is not None
        return self._text


@pytest.fixture
def docs_server(tmp_path, monkeypatch):
    constructed: list[tuple[str, str]] = []

    def make_server(
        *,
        text: str | None = None,
        error: DocsMcpError | None = None,
        configured: bool = True,
    ):
        def factory(url, token, **kwargs):
            constructed.append((url, token))
            return FakeDocsMcpClient(text=text, error=error)

        monkeypatch.setattr(docs_tools, "DocsMcpClient", factory)

        settings_kwargs: dict[str, Any] = {"registry_db_path": str(tmp_path / "r.db")}
        if configured:
            settings_kwargs["docs_mcp_url"] = "http://docs.test"
            settings_kwargs["docs_mcp_token"] = "tok"
        return build_server(IsolatedSettings(**settings_kwargs))

    make_server.constructed = constructed
    return make_server


async def call(server, name, args):
    return (await server.call_tool(name, args))[1]


async def test_tool_successful_relay(docs_server):
    server = docs_server(text="# Traefik docs")
    result = await call(
        server, "get_service_documentation", {"service_name": "traefik", "version": "3.0"}
    )
    assert result == {"content": "# Traefik docs"}


async def test_tool_critical_prefix_is_error(docs_server):
    text = (
        "CRITICAL: The homelab documentation vector store or SearXNG container "
        "is unreachable. This is a local Docker network issue. Please check "
        "your compose stack."
    )
    server = docs_server(text=text)
    result = await call(
        server, "get_service_documentation", {"service_name": "traefik", "version": "3.0"}
    )
    assert result == {"error": text}


async def test_tool_missing_params_prefix_is_error(docs_server):
    text = (
        "ERROR: Missing required parameters. Both 'service_name' and 'version' "
        "must be explicitly provided."
    )
    server = docs_server(text=text)
    result = await call(
        server, "get_service_documentation", {"service_name": "traefik", "version": "3.0"}
    )
    assert result == {"error": text}


async def test_tool_not_found_prefix_is_error(docs_server):
    text = (
        "ERROR: Official documentation not found locally and SearXNG failed to "
        "locate an official source for traefik version 99.0. Verify the service "
        "name and version are correct."
    )
    server = docs_server(text=text)
    result = await call(
        server, "get_service_documentation", {"service_name": "traefik", "version": "99.0"}
    )
    assert result == {"error": text}


async def test_tool_unconfigured_returns_error(docs_server):
    server = docs_server(configured=False)
    result = await call(
        server, "get_service_documentation", {"service_name": "traefik", "version": "3.0"}
    )
    assert result == {"error": "DOCS_MCP_URL and DOCS_MCP_TOKEN must be configured"}
    assert docs_server.constructed == []


async def test_tool_relays_client_error(docs_server):
    server = docs_server(error=DocsMcpError("documentation-mcp request failed: timeout"))
    result = await call(
        server, "get_service_documentation", {"service_name": "traefik", "version": "3.0"}
    )
    assert result == {"error": "documentation-mcp request failed: timeout"}
