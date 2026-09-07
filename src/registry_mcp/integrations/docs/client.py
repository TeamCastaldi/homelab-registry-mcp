"""MCP client for documentation-mcp's single `get_homelab_docs` tool.

documentation-mcp is itself an MCP server (streamable-http only, bearer-token
auth), not a REST API — so this wraps `streamablehttp_client` + `ClientSession`
rather than `httpx`, one call per invocation with no retry loop (a single
on-demand tool call doesn't need the transient-5xx handling `TraefikClient`
uses for polling discovery passes).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

SessionFactory = Callable[[], AbstractAsyncContextManager[ClientSession]]


class DocsMcpError(RuntimeError):
    """Raised when documentation-mcp cannot be reached or returns a malformed result."""


class DocsMcpClient:
    """Client for documentation-mcp's `get_homelab_docs` tool."""

    def __init__(
        self,
        url: str,
        token: str,
        *,
        timeout: float = 30.0,
        session_factory: SessionFactory | None = None,
    ) -> None:
        self._url = url
        self._token = token
        self._timeout = timeout
        self._session_factory: SessionFactory = session_factory or self._default_session_factory

    @asynccontextmanager
    async def _default_session_factory(self) -> AsyncIterator[ClientSession]:
        headers = {"Authorization": f"Bearer {self._token}"}
        async with (
            streamablehttp_client(self._url, headers=headers, timeout=self._timeout) as (
                read_stream,
                write_stream,
                _get_session_id,
            ),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            yield session

    async def get_homelab_docs(
        self, service_name: str, version: str, topic: str | None = None
    ) -> str:
        """Call documentation-mcp's `get_homelab_docs` tool and return its raw text.

        Only distinguishes transport/protocol failure (raised as `DocsMcpError`)
        from a successful MCP call — documentation-mcp always returns a plain
        string, even for its own documented business-logic failures (missing
        params, no source found, backend unreachable). Classifying those literal
        ERROR:/CRITICAL: strings is the caller's job (see `tools.py`).
        """
        arguments: dict[str, Any] = {"service_name": service_name, "version": version}
        if topic is not None:
            arguments["topic"] = topic

        try:
            async with self._session_factory() as session:
                result = await session.call_tool("get_homelab_docs", arguments)
        except Exception as exc:
            raise DocsMcpError(f"documentation-mcp request failed: {exc}") from exc

        if result.isError or not result.content:
            raise DocsMcpError("documentation-mcp returned an error or empty result")

        text = getattr(result.content[0], "text", None)
        if text is None:
            raise DocsMcpError("documentation-mcp returned a non-text content item")
        return text
