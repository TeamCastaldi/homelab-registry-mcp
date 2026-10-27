"""The access-token check behind /mcp: every way a token can be wrong is refused.

Each refusal is a `None`, which the SDK turns into a 401. The group check is not
here: a valid token outside the group is a 403, tested in test_mcp_auth.py.
"""

import base64
import json
import time

import jwt
import pytest
import structlog
from cryptography.hazmat.primitives import serialization

from auth_fakes import AUDIENCE, GROUP, ISSUER, JWKS_URL, FakeAuthentik
from registry_mcp.auth.verifier import AuthentikTokenVerifier


@pytest.fixture
def authentik():
    return FakeAuthentik()


def make_verifier(authentik, **kwargs):
    defaults = {"issuer": ISSUER, "audience": AUDIENCE, "transport": authentik.transport()}
    return AuthentikTokenVerifier(**{**defaults, **kwargs})


async def test_a_valid_token_is_accepted_with_its_claims(authentik):
    access = await make_verifier(authentik).verify_token(authentik.token())
    assert access is not None
    assert access.subject == "user-123"
    assert access.client_id == AUDIENCE
    assert access.scopes == ["openid", "profile"]
    assert access.claims["groups"] == [GROUP]


async def test_the_jwks_url_is_found_through_the_issuers_discovery_document(authentik):
    assert await make_verifier(authentik).verify_token(authentik.token())
    assert [r.url.path for r in authentik.requests] == [
        "/application/o/registry-mcp/.well-known/openid-configuration",
        "/application/o/registry-mcp/jwks/",
    ]


async def test_a_configured_jwks_url_skips_discovery(authentik):
    assert await make_verifier(authentik, jwks_url=JWKS_URL).verify_token(authentik.token())
    assert [r.url.path for r in authentik.requests] == ["/application/o/registry-mcp/jwks/"]


@pytest.mark.parametrize(
    "claims",
    [
        pytest.param({"aud": "some-other-client"}, id="wrong-audience"),
        pytest.param({"iss": "https://sso.example.com/application/o/other/"}, id="wrong-issuer"),
        pytest.param({"exp": int(time.time()) - 120}, id="expired"),
        pytest.param({"nbf": int(time.time()) + 600}, id="not-yet-valid"),
        pytest.param({"aud": None}, id="no-audience"),
        pytest.param({"exp": None}, id="no-expiry"),
        pytest.param({"sub": None}, id="no-subject"),
    ],
)
async def test_a_token_with_a_bad_claim_is_refused(authentik, claims):
    assert await make_verifier(authentik).verify_token(authentik.token(**claims)) is None


async def test_a_token_signed_by_a_key_outside_the_jwks_is_refused(authentik):
    stranger = FakeAuthentik()  # its own key, with the same kid
    assert await make_verifier(authentik).verify_token(stranger.token()) is None


async def test_a_tampered_payload_is_refused(authentik):
    header, _payload, signature = authentik.token().split(".")
    forged = {"iss": ISSUER, "aud": AUDIENCE, "sub": "admin", "exp": int(time.time()) + 300}
    payload = base64.urlsafe_b64encode(json.dumps(forged).encode()).rstrip(b"=").decode()
    assert await make_verifier(authentik).verify_token(f"{header}.{payload}.{signature}") is None


async def test_an_unsigned_token_is_refused(authentik):
    token = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "exp": int(time.time()) + 300},
        key=None,
        algorithm="none",
        headers={"kid": "key-1"},
    )
    assert await make_verifier(authentik).verify_token(token) is None


async def test_an_hmac_token_keyed_with_the_public_key_is_refused(authentik):
    """The classic algorithm-confusion forgery: anyone can read the public key."""
    public_pem = (
        authentik.keys["key-1"]
        .public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "HS256", "typ": "JWT", "kid": "key-1"}).encode()
    ).rstrip(b"=")
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "exp": int(time.time()) + 300}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=")
    import hashlib
    import hmac

    signing_input = header + b"." + payload
    mac = hmac.new(public_pem, signing_input, hashlib.sha256).digest()
    token = (signing_input + b"." + base64.urlsafe_b64encode(mac).rstrip(b"=")).decode()
    assert await make_verifier(authentik).verify_token(token) is None


async def test_a_header_algorithm_other_than_the_keys_is_refused(authentik):
    # PS256 is an allowed algorithm and the key is RSA, but the JWKS says RS256.
    token = authentik.token(alg="PS256")
    assert await make_verifier(authentik).verify_token(token) is None


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b.c", "Bearer x"])
async def test_garbage_is_refused(authentik, token):
    assert await make_verifier(authentik).verify_token(token) is None


async def test_a_token_with_no_key_id_is_refused(authentik):
    token = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "exp": int(time.time()) + 300},
        authentik.keys["key-1"],
        algorithm="RS256",
    )
    assert await make_verifier(authentik).verify_token(token) is None


async def test_a_rotated_key_is_picked_up_at_once(authentik):
    clock = [1000.0]
    verifier = make_verifier(authentik, jwks_url=JWKS_URL, clock=lambda: clock[0])
    assert await verifier.verify_token(authentik.token())
    authentik.add_key("key-2")
    clock[0] += 31  # past the refetch floor, well inside the cache lifetime
    assert await verifier.verify_token(authentik.token(kid="key-2"))


async def test_unknown_key_ids_cannot_make_it_hammer_the_jwks(authentik):
    clock = [1000.0]
    verifier = make_verifier(authentik, jwks_url=JWKS_URL, clock=lambda: clock[0])
    assert await verifier.verify_token(authentik.token())
    stranger = FakeAuthentik()
    stranger.add_key("made-up")
    for _ in range(20):
        assert await verifier.verify_token(stranger.token(kid="made-up")) is None
    assert len(authentik.requests) == 1


async def test_the_cached_key_set_is_refetched_once_it_expires(authentik):
    clock = [1000.0]
    verifier = make_verifier(
        authentik, jwks_url=JWKS_URL, cache_seconds=300, clock=lambda: clock[0]
    )
    await verifier.verify_token(authentik.token())
    await verifier.verify_token(authentik.token())
    assert len(authentik.requests) == 1
    clock[0] += 300
    await verifier.verify_token(authentik.token())
    assert len(authentik.requests) == 2


async def test_an_unreachable_jwks_refuses_rather_than_accepts(authentik):
    verifier = make_verifier(authentik, jwks_url="https://sso.example.com/nowhere/")
    assert await verifier.verify_token(authentik.token()) is None


async def test_a_refusal_logs_why_but_never_the_token(authentik):
    token = authentik.token(aud="some-other-client")
    with structlog.testing.capture_logs() as logs:
        assert await make_verifier(authentik).verify_token(token) is None
    assert [e["reason"] for e in logs if e["event"] == "mcp_token_refused"] == ["wrong_audience"]
    assert token.split(".")[1] not in repr(logs)
