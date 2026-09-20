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
"""

from __future__ import annotations

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

    Carries only the affected key's *name* -- never its value. Treat this as
    a live credential-compromise signal (see ADR-016): the value reached
    this process's memory even though it was never supposed to, and the
    named key should be rotated in Infisical immediately.
    """

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(
            f"Infisical returned a live value for key {key!r} despite "
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

    async def _access_token(self) -> str:
        if self._token is None or time.monotonic() >= self._token_expires_at:
            return await self._login()
        return self._token

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
                raise InfisicalSecretValueLeakedError(key)
            keys.append(key)
        return keys
