"""Check an Authentik access token: signature against the issuer's JWKS, then
`iss`, `aud`, `exp` and `nbf`.

The SDK calls `verify_token` once per request and turns `None` into a 401, so a
token that fails any check here never reaches a tool. The group check is not
here: a valid token outside the group earns 403, not 401, so it lives in
`middleware.py` and reads the claims returned on the `AccessToken`.

Only asymmetric algorithms are accepted. Authentik signs with HS256 and the
client secret when its provider has no signing key, and a public client has no
secret to share with this server, so an HS256 token means the provider is
misconfigured (or someone is trying the public key as an HMAC secret). `none`
is never accepted.

The key set is cached for `MCP_AUTH_JWKS_CACHE_SECONDS`. A token naming a key
id the cache lacks forces one early refetch, so a rotated key works at once,
but no more than once per `_MIN_REFETCH_SECONDS`: otherwise anyone could send
tokens with made-up key ids and make this server hammer Authentik. Redirects
are never followed.

A token is never logged, only the reason it was refused.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import jwt
from mcp.server.auth.provider import AccessToken

from registry_mcp.logging.events import get_logger

ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384")
_LEEWAY_SECONDS = 30
_MIN_REFETCH_SECONDS = 30
_FETCH_TIMEOUT_SECONDS = 5.0

_log = get_logger("auth.verifier")


class JwksUnavailableError(Exception):
    """The key set couldn't be fetched or parsed; reported by name, never a body."""


class AuthentikTokenVerifier:
    """A `mcp.server.auth.provider.TokenVerifier` for one Authentik provider."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str | None = None,
        cache_seconds: int = 300,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Any = time.time,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._jwks_url = jwks_url
        self._cache_seconds = cache_seconds
        self._transport = transport
        self._clock = clock
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float | None = None
        self._lock = asyncio.Lock()

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            return self._refuse("malformed")
        alg = header.get("alg")
        if alg not in ALGORITHMS:
            return self._refuse("algorithm_not_allowed", alg=str(alg)[:16])
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            return self._refuse("no_key_id")
        try:
            key = await self._key(kid)
        except JwksUnavailableError as exc:
            return self._refuse("jwks_unavailable", detail=str(exc))
        if key is None:
            return self._refuse("unknown_key_id")
        if key.algorithm_name != alg:
            return self._refuse("algorithm_mismatch", alg=alg)
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=[alg],
                audience=self._audience,
                issuer=self._issuer,
                leeway=_LEEWAY_SECONDS,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.ExpiredSignatureError:
            return self._refuse("expired")
        except jwt.InvalidAudienceError:
            return self._refuse("wrong_audience")
        except jwt.InvalidIssuerError:
            return self._refuse("wrong_issuer")
        except jwt.PyJWTError as exc:
            return self._refuse("invalid", reason_type=type(exc).__name__)
        scope = claims.get("scope")
        return AccessToken(
            token=token,
            client_id=str(claims.get("azp") or self._audience),
            scopes=scope.split() if isinstance(scope, str) else [],
            expires_at=int(claims["exp"]),
            subject=str(claims["sub"]),
            claims=claims,
        )

    async def _key(self, kid: str) -> jwt.PyJWK | None:
        async with self._lock:
            now = self._clock()
            stale = self._fetched_at is None or now - self._fetched_at >= self._cache_seconds
            missing = kid not in self._keys
            may_refetch = self._fetched_at is None or now - self._fetched_at >= _MIN_REFETCH_SECONDS
            if stale or (missing and may_refetch):
                self._keys = await self._fetch_keys()
                self._fetched_at = now
            return self._keys.get(kid)

    async def _fetch_keys(self) -> dict[str, jwt.PyJWK]:
        async with httpx.AsyncClient(
            timeout=_FETCH_TIMEOUT_SECONDS, follow_redirects=False, transport=self._transport
        ) as client:
            url = self._jwks_url or await self._discover_jwks_url(client)
            body = await _get_json(client, url, "jwks")
        keys = body.get("keys") if isinstance(body, dict) else None
        if not isinstance(keys, list):
            raise JwksUnavailableError("jwks has no keys list")
        found: dict[str, jwt.PyJWK] = {}
        for entry in keys:
            if not isinstance(entry, dict) or entry.get("use", "sig") != "sig":
                continue
            kid = entry.get("kid")
            if not isinstance(kid, str):
                continue
            try:
                found[kid] = jwt.PyJWK(entry)
            except jwt.PyJWTError:
                continue  # a key type this PyJWT can't load is skipped, not fatal
        return found

    async def _discover_jwks_url(self, client: httpx.AsyncClient) -> str:
        config = await _get_json(
            client, self._issuer.rstrip("/") + "/.well-known/openid-configuration", "discovery"
        )
        url = config.get("jwks_uri") if isinstance(config, dict) else None
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            raise JwksUnavailableError("discovery has no jwks_uri")
        self._jwks_url = url
        return url

    def _refuse(self, reason: str, **context: str) -> None:
        _log.info("mcp_token_refused", reason=reason, **context)
        return None


async def _get_json(client: httpx.AsyncClient, url: str, what: str) -> Any:
    try:
        response = await client.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise JwksUnavailableError(f"{what} request failed: {type(exc).__name__}") from None
    if response.status_code != 200:
        raise JwksUnavailableError(f"{what} answered HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError:
        raise JwksUnavailableError(f"{what} is not JSON") from None
