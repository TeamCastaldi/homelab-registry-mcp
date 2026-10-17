"""Async HTTP client for the Dockhand API, with token auth, timeout, and retry.

Reads are the whole of it except for two methods, `container_action` and
`stack_action` (ADR-021): a closed allowlist of start/stop/restart and a stack
redeploy. Every other Dockhand write endpoint (deletes, `down`, `exec`,
`/api/batch`, `check-updates`, `POST /api/stacks`, ...) has no method here, not
even a dormant one (ADR-013). A write is sent once and never retried.

Endpoint paths beyond `GET /api/containers/check-updates` (confirmed from
Dockhand's own changelog) are REST-conventional best guesses, unverified
against a live instance's `/api/docs` (Scalar viewer, opt-in via Dockhand's
own `FEAT_API_DOCS`). Response envelopes are parsed defensively for the same
reason — the real shape (bare array vs. a `results`/`data` wrapper) isn't
confirmed.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

# A container id or a stack name goes straight into a URL path, so it is held to
# a conservative allowlist: no `/`, `..`, `?`, `#`, whitespace, or `%`.
_PATH_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_ENVIRONMENT_ID = re.compile(r"^[0-9]{1,9}$")

CONTAINER_ACTIONS = frozenset({"start", "stop", "restart"})
STACK_ACTIONS = frozenset({"start", "stop", "restart", "redeploy"})


class DockhandError(RuntimeError):
    """Raised when the Dockhand API cannot be reached or returns an error."""


class DockhandClient:
    """Client for the Dockhand API (the `/api` surface): reads, plus the gated
    container and stack actions of ADR-021."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 10.0,
        retries: int = 3,
        backoff: float = 0.5,
        action_timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._retries = max(1, retries)
        self._backoff = backoff
        self._action_timeout = action_timeout
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

    # --- gated actions (ADR-021) -----------------------------------------

    async def container_action(
        self, environment_id: str, container_id: str, action: str
    ) -> dict[str, Any]:
        """Start, stop, or restart one container."""
        if action not in CONTAINER_ACTIONS:
            raise DockhandError(f"unsupported container action {action!r}")
        check_environment(environment_id)
        check_path_id(container_id, "container id")
        return await self._post(f"containers/{container_id}/{action}", environment_id)

    async def stack_action(
        self, environment_id: str, stack_name: str, action: str, *, pull: bool = False
    ) -> dict[str, Any]:
        """Start, stop, or restart a stack, or redeploy it with every container
        recreated (`redeploy`; `pull` also pulls the images first)."""
        if action not in STACK_ACTIONS:
            raise DockhandError(f"unsupported stack action {action!r}")
        check_environment(environment_id)
        check_path_id(stack_name, "stack name")
        if action == "redeploy":
            body = {"pull": bool(pull), "build": False, "forceRecreate": True}
            return await self._post(f"stacks/{stack_name}/deploy", environment_id, body)
        return await self._post(f"stacks/{stack_name}/{action}", environment_id)

    async def _post(
        self, path: str, environment_id: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """One attempt, never retried: a request that reached Dockhand and then
        failed may still have run, so repeating it could act twice. Redirects
        are not followed, so the token only ever goes to the configured host."""
        url = f"{self._base}/api/{path}"
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(
                timeout=self._action_timeout, transport=self._transport
            ) as client:
                response = await client.post(
                    url, params={"env": environment_id}, headers=headers, json=body
                )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise DockhandError(
                f"Dockhand API returned {exc.response.status_code} for {path}"
            ) from exc
        except httpx.HTTPError as exc:
            raise DockhandError(
                f"Dockhand API request to {path} failed ({type(exc).__name__}); "
                "the action may or may not have been applied"
            ) from exc
        try:
            parsed: Any = response.json()
        except ValueError:
            parsed = None
        if isinstance(parsed, dict) and parsed.get("success") is False:
            reason = " ".join(str(parsed.get("error") or parsed.get("message") or "").split())
            raise DockhandError(
                f"Dockhand reported failure for {path}: {reason[:200] or 'no reason'}"
            )
        return {"status_code": response.status_code, "response": parsed}


def check_environment(environment_id: str) -> None:
    """Raise `DockhandError` unless this is a Dockhand environment id (digits)."""
    if not isinstance(environment_id, str) or not _ENVIRONMENT_ID.fullmatch(environment_id):
        raise DockhandError("environment_id must be a Dockhand environment id (digits only)")


def check_path_id(value: str, what: str) -> None:
    """Raise `DockhandError` unless `value` is safe to put in a URL path."""
    if not isinstance(value, str) or not _PATH_ID.fullmatch(value):
        raise DockhandError(
            f"invalid {what}: letters, digits, '_', '.', '-' only, starting with a letter or digit"
        )
