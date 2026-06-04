"""Async HTTP client for the Traefik API, with timeout and retry."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

import httpx

Protocol = Literal["http", "tcp", "udp"]


class TraefikError(RuntimeError):
    """Raised when the Traefik API cannot be reached or returns an error."""


class TraefikClient:
    """Read-only client for the Traefik API (the `/api` surface)."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        retries: int = 3,
        backoff: float = 0.5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._retries = max(1, retries)
        self._backoff = backoff
        self._transport = transport

    async def _get(self, path: str) -> Any:
        url = f"{self._base}/api/{path.lstrip('/')}"
        last_exc: Exception | None = None
        for attempt in range(self._retries):
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout,
                    transport=self._transport,
                ) as client:
                    response = await client.get(url)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                # Client errors (4xx) are not transient; fail fast.
                if exc.response.status_code < 500:
                    raise TraefikError(
                        f"Traefik API returned {exc.response.status_code} for {path}"
                    ) from exc
                last_exc = exc
            except httpx.HTTPError as exc:
                last_exc = exc
            if attempt < self._retries - 1:
                await asyncio.sleep(self._backoff * (2**attempt))
        raise TraefikError(f"Traefik API request to {path} failed: {last_exc}") from last_exc

    async def overview(self) -> dict[str, Any]:
        return await self._get("overview")

    async def entrypoints(self) -> list[dict[str, Any]]:
        return await self._get("entrypoints")

    async def list_routers(self, protocol: Protocol = "http") -> list[dict[str, Any]]:
        return await self._get(f"{protocol}/routers")

    async def get_router(self, name: str, protocol: Protocol = "http") -> dict[str, Any]:
        return await self._get(f"{protocol}/routers/{name}")

    async def list_services(self, protocol: Protocol = "http") -> list[dict[str, Any]]:
        return await self._get(f"{protocol}/services")

    async def list_middlewares(self, protocol: Protocol = "http") -> list[dict[str, Any]]:
        return await self._get(f"{protocol}/middlewares")

    async def rawdata(self) -> dict[str, Any]:
        return await self._get("rawdata")
