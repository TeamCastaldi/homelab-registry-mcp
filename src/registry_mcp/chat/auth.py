"""Chat authentication: Authentik (or any OIDC provider) authorization-code
+ PKCE flow, with a static password as the fallback.

Resolution order — decided once per request by `resolve_mode()`, never
mixed: OIDC when all four `CHAT_OIDC_*` values are set, else the static
`CHAT_PASSWORD`, else chat refuses to serve at all (`registry_mcp.chat.
routes` treats "disabled" as a reason not to register the chat routes, not
as an open endpoint — see ADR-009).

**Why no ID-token signature verification.** This is a confidential client
doing a back-channel authorization-code exchange: the code is redeemed
directly against the IdP's token endpoint over TLS, so the token in hand is
already known to have come from the IdP — the redirect-based reason to
verify a *front-channel* ID token's signature (implicit flow, SPA with no
backend) doesn't apply here. This keeps a JWT/JWKS library out of the
dependency tree. Identity is taken from `/userinfo`, fetched with the
access token, which is the same trust boundary. (OpenID Connect Core
§3.1.3.7 documents this exemption for the authorization code flow.)

**PKCE on a confidential client** is defense in depth, not a requirement —
the client secret lives in an env file on a LAN-reachable host
(`docker-compose.yml` binds `0.0.0.0:8765`), so treating the flow as if it
could be intercepted costs nothing and buys something.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlencode

import httpx

from registry_mcp.chat.session import Identity
from registry_mcp.config import Settings

AuthMode = Literal["oidc", "password", "disabled"]

_OIDC_TIMEOUT_SECONDS = 10.0
_DISCOVERY_TTL_SECONDS = 3600.0


class OidcError(RuntimeError):
    """Raised when OIDC discovery, code exchange, or userinfo fails."""


def resolve_mode(settings: Settings) -> AuthMode:
    """Decide which auth method is active. OIDC wins whenever fully
    configured, regardless of whether `CHAT_PASSWORD` is also set — the two
    are never combined."""
    if (
        settings.chat_oidc_issuer
        and settings.chat_oidc_client_id
        and settings.chat_oidc_client_secret
        and settings.chat_oidc_redirect_url
    ):
        return "oidc"
    if settings.chat_password:
        return "password"
    return "disabled"


# --- OIDC: discovery ---------------------------------------------------------


@dataclass(frozen=True)
class OidcEndpoints:
    authorization_endpoint: str
    token_endpoint: str
    userinfo_endpoint: str


@dataclass
class _DiscoveryCacheEntry:
    endpoints: OidcEndpoints
    fetched_at: float


# Keyed by issuer URL rather than a single slot — harmless in production
# (one issuer per process) and keeps tests using a different issuer per case
# from colliding with each other.
_discovery_cache: dict[str, _DiscoveryCacheEntry] = {}


async def _json_request(
    method: str,
    url: str,
    *,
    timeout: float,
    transport: httpx.AsyncBaseTransport | None,
    retries: int = 2,
    backoff: float = 0.5,
    **kwargs: Any,
) -> dict[str, Any]:
    """Same retry/timeout/error idiom as the other integration clients in
    this repo (see `integrations.traefik.client.TraefikClient._get`), as a
    plain function rather than a class — each OIDC call (discovery, token
    exchange, userinfo) has a different method/body/auth shape and there's
    no long-lived client identity to hang methods off of.
    """
    retries = max(1, retries)
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
                response = await client.request(method, url, **kwargs)
            response.raise_for_status()
            try:
                return response.json()
            except ValueError as exc:
                # A 2xx with a non-JSON body (the IdP, or a proxy in front
                # of it, misbehaving) must still surface as a controlled
                # OidcError, not an uncaught exception out of this function.
                raise OidcError(f"OIDC response from {url} was not valid JSON") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                raise OidcError(
                    f"OIDC request to {url} returned {exc.response.status_code}"
                ) from exc
            last_exc = exc
        except httpx.HTTPError as exc:
            last_exc = exc
        if attempt < retries - 1:
            await asyncio.sleep(backoff * (2**attempt))
    raise OidcError(f"OIDC request to {url} failed: {last_exc}") from last_exc


async def discover_oidc(
    issuer: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> OidcEndpoints:
    """Fetch (or return the cached) `authorization_endpoint`/`token_endpoint`/
    `userinfo_endpoint` from the issuer's `.well-known/openid-configuration`.

    Never called at route-registration time — only lazily, on first login —
    so `register_chat_routes()` stays free of network I/O (see
    `registry_mcp.chat.routes`).
    """
    now = time.monotonic()
    cached = _discovery_cache.get(issuer)
    if cached is not None and now - cached.fetched_at < _DISCOVERY_TTL_SECONDS:
        return cached.endpoints

    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    data = await _json_request("GET", url, timeout=_OIDC_TIMEOUT_SECONDS, transport=transport)
    try:
        endpoints = OidcEndpoints(
            authorization_endpoint=data["authorization_endpoint"],
            token_endpoint=data["token_endpoint"],
            userinfo_endpoint=data["userinfo_endpoint"],
        )
    except KeyError as exc:
        raise OidcError(f"OIDC discovery document at {url} is missing {exc}") from exc

    _discovery_cache[issuer] = _DiscoveryCacheEntry(endpoints=endpoints, fetched_at=now)
    return endpoints


# --- OIDC: authorization code + PKCE -----------------------------------------


def generate_pkce() -> tuple[str, str]:
    """Return `(code_verifier, code_challenge)` for PKCE with S256.

    `secrets.token_urlsafe(64)` yields ~86 URL-safe characters, comfortably
    inside RFC 7636's 43-128 char range for a verifier.
    """
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorize_url(
    endpoints: OidcEndpoints, settings: Settings, *, state: str, code_challenge: str
) -> str:
    """Build the redirect URL to the IdP's authorization endpoint.

    `redirect_uri` always comes from `CHAT_OIDC_REDIRECT_URL` — never from
    the incoming request's `Host` header, which would make this a
    token-leak primitive via Host-header injection.
    """
    params = {
        "response_type": "code",
        "client_id": settings.chat_oidc_client_id,
        "redirect_uri": settings.chat_oidc_redirect_url,
        "scope": settings.chat_oidc_scopes,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{endpoints.authorization_endpoint}?{urlencode(params)}"


async def exchange_code(
    endpoints: OidcEndpoints,
    settings: Settings,
    *,
    code: str,
    code_verifier: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Redeem an authorization code for tokens at the IdP's token endpoint."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.chat_oidc_redirect_url,
        "client_id": settings.chat_oidc_client_id,
        "client_secret": settings.chat_oidc_client_secret,
        "code_verifier": code_verifier,
    }
    return await _json_request(
        "POST",
        endpoints.token_endpoint,
        timeout=_OIDC_TIMEOUT_SECONDS,
        transport=transport,
        data=data,
    )


async def fetch_userinfo(
    endpoints: OidcEndpoints,
    *,
    access_token: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Fetch the authenticated principal's claims from `/userinfo`."""
    return await _json_request(
        "GET",
        endpoints.userinfo_endpoint,
        timeout=_OIDC_TIMEOUT_SECONDS,
        transport=transport,
        headers={"Authorization": f"Bearer {access_token}"},
    )


def identity_from_userinfo(userinfo: dict[str, Any]) -> Identity:
    """Map OIDC userinfo claims onto the chat session's `Identity`."""
    sub = str(userinfo.get("sub") or "")
    name = userinfo.get("name") or userinfo.get("preferred_username") or sub
    email = userinfo.get("email")
    groups_claim = userinfo.get("groups")
    groups = tuple(str(g) for g in groups_claim) if isinstance(groups_claim, list) else ()
    return Identity(
        sub=sub,
        name=str(name),
        email=str(email) if email else None,
        groups=groups,
        amr="oidc",
    )


def groups_allowed(settings: Settings, groups: tuple[str, ...]) -> bool:
    """Gate on `CHAT_OIDC_ALLOWED_GROUPS`.

    Empty (the default) means no additional restriction — any principal the
    IdP itself authenticated is allowed. This is authorization layered on
    top of a successful IdP login, not a public-facing trigger, so it
    doesn't share `PROPOSAL_COMMENT_ALLOWED_USERS`'s fail-closed-when-empty
    posture (see `config.py`'s comment on this field for the reasoning).
    """
    raw = settings.chat_oidc_allowed_groups.strip()
    if not raw:
        return True
    allowed = {g.strip() for g in raw.split(",") if g.strip()}
    if not allowed:
        return True
    return bool(allowed & set(groups))


# --- Static password ----------------------------------------------------------


def check_password(settings: Settings, candidate: str) -> bool:
    """Timing-safe comparison against `CHAT_PASSWORD`."""
    expected = settings.chat_password
    if not expected:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


class LoginAttemptLimiter:
    """A small in-process per-key failed-attempt backoff.

    Not a distributed rate limiter — this is a single-process LAN service,
    and the goal is raising the cost of online guessing past what's
    convenient, not defeating a determined attacker with API access to the
    box already. `key` is normally the caller's IP address.
    """

    def __init__(self, *, max_attempts: int = 5, window_seconds: float = 60.0) -> None:
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._failures: dict[str, list[float]] = {}

    def is_blocked(self, key: str) -> bool:
        now = time.monotonic()
        recent = [t for t in self._failures.get(key, []) if now - t < self._window_seconds]
        self._failures[key] = recent
        return len(recent) >= self._max_attempts

    def record_failure(self, key: str) -> None:
        self._failures.setdefault(key, []).append(time.monotonic())

    def record_success(self, key: str) -> None:
        self._failures.pop(key, None)
