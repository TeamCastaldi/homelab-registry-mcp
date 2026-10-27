"""/mcp as an OAuth resource server, end to end through the real app.

The app is the one `main()` serves: `build_app` with the MCP_AUTH_* settings,
then `http_app`. The only stand-in is Authentik itself (`auth_fakes`), reached
through the verifier's HTTP transport, so every token here is checked by the
same middleware, verifier and group gate a real request meets.
"""

import json
import time

import pytest
from starlette.testclient import TestClient

from auth_fakes import AUDIENCE, GROUP, ISSUER, RESOURCE, FakeAuthentik
from conftest import IsolatedSettings
from registry_mcp.server import build_app, http_app

METADATA_URL = "https://registry-mcp.example.com/.well-known/oauth-protected-resource/mcp"
HOST = {"Host": "localhost"}
ACCEPT = {"Accept": "application/json, text/event-stream"}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}


@pytest.fixture
def authentik():
    return FakeAuthentik()


@pytest.fixture
def client(authentik):
    settings = IsolatedSettings(
        registry_db_path=":memory:",
        mcp_auth_enabled=True,
        mcp_auth_issuer_url=ISSUER,
        mcp_auth_audience=AUDIENCE,
        mcp_auth_required_group=GROUP,
        mcp_auth_resource_url=RESOURCE,
        mcp_auth_scopes="openid profile offline_access",
    )
    server, runtime = build_app(settings)
    runtime.auth.verifier._transport = authentik.transport()  # Authentik is the only fake
    with TestClient(http_app(server, runtime, None)) as test_client:
        yield test_client


def bearer(token):
    return {"Authorization": f"Bearer {token}", **HOST, **ACCEPT}


def rpc_result(response):
    """The JSON-RPC result from a JSON or a one-event SSE response."""
    if response.headers["content-type"].startswith("application/json"):
        return response.json()["result"]
    data = [line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")]
    return json.loads(data[-1])["result"]


def initialize(client, token):
    response = client.post("/mcp", json=INITIALIZE, headers=bearer(token))
    assert response.status_code == 200, response.text
    session = response.headers["mcp-session-id"]
    client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={**bearer(token), "mcp-session-id": session},
    )
    return session


def assert_401_pointing_at_metadata(response):
    assert response.status_code == 401
    assert f'resource_metadata="{METADATA_URL}"' in response.headers["www-authenticate"]


def test_no_token_is_401_with_a_pointer_to_the_metadata(client):
    assert_401_pointing_at_metadata(
        client.post("/mcp", json=INITIALIZE, headers={**HOST, **ACCEPT})
    )


@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_every_method_on_mcp_needs_a_token(client, method):
    assert_401_pointing_at_metadata(client.request(method, "/mcp", headers={**HOST, **ACCEPT}))


@pytest.mark.parametrize(
    "claims",
    [
        pytest.param({"aud": "some-other-client"}, id="wrong-audience"),
        pytest.param({"exp": int(time.time()) - 120}, id="expired"),
        pytest.param({"iss": "https://sso.example.com/application/o/other/"}, id="wrong-issuer"),
    ],
)
def test_a_bad_token_is_401(client, authentik, claims):
    response = client.post("/mcp", json=INITIALIZE, headers=bearer(authentik.token(**claims)))
    assert_401_pointing_at_metadata(response)


def test_a_token_signed_by_another_key_is_401(client):
    response = client.post("/mcp", json=INITIALIZE, headers=bearer(FakeAuthentik().token()))
    assert_401_pointing_at_metadata(response)


@pytest.mark.parametrize("scheme", ["Basic", "bearer-less", "Token"])
def test_a_token_in_any_other_scheme_is_401(client, authentik, scheme):
    value = authentik.token() if scheme == "bearer-less" else f"{scheme} {authentik.token()}"
    response = client.post(
        "/mcp", json=INITIALIZE, headers={"Authorization": value, **HOST, **ACCEPT}
    )
    assert response.status_code == 401


def test_a_valid_token_outside_the_group_is_403(client, authentik):
    token = authentik.token(groups=["family"])
    response = client.post("/mcp", json=INITIALIZE, headers=bearer(token))
    assert response.status_code == 403
    assert response.json()["error"] == "insufficient_scope"
    assert f'resource_metadata="{METADATA_URL}"' in response.headers["www-authenticate"]


def test_a_valid_token_initializes_and_lists_tools(client, authentik):
    token = authentik.token()
    session = initialize(client, token)
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        headers={**bearer(token), "mcp-session-id": session},
    )
    assert response.status_code == 200
    names = {tool["name"] for tool in rpc_result(response)["tools"]}
    assert {"config_status", "registry_list_services", "system_health_check"} <= names


def test_a_session_cannot_be_used_with_someone_elses_token(client, authentik):
    """The SDK binds a session to the identity that created it; a second valid
    user in the group can't pick it up."""
    session = initialize(client, authentik.token(sub="nathan"))

    def list_tools_as(sub):
        return client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers={**bearer(authentik.token(sub=sub)), "mcp-session-id": session},
        )

    assert list_tools_as("nathan").status_code == 200
    assert list_tools_as("someone-else").status_code == 404


def test_a_session_stops_working_once_its_token_expires(client, authentik):
    session = initialize(client, authentik.token())
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        headers={**bearer(authentik.token(exp=int(time.time()) - 120)), "mcp-session-id": session},
    )
    assert_401_pointing_at_metadata(response)


@pytest.mark.parametrize(
    "path",
    ["/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"],
)
def test_the_metadata_is_open_and_tells_a_client_where_and_what_to_ask(client, path):
    response = client.get(path, headers=HOST)
    assert response.status_code == 200
    body = response.json()
    assert body["resource"] == RESOURCE
    assert body["authorization_servers"] == [ISSUER]
    assert body["scopes_supported"] == ["openid", "profile", "offline_access"]


def test_health_is_open(client):
    assert client.get("/health", headers=HOST).json() == {"status": "ok"}


def test_only_health_and_the_metadata_are_open(client):
    """Every route the app serves, asked with no credential of any kind: only the
    two open ones may answer 2xx. (The webhooks and approval links keep their own
    credentials and are registered only when configured; test_mcp_auth_webhooks
    covers them with those features on.)"""
    open_paths = {
        "/health",
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
    }
    app = client.app
    for route in app.routes:
        path = getattr(route, "path", None)
        if path is None or path in open_paths:
            continue
        for method in sorted(getattr(route, "methods", None) or {"GET", "POST"}):
            if method == "HEAD":
                continue
            response = client.request(method, path, headers={**HOST, **ACCEPT})
            assert response.status_code >= 400, (method, path, response.status_code)
