"""A stand-in Authentik for the OAuth tests: a real RSA key, the JWKS and OpenID
discovery document Authentik would serve for it, and tokens signed with it.

Tokens are real JWTs signed with a real key, so the verifier under test runs
PyJWT's own signature, `iss`, `aud` and `exp` checks rather than a mock of them.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from http_fakes import strict_transport

ISSUER = "https://sso.example.com/application/o/registry-mcp/"
AUDIENCE = "registry-mcp-client-id"
GROUP = "registry-mcp-admins"
RESOURCE = "https://registry-mcp.example.com/mcp"
JWKS_PATH = "/application/o/registry-mcp/jwks/"
DISCOVERY_PATH = "/application/o/registry-mcp/.well-known/openid-configuration"
JWKS_URL = "https://sso.example.com" + JWKS_PATH


class FakeAuthentik:
    """One signing key (more can be added to simulate rotation) and the two
    documents a verifier fetches. `requests` records every fetch."""

    def __init__(self) -> None:
        self.keys: dict[str, rsa.RSAPrivateKey] = {}
        self.requests: list[httpx.Request] = []
        self.add_key("key-1")

    def add_key(self, kid: str) -> rsa.RSAPrivateKey:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.keys[kid] = key
        return key

    def jwks(self) -> dict[str, Any]:
        entries = []
        for kid, key in self.keys.items():
            entry = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
            entries.append({**entry, "kid": kid, "alg": "RS256", "use": "sig"})
        return {"keys": entries}

    def transport(self) -> httpx.MockTransport:
        return strict_transport(
            {
                f"GET {JWKS_PATH}": lambda _r: httpx.Response(200, json=self.jwks()),
                f"GET {DISCOVERY_PATH}": {"issuer": ISSUER, "jwks_uri": JWKS_URL},
            },
            captured=self.requests,
        )

    def token(
        self,
        *,
        kid: str = "key-1",
        alg: str = "RS256",
        headers: dict[str, Any] | None = None,
        **overrides: Any,
    ) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "azp": AUDIENCE,
            "sub": "user-123",
            "iat": now,
            "exp": now + 300,
            "scope": "openid profile",
            "groups": [GROUP],
        }
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(
            claims, self.keys[kid], algorithm=alg, headers={"kid": kid, **(headers or {})}
        )
