"""Tests for chat authentication (registry_mcp.chat.auth)."""

import httpx
import pytest

from conftest import IsolatedSettings
from registry_mcp.chat.auth import (
    LoginAttemptLimiter,
    OidcError,
    build_authorize_url,
    check_password,
    discover_oidc,
    exchange_code,
    fetch_userinfo,
    generate_pkce,
    groups_allowed,
    identity_from_userinfo,
    resolve_mode,
)
from registry_mcp.chat.auth import (
    _discovery_cache as discovery_cache,
)

OIDC_SETTINGS = dict(
    chat_oidc_issuer="https://sso.example/application/o/chat/",
    chat_oidc_client_id="client-1",
    chat_oidc_client_secret="secret-1",
    chat_oidc_redirect_url="https://registry-mcp.example/chat/auth/callback",
)


def _transport(handler):
    return httpx.MockTransport(handler)


# --- resolve_mode ------------------------------------------------------------


def test_resolve_mode_disabled_when_nothing_configured():
    settings = IsolatedSettings(registry_db_path=":memory:")
    assert resolve_mode(settings) == "disabled"


def test_resolve_mode_password_when_only_password_set():
    settings = IsolatedSettings(registry_db_path=":memory:", chat_password="hunter2")
    assert resolve_mode(settings) == "password"


def test_resolve_mode_oidc_when_fully_configured():
    settings = IsolatedSettings(registry_db_path=":memory:", **OIDC_SETTINGS)
    assert resolve_mode(settings) == "oidc"


def test_resolve_mode_oidc_wins_over_password_when_both_set():
    settings = IsolatedSettings(
        registry_db_path=":memory:", chat_password="hunter2", **OIDC_SETTINGS
    )
    assert resolve_mode(settings) == "oidc"


def test_resolve_mode_disabled_when_oidc_partially_configured():
    settings = IsolatedSettings(
        registry_db_path=":memory:",
        chat_oidc_issuer="https://sso.example/application/o/chat/",
        chat_oidc_client_id="client-1",
        # client_secret and redirect_url missing
    )
    assert resolve_mode(settings) == "disabled"


# --- discover_oidc -------------------------------------------------------------


async def test_discover_oidc_parses_endpoints():
    discovery_cache.clear()
    issuer = "https://sso.example/application/o/chat/"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/.well-known/openid-configuration")
        return httpx.Response(
            200,
            json={
                "authorization_endpoint": "https://sso.example/authorize",
                "token_endpoint": "https://sso.example/token",
                "userinfo_endpoint": "https://sso.example/userinfo",
            },
        )

    endpoints = await discover_oidc(issuer, transport=_transport(handler))
    assert endpoints.authorization_endpoint == "https://sso.example/authorize"
    assert endpoints.token_endpoint == "https://sso.example/token"
    assert endpoints.userinfo_endpoint == "https://sso.example/userinfo"


async def test_discover_oidc_caches_by_issuer():
    discovery_cache.clear()
    issuer = "https://sso.example/application/o/chat/"
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(
            200,
            json={
                "authorization_endpoint": "a",
                "token_endpoint": "b",
                "userinfo_endpoint": "c",
            },
        )

    transport = _transport(handler)
    await discover_oidc(issuer, transport=transport)
    await discover_oidc(issuer, transport=transport)
    assert calls["n"] == 1


async def test_discover_oidc_missing_field_raises():
    discovery_cache.clear()

    def handler(_request):
        return httpx.Response(200, json={"authorization_endpoint": "a"})

    with pytest.raises(OidcError):
        await discover_oidc("https://sso.example/app/", transport=_transport(handler))


async def test_discover_oidc_4xx_fails_fast():
    discovery_cache.clear()
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(404)

    with pytest.raises(OidcError):
        await discover_oidc("https://sso.example/app2/", transport=_transport(handler))
    assert calls["n"] == 1


# --- PKCE + authorize URL -------------------------------------------------------


def test_generate_pkce_produces_verifier_and_matching_challenge():
    import base64
    import hashlib

    verifier, challenge = generate_pkce()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    expected = expected.rstrip(b"=").decode("ascii")
    assert challenge == expected


def test_build_authorize_url_uses_configured_redirect_not_host_header():
    from registry_mcp.chat.auth import OidcEndpoints

    settings = IsolatedSettings(registry_db_path=":memory:", **OIDC_SETTINGS)
    endpoints = OidcEndpoints(
        authorization_endpoint="https://sso.example/authorize",
        token_endpoint="https://sso.example/token",
        userinfo_endpoint="https://sso.example/userinfo",
    )
    url = build_authorize_url(endpoints, settings, state="s1", code_challenge="c1")
    assert url.startswith("https://sso.example/authorize?")
    assert "redirect_uri=https%3A%2F%2Fregistry-mcp.example%2Fchat%2Fauth%2Fcallback" in url
    assert "code_challenge=c1" in url
    assert "code_challenge_method=S256" in url
    assert "state=s1" in url


# --- exchange_code / fetch_userinfo --------------------------------------------


async def test_exchange_code_posts_expected_form(monkeypatch):
    from registry_mcp.chat.auth import OidcEndpoints

    settings = IsolatedSettings(registry_db_path=":memory:", **OIDC_SETTINGS)
    endpoints = OidcEndpoints(
        authorization_endpoint="https://sso.example/authorize",
        token_endpoint="https://sso.example/token",
        userinfo_endpoint="https://sso.example/userinfo",
    )
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"access_token": "at-1", "token_type": "Bearer"})

    result = await exchange_code(
        endpoints,
        settings,
        code="code-1",
        code_verifier="verifier-1",
        transport=_transport(handler),
    )
    assert result["access_token"] == "at-1"
    assert "grant_type=authorization_code" in seen["body"]
    assert "client_secret=secret-1" in seen["body"]
    assert "code_verifier=verifier-1" in seen["body"]


async def test_exchange_code_4xx_raises():
    from registry_mcp.chat.auth import OidcEndpoints

    settings = IsolatedSettings(registry_db_path=":memory:", **OIDC_SETTINGS)
    endpoints = OidcEndpoints(
        authorization_endpoint="a",
        token_endpoint="https://sso.example/token",
        userinfo_endpoint="c",
    )

    def handler(_request):
        return httpx.Response(400, json={"error": "invalid_grant"})

    with pytest.raises(OidcError):
        await exchange_code(
            endpoints,
            settings,
            code="bad",
            code_verifier="v",
            transport=_transport(handler),
        )


async def test_fetch_userinfo_sends_bearer_token():
    from registry_mcp.chat.auth import OidcEndpoints

    endpoints = OidcEndpoints(
        authorization_endpoint="a",
        token_endpoint="b",
        userinfo_endpoint="https://sso.example/userinfo",
    )
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200, json={"sub": "u1", "email": "n@example.com", "groups": ["admins"]}
        )

    userinfo = await fetch_userinfo(endpoints, access_token="at-1", transport=_transport(handler))
    assert seen["auth"] == "Bearer at-1"
    assert userinfo["sub"] == "u1"


def test_identity_from_userinfo_maps_claims():
    identity = identity_from_userinfo(
        {"sub": "u1", "name": "Nathan", "email": "n@example.com", "groups": ["admins", "family"]}
    )
    assert identity.sub == "u1"
    assert identity.name == "Nathan"
    assert identity.email == "n@example.com"
    assert identity.groups == ("admins", "family")
    assert identity.amr == "oidc"


def test_identity_from_userinfo_falls_back_to_preferred_username():
    identity = identity_from_userinfo({"sub": "u1", "preferred_username": "nathan"})
    assert identity.name == "nathan"
    assert identity.groups == ()


# --- groups_allowed --------------------------------------------------------


def test_groups_allowed_unset_allows_any_authenticated_user():
    settings = IsolatedSettings(registry_db_path=":memory:")
    assert groups_allowed(settings, ()) is True
    assert groups_allowed(settings, ("randomgroup",)) is True


def test_groups_allowed_restricts_to_configured_groups():
    settings = IsolatedSettings(
        registry_db_path=":memory:", chat_oidc_allowed_groups="admins, family"
    )
    assert groups_allowed(settings, ("family",)) is True
    assert groups_allowed(settings, ("guests",)) is False
    assert groups_allowed(settings, ()) is False


# --- check_password -------------------------------------------------------


def test_check_password_matches():
    settings = IsolatedSettings(registry_db_path=":memory:", chat_password="hunter2")
    assert check_password(settings, "hunter2") is True
    assert check_password(settings, "wrong") is False


def test_check_password_false_when_unset():
    settings = IsolatedSettings(registry_db_path=":memory:")
    assert check_password(settings, "anything") is False


# --- LoginAttemptLimiter ----------------------------------------------------


def test_login_attempt_limiter_blocks_after_threshold():
    limiter = LoginAttemptLimiter(max_attempts=3, window_seconds=60)
    key = "1.2.3.4"
    assert limiter.is_blocked(key) is False
    for _ in range(3):
        limiter.record_failure(key)
    assert limiter.is_blocked(key) is True


def test_login_attempt_limiter_success_clears_failures():
    limiter = LoginAttemptLimiter(max_attempts=2, window_seconds=60)
    key = "1.2.3.4"
    limiter.record_failure(key)
    limiter.record_failure(key)
    assert limiter.is_blocked(key) is True
    limiter.record_success(key)
    assert limiter.is_blocked(key) is False


def test_login_attempt_limiter_keys_are_independent():
    limiter = LoginAttemptLimiter(max_attempts=1, window_seconds=60)
    limiter.record_failure("1.2.3.4")
    assert limiter.is_blocked("1.2.3.4") is True
    assert limiter.is_blocked("5.6.7.8") is False
