"""Async HTTP client for the Dockhand API, with token auth, timeout, and retry.

Read-only by convention (see CLAUDE.md's "Upstream APIs are read-only" rule and
ADR-013) — Dockhand's API also exposes update-triggering and stack-mutating
endpoints (`POST /api/containers/check-updates`, `POST /api/stacks`), but no
method here ever calls them, not even as a dormant/unused one.

Endpoint paths beyond `GET /api/containers/check-updates` (confirmed from
Dockhand's own changelog) are REST-conventional best guesses, unverified
against a live instance's `/api/docs` (Scalar viewer, opt-in via Dockhand's
own `FEAT_API_DOCS`). Response envelopes are parsed defensively for the same
reason — the real shape (bare array vs. a `results`/`data` wrapper) isn't
confirmed.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx


class DockhandError(RuntimeError):
    """Raised when the Dockhand API cannot be reached or returns an error."""


class DockhandClient:
    """Read-only client for the Dockhand API (the `/api` surface)."""

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
        url = f"{self._base}/api/{path.lstrip('/')}"
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
                # Client errors (4xx) are not transient; fail fast.
                if exc.response.status_code < 500:
                    raise DockhandError(
                        f"Dockhand API returned {exc.response.status_code} for {path}"
                    ) from exc
                last_exc = exc
            except httpx.HTTPError as exc:
                last_exc = exc
            if attempt < self._retries - 1:
                await asyncio.sleep(self._backoff * (2**attempt))
        raise DockhandError(f"Dockhand API request to {path} failed: {last_exc}") from last_exc

    @staticmethod
    def _list(payload: Any) -> list[dict[str, Any]]:
        """Defensive unwrap: the real list-endpoint envelope isn't confirmed."""
        if isinstance(payload, dict):
            for key in ("results", "data", "items"):
                if isinstance(payload.get(key), list):
                    return payload[key]
            return []
        return payload if isinstance(payload, list) else []

    async def list_environments(self) -> list[dict[str, Any]]:
        return self._list(await self._get("environments"))

    async def get_environment(self, environment_id: str) -> dict[str, Any]:
        return await self._get(f"environments/{environment_id}")

    async def list_stacks(self, environment_id: str | None = None) -> list[dict[str, Any]]:
        return self._list(await self._get("stacks", {"environment_id": environment_id}))

    async def get_stack(self, stack_id: str) -> dict[str, Any]:
        return await self._get(f"stacks/{stack_id}")

    async def list_containers(
        self, environment_id: str | None = None, stack_id: str | None = None
    ) -> list[dict[str, Any]]:
        return self._list(
            await self._get("containers", {"environment_id": environment_id, "stack_id": stack_id})
        )

    async def list_pending_updates(self) -> list[dict[str, Any]]:
        """Containers with an image update available, per Dockhand's last check.

        Reads `GET /api/containers/check-updates` — the confirmed-real read
        variant. Never calls its `POST` twin, which triggers a fresh check.
        """
        return self._list(await self._get("containers/check-updates"))

    async def list_vulnerabilities(self, container_id: str | None = None) -> list[dict[str, Any]]:
        return self._list(await self._get("vulnerabilities", {"container_id": container_id}))
