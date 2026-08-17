"""Tests for the HMAC-signed session/login-flow tokens (registry_mcp.chat.session)."""

import time

from registry_mcp.chat.session import (
    Identity,
    decode_token,
    encode_token,
    issue_flow,
    issue_session,
    resolve_secret,
    session_needs_refresh,
    verify_flow,
    verify_session,
)

SECRET = resolve_secret("test-secret")


# --- resolve_secret -----------------------------------------------------------


def test_resolve_secret_uses_configured_value():
    assert resolve_secret("my-secret") == b"my-secret"


def test_resolve_secret_generates_ephemeral_when_unset():
    a = resolve_secret(None)
    b = resolve_secret(None)
    assert a != b  # each call with no configured value is independent
    assert len(a) > 16


# --- encode_token / decode_token ----------------------------------------------


def test_token_round_trips():
    token = encode_token(SECRET, {"hello": "world"})
    assert decode_token(SECRET, token) == {"hello": "world"}


def test_token_rejects_tampered_signature():
    token = encode_token(SECRET, {"hello": "world"})
    payload, sig = token.rsplit(".", 1)
    tampered = payload + "." + ("a" if sig[0] != "a" else "b") + sig[1:]
    assert decode_token(SECRET, tampered) is None


def test_token_rejects_tampered_payload():
    token = encode_token(SECRET, {"admin": False})
    payload, sig = token.rsplit(".", 1)
    # Flip a character in the payload without recomputing the signature.
    tampered_payload = ("a" if payload[0] != "a" else "b") + payload[1:]
    assert decode_token(SECRET, f"{tampered_payload}.{sig}") is None


def test_token_rejects_wrong_key():
    token = encode_token(SECRET, {"hello": "world"})
    assert decode_token(resolve_secret("other-secret"), token) is None


def test_token_rejects_malformed_string():
    assert decode_token(SECRET, "not-a-token") is None
    assert decode_token(SECRET, "") is None
    assert decode_token(SECRET, "..") is None


def test_token_rejects_non_dict_payload():
    token = encode_token(SECRET, [1, 2, 3])  # type: ignore[arg-type]
    assert decode_token(SECRET, token) is None


# --- session -------------------------------------------------------------------


def test_session_round_trips():
    identity = Identity(
        sub="u1", name="Nathan", email="n@example.com", groups=("admins",), amr="oidc"
    )
    token = issue_session(SECRET, identity, ttl_seconds=3600)
    verified = verify_session(SECRET, token)
    assert verified == identity


def test_session_rejects_expired():
    identity = Identity(sub="u1", name="N", email=None, groups=(), amr="password")
    token = issue_session(SECRET, identity, ttl_seconds=-10)
    assert verify_session(SECRET, token) is None


def test_session_rejects_tampered():
    identity = Identity(sub="u1", name="N", email=None, groups=(), amr="password")
    token = issue_session(SECRET, identity, ttl_seconds=3600)
    assert verify_session(SECRET, token + "x") is None


def test_session_defaults_name_to_sub_when_missing():
    token = encode_token(
        SECRET, {"v": 1, "sub": "u1", "iat": int(time.time()), "exp": int(time.time()) + 60}
    )
    identity = verify_session(SECRET, token)
    assert identity is not None
    assert identity.name == "u1"
    assert identity.groups == ()


def test_session_rejects_missing_sub():
    token = encode_token(SECRET, {"v": 1, "iat": int(time.time()), "exp": int(time.time()) + 60})
    assert verify_session(SECRET, token) is None


def test_session_rejects_wrong_version():
    token = encode_token(
        SECRET, {"v": 2, "sub": "u1", "iat": int(time.time()), "exp": int(time.time()) + 60}
    )
    assert verify_session(SECRET, token) is None


def test_session_needs_refresh_past_half_ttl():
    identity = Identity(sub="u1", name="N", email=None, groups=(), amr="password")
    token = encode_token(
        SECRET,
        {
            "v": 1,
            "sub": identity.sub,
            "name": identity.name,
            "email": None,
            "groups": [],
            "amr": "password",
            "iat": int(time.time()) - 100,
            "exp": int(time.time()) + 10000,
        },
    )
    assert session_needs_refresh(SECRET, token, ttl_seconds=100) is True
    assert session_needs_refresh(SECRET, token, ttl_seconds=100000) is False


def test_session_needs_refresh_false_for_invalid_token():
    assert session_needs_refresh(SECRET, "garbage", ttl_seconds=100) is False


# --- login flow ------------------------------------------------------------


def test_flow_round_trips():
    token = issue_flow(SECRET, state="s1", verifier="v1", nonce="n1", next_path="/chat/x")
    flow = verify_flow(SECRET, token)
    assert flow is not None
    assert (flow.state, flow.verifier, flow.nonce, flow.next_path) == ("s1", "v1", "n1", "/chat/x")


def test_flow_rejects_expired():
    token = issue_flow(
        SECRET, state="s1", verifier="v1", nonce="n1", next_path="/chat", ttl_seconds=-1
    )
    assert verify_flow(SECRET, token) is None


def test_flow_rejects_open_redirect_next_path():
    token = issue_flow(
        SECRET, state="s1", verifier="v1", nonce="n1", next_path="https://evil.example/"
    )
    flow = verify_flow(SECRET, token)
    assert flow is not None
    assert flow.next_path == "/chat"  # falls back rather than trusting an external URL


def test_flow_rejects_session_token_by_mistake():
    # A session token handed to verify_flow (wrong cookie in the wrong place)
    # must not be mistaken for a valid flow — the `t` marker guards this.
    identity = Identity(sub="u1", name="N", email=None, groups=(), amr="password")
    session_token = issue_session(SECRET, identity, ttl_seconds=3600)
    assert verify_flow(SECRET, session_token) is None


def test_flow_rejects_missing_fields():
    token = encode_token(SECRET, {"t": "flow", "state": "s1", "exp": int(time.time()) + 60})
    assert verify_flow(SECRET, token) is None
