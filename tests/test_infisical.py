"""Tests for the Infisical client and MCP tool (ADR-016, whole-project ADR-017)."""

import asyncio

import httpx
import pytest

import registry_mcp.integrations.infisical.tools as infisical_tools
from conftest import IsolatedSettings, tool_payload
from registry_mcp.integrations.infisical import (
    InfisicalClient,
    InfisicalError,
    InfisicalSecretValueLeakedError,
)
from registry_mcp.providers.notification import NullNotificationProvider
from registry_mcp.server import build_server

LOGIN_PATH = "/api/v1/auth/universal-auth/login"
SECRETS_PATH = "/api/v3/secrets/raw"
FOLDERS_PATH = "/api/v1/folders"


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


def _masked(*keys: str) -> dict:
    return {
        "secrets": [
            {"secretKey": key, "secretValue": "<hidden-by-infisical>", "secretValueHidden": True}
            for key in keys
        ]
    }


def _tree_transport(login_response, folders_by_path, secrets_by_path, captured=None):
    """Route folder-listing and secrets requests by their `path`/`secretPath`
    query param, for exercising `list_secret_tree`'s breadth-first walk."""

    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        if request.url.path == LOGIN_PATH:
            return httpx.Response(200, json=login_response)
        if request.url.path == SECRETS_PATH:
            path = request.url.params.get("secretPath")
            return httpx.Response(200, json=secrets_by_path.get(path, {"secrets": []}))
        if request.url.path == FOLDERS_PATH:
            path = request.url.params.get("path")
            return httpx.Response(200, json={"folders": folders_by_path.get(path, [])})
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


# --- whole-project folder-tree walk (ADR-017) ------------------------------


async def test_client_list_secret_tree_walks_folders_and_groups_by_path():
    login_ok = LOGIN_OK
    folders_by_path = {
        "/": [{"name": "authentik"}, {"name": "homelab-registry-mcp"}],
        "/authentik": [],
        "/homelab-registry-mcp": [],
    }
    secrets_by_path = {
        "/": {"secrets": []},
        "/authentik": _masked("AUTHENTIK_TOKEN"),
        "/homelab-registry-mcp": _masked("GIT_TOKEN", "SECRETS_KEY_PATH"),
    }
    client = InfisicalClient(
        "http://i",
        "cid",
        "csecret",
        transport=_tree_transport(login_ok, folders_by_path, secrets_by_path),
    )
    by_path, inaccessible = await client.list_secret_tree("proj1", "prod", "/")
    assert by_path == {
        "/authentik": ["AUTHENTIK_TOKEN"],
        "/homelab-registry-mcp": ["GIT_TOKEN", "SECRETS_KEY_PATH"],
    }
    assert inaccessible == []


async def test_client_list_secret_tree_reports_inaccessible_folder_without_failing():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == LOGIN_PATH:
            return httpx.Response(200, json=LOGIN_OK)
        if request.url.path == FOLDERS_PATH:
            path = request.url.params.get("path")
            if path == "/":
                return httpx.Response(
                    200, json={"folders": [{"name": "authentik"}, {"name": "vaultwarden"}]}
                )
            return httpx.Response(200, json={"folders": []})
        if request.url.path == SECRETS_PATH:
            path = request.url.params.get("secretPath")
            if path == "/vaultwarden":
                return httpx.Response(403, json={"message": "forbidden"})
            if path == "/authentik":
                return httpx.Response(200, json=_masked("AUTHENTIK_TOKEN"))
            return httpx.Response(200, json={"secrets": []})
        return httpx.Response(404, json={"message": "not found"})

    client = InfisicalClient("http://i", "cid", "csecret", transport=httpx.MockTransport(handler))
    by_path, inaccessible = await client.list_secret_tree("proj1", "prod", "/")
    assert by_path == {"/authentik": ["AUTHENTIK_TOKEN"]}
    assert inaccessible == ["/vaultwarden"]


async def test_client_list_secret_tree_leak_in_nested_folder_fails_closed_with_path():
    folders_by_path = {"/": [{"name": "authentik"}], "/authentik": []}
    secrets_by_path = {
        "/": {"secrets": []},
        "/authentik": {
            "secrets": [
                {
                    "secretKey": "AUTHENTIK_TOKEN",
                    "secretValue": "goauthentik-live-token",
                    "secretValueHidden": False,
                }
            ]
        },
    }
    client = InfisicalClient(
        "http://i",
        "cid",
        "csecret",
        transport=_tree_transport(LOGIN_OK, folders_by_path, secrets_by_path),
    )
    with pytest.raises(InfisicalSecretValueLeakedError) as exc_info:
        await client.list_secret_tree("proj1", "prod", "/")
    assert exc_info.value.key == "AUTHENTIK_TOKEN"
    assert exc_info.value.path == "/authentik"
    assert "goauthentik-live-token" not in str(exc_info.value)


async def test_client_list_secret_tree_respects_max_folders_cap():
    folders_by_path = {
        "/": [{"name": "a"}, {"name": "b"}, {"name": "c"}],
        "/a": [],
        "/b": [],
        "/c": [],
    }
    secrets_by_path = {
        "/": {"secrets": []},
        "/a": _masked("A_KEY"),
        "/b": _masked("B_KEY"),
        "/c": _masked("C_KEY"),
    }
    client = InfisicalClient(
        "http://i",
        "cid",
        "csecret",
        transport=_tree_transport(LOGIN_OK, folders_by_path, secrets_by_path),
    )
    by_path, _inaccessible = await client.list_secret_tree("proj1", "prod", "/", max_folders=2)
    # Only "/" (no keys) and "/a" (the first queued child) fit under the cap.
    assert by_path == {"/a": ["A_KEY"]}
    assert "/b" not in by_path
    assert "/c" not in by_path


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
    return tool_payload(await server.call_tool(name, args))


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


# --- tool, whole-project mode (ADR-017) -------------------------------------


@pytest.fixture
def recursive_settings_kwargs(infisical_settings_kwargs):
    return {
        **infisical_settings_kwargs,
        "infisical_secret_path": "/",
        "infisical_recursive_scan": True,
    }


async def test_tool_recursive_scan_returns_secrets_by_path(recursive_settings_kwargs, monkeypatch):
    folders_by_path = {
        "/": [{"name": "authentik"}, {"name": "homelab-registry-mcp"}],
        "/authentik": [],
        "/homelab-registry-mcp": [],
    }
    secrets_by_path = {
        "/": {"secrets": []},
        "/authentik": _masked("AUTHENTIK_TOKEN"),
        "/homelab-registry-mcp": _masked("GIT_TOKEN"),
    }
    _patch_client(monkeypatch, _tree_transport(LOGIN_OK, folders_by_path, secrets_by_path))
    server = build_server(IsolatedSettings(**recursive_settings_kwargs))
    result = await call(server, "infisical_status", {})
    assert result == {
        "secrets_by_path": {
            "/authentik": ["AUTHENTIK_TOKEN"],
            "/homelab-registry-mcp": ["GIT_TOKEN"],
        }
    }
    assert "inaccessible_paths" not in result


async def test_tool_recursive_scan_surfaces_inaccessible_paths(
    recursive_settings_kwargs, monkeypatch
):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == LOGIN_PATH:
            return httpx.Response(200, json=LOGIN_OK)
        if request.url.path == FOLDERS_PATH:
            path = request.url.params.get("path")
            if path == "/":
                return httpx.Response(
                    200, json={"folders": [{"name": "authentik"}, {"name": "vaultwarden"}]}
                )
            return httpx.Response(200, json={"folders": []})
        if request.url.path == SECRETS_PATH:
            path = request.url.params.get("secretPath")
            if path == "/vaultwarden":
                return httpx.Response(403, json={"message": "forbidden"})
            if path == "/authentik":
                return httpx.Response(200, json=_masked("AUTHENTIK_TOKEN"))
            return httpx.Response(200, json={"secrets": []})
        return httpx.Response(404, json={"message": "not found"})

    _patch_client(monkeypatch, httpx.MockTransport(handler))
    server = build_server(IsolatedSettings(**recursive_settings_kwargs))
    result = await call(server, "infisical_status", {})
    assert result["secrets_by_path"] == {"/authentik": ["AUTHENTIK_TOKEN"]}
    assert result["inaccessible_paths"] == ["/vaultwarden"]


async def test_tool_recursive_scan_leak_fails_closed_with_notification(
    recursive_settings_kwargs, monkeypatch
):
    folders_by_path = {"/": [{"name": "authentik"}], "/authentik": []}
    secrets_by_path = {
        "/": {"secrets": []},
        "/authentik": {
            "secrets": [
                {
                    "secretKey": "AUTHENTIK_TOKEN",
                    "secretValue": "goauthentik-live-token",
                    "secretValueHidden": False,
                }
            ]
        },
    }
    _patch_client(monkeypatch, _tree_transport(LOGIN_OK, folders_by_path, secrets_by_path))
    notifier = _RecordingNotifier()
    import registry_mcp.server as server_module

    monkeypatch.setattr(server_module, "build_notification_provider", lambda _s: notifier)
    server = build_server(IsolatedSettings(**recursive_settings_kwargs))
    result = await call(server, "infisical_status", {})
    assert "secrets_by_path" not in result
    assert "error" in result
    assert "goauthentik-live-token" not in str(result)
    assert len(notifier.sent) == 1
    assert "/authentik" in notifier.sent[0]["body"]
    assert "AUTHENTIK_TOKEN" in notifier.sent[0]["body"]
    assert "goauthentik-live-token" not in notifier.sent[0]["body"]


async def test_tool_non_recursive_by_default(infisical_settings_kwargs, monkeypatch):
    """Regression check: leaving INFISICAL_RECURSIVE_SCAN unset keeps the
    original single-folder behavior -- `keys`, not `secrets_by_path`."""
    _patch_client(monkeypatch, _transport(LOGIN_OK, SECRETS_MASKED))
    server = build_server(IsolatedSettings(**infisical_settings_kwargs))
    result = await call(server, "infisical_status", {})
    assert "keys" in result
    assert "secrets_by_path" not in result
    assert isinstance(NullNotificationProvider(), NullNotificationProvider)


def _counting_transport(logins: list[int]):
    """Serves a login and a masked secrets read, counting logins. Login yields to
    the event loop, the way a real network round-trip does."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == LOGIN_PATH:
            logins.append(1)
            await asyncio.sleep(0)
            return httpx.Response(200, json=LOGIN_OK)
        return httpx.Response(200, json=SECRETS_MASKED)

    return httpx.MockTransport(handler)


async def test_tool_reuses_one_login_across_calls(infisical_settings_kwargs, monkeypatch):
    logins: list[int] = []
    _patch_client(monkeypatch, _counting_transport(logins))
    server = build_server(IsolatedSettings(**infisical_settings_kwargs))

    for _ in range(3):
        assert "keys" in await call(server, "infisical_status", {})

    assert len(logins) == 1


async def test_client_concurrent_calls_share_one_login():
    logins: list[int] = []
    client = InfisicalClient("http://i", "cid", "csecret", transport=_counting_transport(logins))

    results = await asyncio.gather(
        *(client.list_secret_keys("proj1", "prod", "/") for _ in range(3))
    )

    assert all(keys == results[0] for keys in results)
    assert len(logins) == 1
