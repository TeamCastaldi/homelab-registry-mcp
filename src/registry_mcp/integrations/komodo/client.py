"""Async HTTP client for the Komodo API, with timeout, retry, and authentication."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx


class KomodoError(RuntimeError):
    """Raised when the Komodo API cannot be reached or returns an error."""


class KomodoClient:
    """Read-only client for the Komodo Core RPC API."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_secret: str,
        *,
        timeout: float = 10.0,
        retries: int = 3,
        backoff: float = 0.5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._api_key = api_key
        self._api_secret = api_secret
        self._timeout = timeout
        self._retries = max(1, retries)
        self._backoff = backoff
        self._transport = transport

    async def _request(self, path: str, payload: dict) -> Any:
        url = f"{self._base}/{path.lstrip('/')}"
        last_exc: Exception | None = None
        for attempt in range(self._retries):
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout,
                    transport=self._transport,
                ) as client:
                    headers = {
                        "X-Api-Key": self._api_key,
                        "X-Api-Secret": self._api_secret,
                    }
                    response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                # Client errors (4xx) are not transient; fail fast.
                if exc.response.status_code < 500:
                    raise KomodoError(
                        f"Komodo API returned {exc.response.status_code} for {path}"
                    ) from exc
                last_exc = exc
            except httpx.HTTPError as exc:
                last_exc = exc
            if attempt < self._retries - 1:
                await asyncio.sleep(self._backoff * (2**attempt))
        raise KomodoError(f"Komodo API request to {path} failed: {last_exc}") from last_exc

    async def read(self, query: str, params: dict | None = None) -> Any:
        """Execute a read query (e.g. `ListStacks`)."""
        payload = {"type": query, "params": params or {}}
        return await self._request("read", payload)

    async def execute(self, command: str, params: dict | None = None) -> Any:
        """Execute a write command (e.g. `DeployStack`).

        Unused today — this integration is read-only by convention (see CLAUDE.md's
        "Upstream APIs are read-only" rule). Kept for a future opt-in write path.
        """
        payload = {"type": command, "params": params or {}}
        return await self._request("execute", payload)

    async def list_stacks(self) -> list[dict[str, Any]]:
        """List all stacks."""
        result = await self.read("ListStacks")
        return result if isinstance(result, list) else []

    async def get_stack(self, name: str) -> dict[str, Any]:
        """Get a single stack by name."""
        return await self.read("GetStack", {"name": name})

    async def list_services(self) -> list[dict[str, Any]]:
        """List all services (containers)."""
        result = await self.read("ListServices")
        return result if isinstance(result, list) else []

    async def get_service(self, service_id: str) -> dict[str, Any]:
        """Get a single service by ID."""
        return await self.read("GetService", {"serviceId": service_id})

    async def list_updates(self) -> list[dict[str, Any]]:
        """List detected image updates for managed services."""
        result = await self.read("ListUpdates")
        return result if isinstance(result, list) else []

    async def get_logs(self, service_id: str, lines: int = 100) -> str:
        """Get recent logs for a service."""
        result = await self.read("GetLogs", {"serviceId": service_id, "lines": lines})
        return result if isinstance(result, str) else ""

    async def health_check(self) -> dict[str, Any]:
        """Check Komodo Core health."""
        try:
            return await self.read("Health")
        except KomodoError as exc:
            return {"status": "unhealthy", "error": str(exc)}
