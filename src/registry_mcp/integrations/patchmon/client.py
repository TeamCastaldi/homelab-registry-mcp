"""Async client for PatchMon's scoped Integration API: read-only, HTTP Basic.

PatchMon serves `/api/v1/api/hosts/...` to "API" integration credentials,
created under Settings -> Integrations: a Token Key (`patchmon_ae_...`) and a
Token Secret, sent as HTTP Basic. Every call here is a GET, which PatchMon's
router gates on the `host:get` scope. The one write endpoint on this surface,
`DELETE /api/v1/api/hosts/{id}` (`host:delete`), is never called, not even from
a dormant method, and the credential should never be granted that scope.

Response shapes were read from PatchMon's
`server-source-code/internal/handler/api_hosts.go` rather than its docs, which
disagree with the router in places (its Ansible chapter names a `host:read`
scope; the router checks `host:get`).

A host id is PatchMon's UUID and is checked before it reaches a URL path, so a
value that came in on a webhook can't walk the path or add a query string.
Redirects are never followed: the Basic credentials go only to the configured
origin.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

from registry_mcp.config import Settings, reveal

_HOST_ID_RE = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
# PATCHMON_API_URL is the instance root, but the dynamic-inventory plugin's own
# `api_url` is the full hosts endpoint; either is accepted.
_ENDPOINT_SUFFIXES = ("/api/v1/api/hosts", "/api/v1/api", "/api/v1")
_HINTS = {
    401: "the Token Key or Token Secret is wrong, disabled, or expired",
    403: "the credential lacks the host:get scope, or its IP allowlist excludes this server",
    404: "PatchMon has no such host",
}


class PatchmonError(RuntimeError):
    """PatchMon's API couldn't be reached, refused the call, or answered oddly.

    Messages name the path and status, never the credential."""


def is_host_id(value: object) -> bool:
    return isinstance(value, str) and bool(_HOST_ID_RE.match(value))


def _root(base_url: str) -> str:
    root = base_url.strip().rstrip("/")
    for suffix in _ENDPOINT_SUFFIXES:
        if root.endswith(suffix):
            return root[: -len(suffix)]
    return root


class PatchmonClient:
    """Read-only client for PatchMon's `/api/v1/api` host endpoints."""

    def __init__(
        self,
        base_url: str,
        key: str,
        secret: str,
        *,
        timeout: float = 5.0,
        retries: int = 2,
        backoff: float = 0.25,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = f"{_root(base_url)}/api/v1/api"
        self._auth = httpx.BasicAuth(key, secret)
        self._timeout = timeout
        self._retries = max(1, retries)
        self._backoff = backoff
        self._transport = transport

    async def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        url = f"{self._base}/{path}"
        last_exc: Exception | None = None
        for attempt in range(self._retries):
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout,
                    transport=self._transport,
                    follow_redirects=False,
                ) as client:
                    response = await client.get(url, params=params, auth=self._auth)
            except httpx.HTTPError as exc:
                last_exc = exc
            else:
                status = response.status_code
                if status < 300:
                    try:
                        body = response.json()
                    except ValueError as exc:
                        raise PatchmonError(f"PatchMon API sent non-JSON for {path}") from exc
                    if not isinstance(body, dict):
                        raise PatchmonError(f"PatchMon API sent an unexpected shape for {path}")
                    return body
                if status < 500:
                    # A 3xx (never followed) or a 4xx won't change on a retry.
                    hint = _HINTS.get(status)
                    raise PatchmonError(
                        f"PatchMon API returned {status} for {path}" + (f": {hint}" if hint else "")
                    )
                last_exc = PatchmonError(f"PatchMon API returned {status} for {path}")
            if attempt < self._retries - 1:
                await asyncio.sleep(self._backoff * (2**attempt))
        raise PatchmonError(
            f"PatchMon API request to {path} failed: {type(last_exc).__name__}"
        ) from last_exc

    def _host_path(self, host_id: str, endpoint: str) -> str:
        if not is_host_id(host_id):
            raise PatchmonError("not a PatchMon host id (expected a UUID)")
        return f"hosts/{host_id}/{endpoint}"

    async def get_host_info(self, host_id: str) -> dict[str, Any]:
        """`id`, `friendly_name`, `hostname`, `ip`, `os_type`, `os_version`,
        `agent_version`, `machine_id`, `host_groups`. A string PatchMon has no
        value for comes back empty, not null."""
        return await self._get(self._host_path(host_id, "info"))

    async def get_host_system(self, host_id: str) -> dict[str, Any]:
        """Kernel (`kernel_version` running, `installed_kernel_version`),
        `needs_reboot` and `reboot_reason`, CPU, RAM, disks, uptime."""
        return await self._get(self._host_path(host_id, "system"))

    async def list_host_packages(
        self, host_id: str, *, updates_only: bool = True
    ) -> list[dict[str, Any]]:
        """Packages PatchMon last saw on the host, security updates first. Each
        has `name`, `current_version`, `available_version` (null when current),
        `needs_update`, `is_security_update`."""
        params = {"updates_only": "true"} if updates_only else None
        body = await self._get(self._host_path(host_id, "packages"), params)
        packages = body.get("packages")
        if not isinstance(packages, list):
            raise PatchmonError("PatchMon API sent an unexpected shape for packages")
        return [item for item in packages if isinstance(item, dict)]

    async def list_package_reports(self, host_id: str, *, limit: int = 1) -> list[dict[str, Any]]:
        """The host's latest agent reports, newest first: `date`, `status`,
        `outdated_packages`, `security_updates`, `error_message`."""
        body = await self._get(
            self._host_path(host_id, "package_reports"), {"limit": str(max(1, min(limit, 100)))}
        )
        reports = body.get("reports")
        if not isinstance(reports, list):
            raise PatchmonError("PatchMon API sent an unexpected shape for package_reports")
        return [item for item in reports if isinstance(item, dict)]


def build_patchmon_client(
    settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
) -> PatchmonClient | None:
    """A client when PATCHMON_API_URL, _KEY, and _SECRET are all set, else None."""
    key = (reveal(settings.patchmon_api_key) or "").strip()
    secret = (reveal(settings.patchmon_api_secret) or "").strip()
    if not (settings.patchmon_api_url and settings.patchmon_api_url.strip() and key and secret):
        return None
    return PatchmonClient(
        settings.patchmon_api_url,
        key,
        secret,
        timeout=settings.patchmon_api_timeout_seconds,
        transport=transport,
    )
