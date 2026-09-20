"""Tests for the Infisical client and MCP tool (ADR-016)."""

import httpx
import pytest

import registry_mcp.integrations.infisical.tools as infisical_tools
from conftest import IsolatedSettings
from registry_mcp.integrations.infisical import (
    InfisicalClient,
    InfisicalError,
    InfisicalSecretValueLeakedError,
)
from registry_mcp.providers.notification import NullNotificationProvider
from registry_mcp.server import build_server

LOGIN_PATH = "/api/v1/auth/universal-auth/login"
SECRETS_PATH = "/api/v3/secrets/raw"


def _transport(login_response, secrets_response, captured=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        if request.url.path == LOGIN_PATH:
            return httpx.Response(200, json=login_response)
        if request.url.path == SECRETS_PATH:
            return httpx.Response(200, json=secrets_response)
        return httpx.Response(404, json={"message": "not found"})

    return httpx.MockTransport(handler)


LOGIN_OK = {"accessToken": "at_test", "expiresIn": 300}
# Real shape confirmed live against a self-hosted Infisical instance with
# viewSecretValue=false: secretValueHidden is an explicit True, and
# secretValue is the literal placeholder string, not null/omitted.
SECRETS_MASKED = {
    "secrets": [
        {
            "secretKey": "ANSIBLE_INVENTORY_PATH",
            "secretValue": "<hidden-by-infisical>",
            "secretValueHidden": True,
        },
        {
            "secretKey": "GIT_TOKEN",
            "secretValue": "<hidden-by-infisical>",
            "secretValueHidden": True,
        },
    ]
}
SECRETS_LEAKED = {
    "secrets": [
        {
            "secretKey": "ANSIBLE_INVENTORY_PATH",
            "secretValue": "<hidden-by-infisical>",
            "secretValueHidden": True,
        },
        {
            "secretKey": "GIT_TOKEN",
            "secretValue": "ghp_liveTokenValue",
            "secretValueHidden": False,
        },
    ]
}


# --- client ---------------------------------------------------------------


async def test_client_logs_in_and_sends_bearer_token():
    captured: list[httpx.Request] = []
    client = InfisicalClient(
        "http://i", "cid", "csecret", transport=_transport(LOGIN_OK, SECRETS_MASKED, captured)
    )
    await client.list_secret_keys("proj1", "prod", "/svc")
    login_req, secrets_req = captured
    assert login_req.url.path == LOGIN_PATH
    assert secrets_req.headers["Authorization"] == "Bearer at_test"
    assert secrets_req.url.params["workspaceId"] == "proj1"
    assert secrets_req.url.params["environment"] == "prod"
    assert secrets_req.url.params["secretPath"] == "/svc"
    assert secrets_req.url.params["viewSecretValue"] == "false"


async def test_client_returns_key_names_only():
    client = InfisicalClient(
        "http://i", "cid", "csecret", transport=_transport(LOGIN_OK, SECRETS_MASKED)
    )
    keys = await client.list_secret_keys("proj1", "prod", "/svc")
    assert keys == ["ANSIBLE_INVENTORY_PATH", "GIT_TOKEN"]


async def test_client_reuses_cached_token():
    calls = {"login": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == LOGIN_PATH:
            calls["login"] += 1
            return httpx.Response(200, json=LOGIN_OK)
        return httpx.Response(200, json=SECRETS_MASKED)

    client = InfisicalClient("http://i", "cid", "csecret", transport=httpx.MockTransport(handler))
    await client.list_secret_keys("proj1", "prod", "/svc")
    await client.list_secret_keys("proj1", "prod", "/svc")
    assert calls["login"] == 1


async def test_client_relogs_in_after_token_expiry():
    calls = {"login": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == LOGIN_PATH:
            calls["login"] += 1
            return httpx.Response(200, json=LOGIN_OK)
        return httpx.Response(200, json=SECRETS_MASKED)

    client = InfisicalClient("http://i", "cid", "csecret", transport=httpx.MockTransport(handler))
    await client.list_secret_keys("proj1", "prod", "/svc")
    assert calls["login"] == 1
    # Force the cached token to look expired without waiting out expiresIn.
    client._token_expires_at = 0.0
    await client.list_secret_keys("proj1", "prod", "/svc")
    assert calls["login"] == 2


async def test_client_login_failure_raises():
    client = InfisicalClient(
        "http://i",
        "cid",
        "wrong",
        transport=httpx.MockTransport(lambda _r: httpx.Response(401, json={})),
    )
    with pytest.raises(InfisicalError):
        await client.list_secret_keys("proj1", "prod", "/svc")


async def test_client_secrets_error_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == LOGIN_PATH:
            return httpx.Response(200, json=LOGIN_OK)
        return httpx.Response(403, json={})

    client = InfisicalClient("http://i", "cid", "csecret", transport=httpx.MockTransport(handler))
    with pytest.raises(InfisicalError):
        await client.list_secret_keys("proj1", "prod", "/svc")


# --- defensive value-leak gate ---------------------------------------------


async def test_client_raises_leak_error_never_forwarding_value():
    client = InfisicalClient(
        "http://i", "cid", "csecret", transport=_transport(LOGIN_OK, SECRETS_LEAKED)
    )
    with pytest.raises(InfisicalSecretValueLeakedError) as exc_info:
        await client.list_secret_keys("proj1", "prod", "/svc")
    assert exc_info.value.key == "GIT_TOKEN"
    # The exception's own string form must never contain the leaked value.
    assert "ghp_liveTokenValue" not in str(exc_info.value)


async def test_client_raises_leak_error_when_hidden_flag_true_but_value_unrecognized():
    """secretValueHidden alone is not trusted: an unrecognized placeholder
    string (e.g. a future Infisical version renaming it) must still fail
    closed rather than being accepted as new-but-fine."""
    secrets = {
        "secrets": [
            {
                "secretKey": "GIT_TOKEN",
                "secretValue": "<some-other-placeholder>",
                "secretValueHidden": True,
            }
        ]
    }
    client = InfisicalClient("http://i", "cid", "csecret", transport=_transport(LOGIN_OK, secrets))
    with pytest.raises(InfisicalSecretValueLeakedError) as exc_info:
        await client.list_secret_keys("proj1", "prod", "/svc")
    assert exc_info.value.key == "GIT_TOKEN"


async def test_client_raises_leak_error_when_value_masked_but_hidden_flag_false():
    """secretValue being a known placeholder is not trusted alone either:
    secretValueHidden must also be exactly True."""
    secrets = {
        "secrets": [
            {
                "secretKey": "GIT_TOKEN",
                "secretValue": "<hidden-by-infisical>",
                "secretValueHidden": False,
            }
        ]
    }
    client = InfisicalClient("http://i", "cid", "csecret", transport=_transport(LOGIN_OK, secrets))
    with pytest.raises(InfisicalSecretValueLeakedError) as exc_info:
        await client.list_secret_keys("proj1", "prod", "/svc")
    assert exc_info.value.key == "GIT_TOKEN"


# --- tool -------------------------------------------------------------------


class _RecordingNotifier:
    def __init__(self):
        self.sent = []

    async def send(self, title, body, url=None, diff=None):
        self.sent.append({"title": title, "body": body})


@pytest.fixture
def infisical_settings_kwargs(tmp_path):
    return dict(
        registry_db_path=str(tmp_path / "r.db"),
        infisical_enabled=True,
        infisical_base_url="http://infisical.test",
        infisical_client_id="cid",
        infisical_client_secret="csecret",
        infisical_project_id="proj1",
        infisical_environment="prod",
        infisical_secret_path="/homelab-registry-mcp",
    )


def _patch_client(monkeypatch, transport):
    real = infisical_tools.InfisicalClient

    def factory(base_url, client_id, client_secret, **kwargs):
        kwargs["transport"] = transport
        return real(base_url, client_id, client_secret, **kwargs)

    monkeypatch.setattr(infisical_tools, "InfisicalClient", factory)


async def call(server, name, args):
    return (await server.call_tool(name, args))[1]


async def test_tool_returns_keys(infisical_settings_kwargs, monkeypatch):
    _patch_client(monkeypatch, _transport(LOGIN_OK, SECRETS_MASKED))
    server = build_server(IsolatedSettings(**infisical_settings_kwargs))
    result = await call(server, "infisical_status", {})
    assert result["keys"] == ["ANSIBLE_INVENTORY_PATH", "GIT_TOKEN"]
    assert "error" not in result


async def test_tool_never_returns_values(infisical_settings_kwargs, monkeypatch):
    _patch_client(monkeypatch, _transport(LOGIN_OK, SECRETS_MASKED))
    server = build_server(IsolatedSettings(**infisical_settings_kwargs))
    result = await call(server, "infisical_status", {})
    assert result == {"keys": ["ANSIBLE_INVENTORY_PATH", "GIT_TOKEN"]}


async def test_tool_disabled_returns_error(tmp_path):
    server = build_server(IsolatedSettings(registry_db_path=str(tmp_path / "r.db")))
    result = await call(server, "infisical_status", {})
    assert "error" in result


async def test_tool_enabled_but_unconfigured_returns_error(tmp_path):
    server = build_server(
        IsolatedSettings(registry_db_path=str(tmp_path / "r.db"), infisical_enabled=True)
    )
    result = await call(server, "infisical_status", {})
    assert "error" in result


async def test_tool_fails_closed_on_leak_and_never_returns_value(
    infisical_settings_kwargs, monkeypatch
):
    _patch_client(monkeypatch, _transport(LOGIN_OK, SECRETS_LEAKED))
    server = build_server(IsolatedSettings(**infisical_settings_kwargs))
    result = await call(server, "infisical_status", {})
    assert "keys" not in result
    assert "error" in result
    assert "ghp_liveTokenValue" not in str(result)


async def test_tool_leak_fires_urgent_notification_naming_only_the_key(
    infisical_settings_kwargs, monkeypatch
):
    _patch_client(monkeypatch, _transport(LOGIN_OK, SECRETS_LEAKED))
    notifier = _RecordingNotifier()
    # register_infisical_tools receives its notifier at build_server() time,
    # constructed via build_notification_provider(settings) in server.py --
    # patch it there, since the notifier is built once at startup, not
    # per-call.
    import registry_mcp.server as server_module

    monkeypatch.setattr(server_module, "build_notification_provider", lambda _s: notifier)
    server = build_server(IsolatedSettings(**infisical_settings_kwargs))
    await call(server, "infisical_status", {})
    assert len(notifier.sent) == 1
    assert "GIT_TOKEN" in notifier.sent[0]["body"]
    assert "ghp_liveTokenValue" not in notifier.sent[0]["body"]
    assert "ghp_liveTokenValue" not in notifier.sent[0]["title"]


async def test_null_notification_provider_is_default(infisical_settings_kwargs, monkeypatch):
    """Sanity check: with no NOTIFICATION_PROVIDER configured, the leak path
    still completes (via NullNotificationProvider) instead of raising."""
    _patch_client(monkeypatch, _transport(LOGIN_OK, SECRETS_LEAKED))
    server = build_server(IsolatedSettings(**infisical_settings_kwargs))
    result = await call(server, "infisical_status", {})
    assert "error" in result
    assert isinstance(NullNotificationProvider(), NullNotificationProvider)
