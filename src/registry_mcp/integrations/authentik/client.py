"""Async HTTP client for the Authentik API, with token auth, timeout, and retry."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx


class AuthentikError(RuntimeError):
    """Raised when the Authentik API cannot be reached or returns an error."""


class AuthentikClient:
    """Read-only client for the Authentik API (the `/api/v3` surface)."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 10.0,
        retries: int = 3,
        backoff: float = 0.5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._retries = max(1, retries)
        self._backoff = backoff
        self._transport = transport

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self._base}/{path.lstrip('/')}"
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        headers = {"Authorization": f"Bearer {self._token}"}
        last_exc: Exception | None = None
        for attempt in range(self._retries):
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout,
                    transport=self._transport,
                ) as client:
                    response = await client.get(url, params=clean, headers=headers)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    raise AuthentikError(
                        f"Authentik API returned {exc.response.status_code} for {path}"
                    ) from exc
                last_exc = exc
            except httpx.HTTPError as exc:
                last_exc = exc
            if attempt < self._retries - 1:
                await asyncio.sleep(self._backoff * (2**attempt))
        raise AuthentikError(f"Authentik API request to {path} failed: {last_exc}") from last_exc

    @staticmethod
    def _results(payload: Any) -> list[dict[str, Any]]:
        """Authentik list endpoints are paginated as {pagination, results}."""
        if isinstance(payload, dict) and "results" in payload:
            return payload["results"]
        return payload if isinstance(payload, list) else []

    async def list_applications(self) -> list[dict[str, Any]]:
        # superuser_full_list bypasses the per-user application visibility filter so
        # the service account sees all applications, not just ones it has access to.
        return self._results(await self._get("core/applications/", {"superuser_full_list": "true"}))

    async def get_application(self, slug: str) -> dict[str, Any]:
        return await self._get(f"core/applications/{slug}/")

    async def list_providers(self) -> list[dict[str, Any]]:
        return self._results(await self._get("providers/all/"))

    async def list_outposts(self) -> list[dict[str, Any]]:
        return self._results(await self._get("outposts/instances/"))

    async def get_outpost_health(self, outpost_pk: str) -> Any:
        return await self._get(f"outposts/instances/{outpost_pk}/health/")

    async def list_policies(self) -> list[dict[str, Any]]:
        return self._results(await self._get("policies/all/"))

    async def list_events(self, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        merged = {"ordering": "-created"}
        merged.update(params or {})
        return self._results(await self._get("events/events/", merged))

    async def list_users(self, search: str | None = None) -> list[dict[str, Any]]:
        return self._results(await self._get("core/users/", {"search": search}))

    async def list_groups(self, search: str | None = None) -> list[dict[str, Any]]:
        return self._results(await self._get("core/groups/", {"search": search}))
