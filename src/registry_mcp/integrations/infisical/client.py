"""Async client for Infisical's Universal Auth + secrets API (ADR-016).

Read-only by construction, not just by convention: this client never has a
write method, and `list_secret_keys` never returns a secret's value under
any circumstance -- only its key name. It requests `viewSecretValue=false`
on every read, but does not trust that parameter alone: every response is
inspected for confirmation the value was actually masked, and raises
`InfisicalSecretValueLeakedError` (carrying only the affected key's name,
never its value) if it wasn't. See ADR-016's "defensive value-leak gate".

Confirmed live against the operator's self-hosted instance: with
`viewSecretValue=false`, each secret comes back with `secretValueHidden:
true` and `secretValue: "<hidden-by-infisical>"` (a literal placeholder, not
`null`/omitted). The masking check below trusts neither field alone --
`secretValueHidden` must be `True` *and* `secretValue` must be one of the
known-safe placeholders, so a future Infisical version changing the exact
placeholder text fails closed (flagged as a leak) rather than silently
being accepted as new-but-fine.

`has_value` (whether a key's *value* is non-empty, distinct from whether the
key exists at all) is deliberately not reported: the placeholder is uniform
across every key regardless of whether the real value is empty or not, so
there is no signal here to distinguish "masked" from "genuinely empty"
without seeing the real value, which this client will never fetch. Key
existence is the only thing this can report.

`list_secret_tree` (ADR-017) extends the same read-only, never-a-value
guarantee across an entire folder subtree, for operators whose Infisical
project holds every service's secrets as sibling folders rather than one
project per service. It walks the tree via Infisical's folder-listing API
(`/api/v1/folders`) and reuses `list_secret_keys` per folder, rather than
relying on a `recursive` query flag on the secrets endpoint: this rollout
has twice found this self-hosted instance's real behavior diverging from
Infisical's documented API (the `workspaceSlug` parameter, and the exact
`viewSecretValue=false` masking shape above), so a widely-supported,
long-stable endpoint (folder listing) is preferred over an unverified flag
on a newer one. **Confirmed live**: `/api/v1/folders` returns exactly the
assumed `{"folders": [{"name": ...}]}` shape, and the walk correctly
recurses at least two levels deep. If a future Infisical version's
response shape ever diverges from what's assumed here, the walk degrades
to seeing only the root path's own secrets (the already-proven single-path
code path) rather than crashing -- but that failure mode is silent: it
looks identical to "this project genuinely has no subfolders." Re-confirm
live that multiple folders are actually returned after any Infisical
version upgrade, rather than trusting an empty result at face value.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

# Infisical's confirmed-live placeholder for a value hidden by
# viewSecretValue=false, plus the more conservative None/"" a future version
# might use instead. Anything else, or secretValueHidden not being exactly
# True, is treated as a live value having leaked through.
_MASKED_VALUE_PLACEHOLDERS = frozenset({None, "", "<hidden-by-infisical>"})


class InfisicalError(RuntimeError):
    """Raised when the Infisical API cannot be reached or returns an error."""


class InfisicalSecretValueLeakedError(InfisicalError):
    """Raised when Infisical returns a secret's live value despite
    `viewSecretValue=false` being requested.

    Carries only the affected key's *name* and the folder `path` it was
    found in -- never its value. Treat this as a live credential-compromise
    signal (see ADR-016): the value reached this process's memory even
    though it was never supposed to, and the named key should be rotated in
    Infisical immediately. `path` is optional because a caller reading a
    single, already-known folder (the common case) doesn't need it repeated
    back; `list_secret_tree` (ADR-017) always sets it, since the same key
    name can exist in more than one folder.
    """

    def __init__(self, key: str, path: str | None = None) -> None:
        self.key = key
        self.path = path
        location = f"{path.rstrip('/')}/{key}" if path else key
        super().__init__(
            f"Infisical returned a live value for {location!r} despite "
            "viewSecretValue=false being requested -- rotate this secret "
            "in Infisical immediately"
        )


class InfisicalClient:
    """Read-only client for Infisical's Universal Auth + secrets-raw API."""

    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        *,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout = timeout
        self._transport = transport
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        # Concurrent calls that find no valid token wait for one login
        # instead of each doing their own.
        self._login_lock = asyncio.Lock()

    async def _login(self) -> str:
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            response = await client.post(
                f"{self._base}/api/v1/auth/universal-auth/login",
                json={"clientId": self._client_id, "clientSecret": self._client_secret},
            )
        if response.status_code >= 400:
            raise InfisicalError(f"Infisical Universal Auth login failed: {response.status_code}")
        payload = response.json()
        token = payload.get("accessToken")
        if not token:
            raise InfisicalError("Infisical Universal Auth login returned no access token")
        # Refresh a bit early rather than exactly at expiry; fall back to a
        # conservative default if the response doesn't say how long the
        # token lives.
        expires_in = payload.get("expiresIn") or 300
        self._token_expires_at = time.monotonic() + max(30, expires_in - 30)
        self._token = token
        return token

    def _valid_token(self) -> str | None:
        if self._token is None or time.monotonic() >= self._token_expires_at:
            return None
        return self._token

    async def _access_token(self) -> str:
        if token := self._valid_token():
            return token
        async with self._login_lock:
            # Another call may have logged in while this one waited.
            return self._valid_token() or await self._login()

    async def list_secret_keys(
        self, project_id: str, environment: str, secret_path: str
    ) -> list[str]:
        """Return the secret key names configured at `project_id`/`environment`/
        `secret_path` -- never a value, under any circumstance.

        Raises `InfisicalSecretValueLeakedError` (never forwarding the value)
        if any entry carries a live, non-empty `secretValue` despite
        `viewSecretValue=false` being requested.
        """
        token = await self._access_token()
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            response = await client.get(
                f"{self._base}/api/v3/secrets/raw",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "workspaceId": project_id,
                    "environment": environment,
                    "secretPath": secret_path,
                    "viewSecretValue": "false",
                },
            )
        if response.status_code >= 400:
            raise InfisicalError(f"Infisical secrets read failed: {response.status_code}")
        secrets: list[dict[str, Any]] = response.json().get("secrets", [])
        keys: list[str] = []
        for secret in secrets:
            key = secret.get("secretKey", "")
            value = secret.get("secretValue")
            hidden = secret.get("secretValueHidden")
            if hidden is not True or value not in _MASKED_VALUE_PLACEHOLDERS:
                raise InfisicalSecretValueLeakedError(key, path=secret_path)
            keys.append(key)
        return keys

    async def list_folder_names(self, project_id: str, environment: str, path: str) -> list[str]:
        """Return the immediate child folder names under `path` (not
        recursive) -- e.g. `/` -> `["authentik", "homelab-registry-mcp", ...]`.

        UNVERIFIED against the operator's live instance (see this module's
        docstring): assumes `GET /api/v1/folders` takes the same
        `workspaceId`/`environment` parameter names as the confirmed-live
        `/api/v3/secrets/raw`, plus a `path` query param, and returns
        `{"folders": [{"name": ..., ...}, ...]}`.
        """
        token = await self._access_token()
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            response = await client.get(
                f"{self._base}/api/v1/folders",
                headers={"Authorization": f"Bearer {token}"},
                params={"workspaceId": project_id, "environment": environment, "path": path},
            )
        if response.status_code >= 400:
            raise InfisicalError(f"Infisical folder listing failed: {response.status_code}")
        folders: list[dict[str, Any]] = response.json().get("folders", [])
        return [name for f in folders if (name := f.get("name"))]

    async def list_secret_tree(
        self,
        project_id: str,
        environment: str,
        root_path: str,
        *,
        max_folders: int = 50,
    ) -> tuple[dict[str, list[str]], list[str]]:
        """Recursively list secret key names under `root_path` and every
        subfolder beneath it, grouped by the exact folder each key lives in
        -- never a value, under any circumstance (ADR-017).

        Walks the folder tree breadth-first via `list_folder_names`, reusing
        `list_secret_keys` (and its value-leak gate) per folder, so a folder
        the Machine Identity can't read is skipped and returned in the
        second tuple element instead of failing the whole sweep. A leak
        anywhere in the tree still fails the whole sweep closed --
        `InfisicalSecretValueLeakedError` propagates immediately, never
        caught here.

        `max_folders` bounds how many folders one sweep visits, so a very
        large or deeply nested project can't make one tool call balloon
        unboundedly; folders past the cap are simply not visited.
        """
        root_path = root_path or "/"
        by_path: dict[str, list[str]] = {}
        inaccessible: list[str] = []
        queue: list[str] = [root_path]
        visited = 0

        while queue and visited < max_folders:
            current = queue.pop(0)
            visited += 1
            try:
                keys = await self.list_secret_keys(project_id, environment, current)
            except InfisicalSecretValueLeakedError:
                raise
            except InfisicalError:
                inaccessible.append(current)
                continue
            if keys:
                by_path[current] = keys
            try:
                children = await self.list_folder_names(project_id, environment, current)
            except InfisicalError:
                # Secrets at this folder (if any) are already captured above;
                # just can't enumerate its subfolders.
                continue
            base = current.rstrip("/")
            queue.extend(f"{base}/{name}" for name in children)

        return by_path, inaccessible
