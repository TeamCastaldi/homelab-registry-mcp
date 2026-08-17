"""Stateless, HMAC-signed tokens for the chat session and the OIDC login flow.

No server-side session store: the signed cookie *is* the session, so state
survives a process restart whenever `CHAT_SESSION_SECRET` is set, and no new
dependency is needed (stdlib `hmac`/`secrets` only — see the design note in
`registry_mcp.chat.auth` on why a JWT/`itsdangerous`-class library was
skipped). `resolve_secret()` is meant to be called once, at route
registration time, and its result threaded through closures — the same
"compute once in `build_server`, close over it" shape already used for
`read_only` (see `tools/secrets.py`'s `_read_only_error`) — rather than
cached behind a module-level global.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

_SESSION_VERSION = 1
_FLOW_TYPE = "flow"


def resolve_secret(configured: str | None) -> bytes:
    """Return the signing key as bytes.

    A configured secret is used verbatim; an unset one gets a fresh random
    key so signing is never skipped — deliberately fail-safe (sessions don't
    survive a restart) rather than fail-open (no signature at all). Call
    this once at startup, not per-request — a new key on every call would
    invalidate every cookie immediately.
    """
    if configured:
        return configured.encode("utf-8")
    return secrets.token_urlsafe(32).encode("utf-8")


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sign(secret: bytes, payload: str) -> str:
    mac = hmac.new(secret, payload.encode("ascii"), hashlib.sha256).digest()
    return _b64url_encode(mac)


def encode_token(secret: bytes, data: dict[str, Any]) -> str:
    """Serialize `data` to a signed, URL-safe token: `<payload>.<signature>`.

    The signature covers the *encoded* payload string, not the raw dict, so
    there is no canonicalization ambiguity to worry about between encode and
    verify.
    """
    payload = _b64url_encode(json.dumps(data, separators=(",", ":")).encode("utf-8"))
    return f"{payload}.{_sign(secret, payload)}"


def decode_token(secret: bytes, token: str) -> dict[str, Any] | None:
    """Verify and decode a token produced by `encode_token`.

    Returns None on any failure — bad shape, tampered signature, wrong key,
    non-JSON payload — never raises, so callers can treat "no valid session"
    and "malformed cookie" identically.
    """
    try:
        payload, signature = token.rsplit(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(signature, _sign(secret, payload)):
        return None
    try:
        data = json.loads(_b64url_decode(payload))
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


# --- Session (post-login identity) ------------------------------------------


@dataclass(frozen=True)
class Identity:
    """The authenticated principal for a chat session."""

    sub: str
    name: str
    email: str | None
    groups: tuple[str, ...]
    amr: str  # "oidc" or "password" — how this identity was established


def issue_session(secret: bytes, identity: Identity, *, ttl_seconds: int) -> str:
    """Mint a signed session token for `identity`, valid for `ttl_seconds`."""
    now = int(time.time())
    return encode_token(
        secret,
        {
            "v": _SESSION_VERSION,
            "sub": identity.sub,
            "name": identity.name,
            "email": identity.email,
            "groups": list(identity.groups),
            "amr": identity.amr,
            "iat": now,
            "exp": now + ttl_seconds,
        },
    )


def verify_session(secret: bytes, token: str) -> Identity | None:
    """Verify and unpack a session token, or None if invalid/expired."""
    data = decode_token(secret, token)
    if data is None or data.get("v") != _SESSION_VERSION:
        return None
    exp = data.get("exp")
    if not isinstance(exp, int) or exp < int(time.time()):
        return None
    sub = data.get("sub")
    if not isinstance(sub, str) or not sub:
        return None
    groups = data.get("groups")
    if not isinstance(groups, list):
        groups = []
    name = data.get("name")
    email = data.get("email")
    return Identity(
        sub=sub,
        name=name if isinstance(name, str) and name else sub,
        email=email if isinstance(email, str) else None,
        groups=tuple(str(g) for g in groups),
        amr=str(data.get("amr", "")),
    )


def session_needs_refresh(secret: bytes, token: str, *, ttl_seconds: int) -> bool:
    """True once more than half the session's TTL has elapsed.

    Callers use this to decide whether to re-issue the cookie on a request
    (a sliding-expiration refresh) without unpacking the token twice.
    """
    data = decode_token(secret, token)
    if data is None:
        return False
    iat = data.get("iat")
    if not isinstance(iat, int):
        return False
    return time.time() - iat > ttl_seconds / 2


# --- Login flow (OIDC state/PKCE, held between /login and /callback) -------


@dataclass(frozen=True)
class LoginFlow:
    state: str
    verifier: str
    nonce: str
    next_path: str


def issue_flow(
    secret: bytes,
    *,
    state: str,
    verifier: str,
    nonce: str,
    next_path: str,
    ttl_seconds: int = 300,
) -> str:
    """Mint a short-lived token carrying OIDC `state`/PKCE `verifier`/`nonce`.

    Held in a cookie between the redirect to the IdP and the callback,
    instead of server memory — keeps the server stateless and the flow
    restart-safe. 300s default is generous for a human to complete a login
    redirect but short enough that a leaked flow cookie is nearly useless.
    """
    now = int(time.time())
    return encode_token(
        secret,
        {
            "t": _FLOW_TYPE,
            "state": state,
            "verifier": verifier,
            "nonce": nonce,
            "next": next_path,
            "exp": now + ttl_seconds,
        },
    )


def verify_flow(secret: bytes, token: str) -> LoginFlow | None:
    """Verify and unpack a login-flow token, or None if invalid/expired."""
    data = decode_token(secret, token)
    if data is None or data.get("t") != _FLOW_TYPE:
        return None
    exp = data.get("exp")
    if not isinstance(exp, int) or exp < int(time.time()):
        return None
    state, verifier, nonce = data.get("state"), data.get("verifier"), data.get("nonce")
    if not all(isinstance(x, str) and x for x in (state, verifier, nonce)):
        return None
    next_path = data.get("next")
    if not isinstance(next_path, str) or not next_path.startswith("/chat"):
        next_path = "/chat"
    return LoginFlow(state=state, verifier=verifier, nonce=nonce, next_path=next_path)
