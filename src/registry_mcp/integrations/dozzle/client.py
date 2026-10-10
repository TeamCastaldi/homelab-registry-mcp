"""MCP client for Dozzle's built-in MCP server (`/api/mcp`, streamable-http).

Dozzle is itself an MCP server, so like `DocsMcpClient` this wraps
`streamablehttp_client` + `ClientSession`, one session per call and no retry loop.
It is meant to be reached over `swarm-net` (`http://dozzle:8080/api/mcp`), where
the Authentik forward-auth that guards the public route never applies.

Dozzle's five tools are all read-only upstream. This client still refuses any tool
name outside `READ_ONLY_TOOLS`, so a tool a later Dozzle release adds (one that
acts on a container, say) can't be relayed by accident.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

SessionFactory = Callable[[], AbstractAsyncContextManager[ClientSession]]

READ_ONLY_TOOLS = frozenset(
    {
        "list_containers",
        "get_container_logs",
        "search_container_logs",
        "list_hosts",
        "get_container_stats",
    }
)


class DozzleError(RuntimeError):
    """Raised when Dozzle cannot be reached, reports an error, or answers in a shape
    this client doesn't recognize."""


class DozzleClient:
    """Read-only client for Dozzle's MCP tools."""

    def __init__(
        self,
        url: str,
        token: str | None = None,
        *,
        timeout: float = 15.0,
        session_factory: SessionFactory | None = None,
    ) -> None:
        self._url = url
        self._token = token
        self._timeout = timeout
        self._session_factory: SessionFactory = session_factory or self._default_session_factory

    @asynccontextmanager
    async def _default_session_factory(self) -> AsyncIterator[ClientSession]:
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else None
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

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        """Call one allowlisted tool and return its text. `isError` raises."""
        if name not in READ_ONLY_TOOLS:
            raise DozzleError(f"{name!r} is not an allowed Dozzle tool")
        try:
            async with self._session_factory() as session:
                result = await session.call_tool(name, arguments or {})
        except Exception as exc:
            raise DozzleError(f"Dozzle request failed: {exc}") from exc

        texts = [getattr(c, "text", None) for c in result.content]
        text = "\n".join(t for t in texts if t is not None)
        if result.isError:
            raise DozzleError(f"Dozzle tool {name} failed: {text or 'no detail'}")
        if not text:
            raise DozzleError(f"Dozzle tool {name} returned no content")
        return text
