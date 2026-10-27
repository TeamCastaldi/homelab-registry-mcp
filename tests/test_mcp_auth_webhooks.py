"""With OAuth on /mcp, the webhooks and approval links keep their own
credentials and stay outside OAuth (ADR-024).

The app has every route mounted: MCP OAuth, the Dockhand webhook, the PatchMon
webhook and its approval links. Each receiver must reject a request without its
own credential, accept one with it and no OAuth token, and never treat an OAuth
token as its credential. The approval links must work with no login at all,
the way they're opened from a phone.
"""

import time

import httpx
import pytest

from auth_fakes import AUDIENCE, GROUP, ISSUER, RESOURCE, FakeAuthentik
from registry_mcp.server import build_app, http_app
from test_patchmon_webhook import (  # noqa: F401 - fixtures used by name
    FakeSMTP,
    _links,
    _path,
    _payload,
    _settings,
    _sign,
    _token,
    ansible,
    fake_smtp,
)

DOCKHAND_SECRET = "dockhand-webhook-secret"
DOCKHAND_ALERT = {
    "event": "update_available",
    "container": "ghost",  # unknown container: acknowledged with 200, nothing opened
    "current_image": "ghost:5.0.0",
    "latest_image": "ghost:5.0.1",
    "server": "workload-01",
}
OPEN_PATHS = {
    "/health",
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/mcp",
}


@pytest.fixture
def authentik():
    return FakeAuthentik()


@pytest.fixture
def app(tmp_path, authentik):
    settings = _settings(
        tmp_path,
        mcp_auth_enabled=True,
        mcp_auth_issuer_url=ISSUER,
        mcp_auth_audience=AUDIENCE,
        mcp_auth_required_group=GROUP,
        mcp_auth_resource_url=RESOURCE,
        dockhand_webhook_enabled=True,
        dockhand_webhook_secret=DOCKHAND_SECRET,
        git_base_url="https://git.test",
        git_token="tok",
        git_repo="nathan/homelab",
    )
    server, runtime = build_app(settings)
    runtime.auth.verifier._transport = authentik.transport()
    return http_app(server, runtime, None)


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://registry.test") as c:
        yield c


def oauth(authentik):
    return {"Authorization": f"Bearer {authentik.token()}"}


# --- Dockhand: a shared bearer secret (Dockhand doesn't sign bodies) ---


async def test_dockhand_without_its_secret_is_403(client):
    assert (await client.post("/webhooks/dockhand", json=DOCKHAND_ALERT)).status_code == 403


async def test_dockhand_with_a_wrong_secret_is_403(client):
    response = await client.post(
        "/webhooks/dockhand", json=DOCKHAND_ALERT, headers={"Authorization": "Bearer nope"}
    )
    assert response.status_code == 403


@pytest.mark.parametrize("header", ["Authorization", "X-Dockhand-Token"])
async def test_dockhand_with_its_secret_needs_no_oauth_token(client, header):
    value = f"Bearer {DOCKHAND_SECRET}" if header == "Authorization" else DOCKHAND_SECRET
    response = await client.post("/webhooks/dockhand", json=DOCKHAND_ALERT, headers={header: value})
    assert response.status_code == 200


async def test_an_oauth_token_is_not_the_dockhand_secret(client, authentik):
    response = await client.post(
        "/webhooks/dockhand", json=DOCKHAND_ALERT, headers=oauth(authentik)
    )
    assert response.status_code == 403


# --- PatchMon: HMAC-SHA256 of the body ---


async def _patchmon(client, body, headers):
    return await client.post(
        "/webhooks/patchmon",
        content=body,
        headers={"content-type": "application/json", **headers},
    )


async def test_patchmon_unsigned_is_401(client):
    body = httpx.Request("POST", "/", json=_payload()).content
    assert (await _patchmon(client, body, {})).status_code == 401
    assert FakeSMTP.sent == []


async def test_patchmon_signed_with_the_wrong_secret_is_401(client):
    body = httpx.Request("POST", "/", json=_payload()).content
    headers = {"X-PatchMon-Signature": _sign(body, "wrong-secret")}
    assert (await _patchmon(client, body, headers)).status_code == 401


async def test_an_oauth_token_is_not_a_patchmon_signature(client, authentik):
    body = httpx.Request("POST", "/", json=_payload()).content
    assert (await _patchmon(client, body, oauth(authentik))).status_code == 401
    assert FakeSMTP.sent == []


async def test_patchmon_signed_needs_no_oauth_token(client):
    body = httpx.Request("POST", "/", json=_payload()).content
    response = await _patchmon(client, body, {"X-PatchMon-Signature": _sign(body)})
    assert response.status_code == 202
    assert len(FakeSMTP.sent) == 1


# --- approval links: the emailed token is the credential, no login ---


async def _approve_link(client):
    body = httpx.Request("POST", "/", json=_payload()).content
    response = await _patchmon(client, body, {"X-PatchMon-Signature": _sign(body)})
    assert response.status_code == 202
    return _links(FakeSMTP.sent[-1])[0]


async def test_an_approval_link_works_without_a_login_and_only_once(client, ansible):  # noqa: F811
    approve = await _approve_link(client)
    page = await client.get(_path(approve))
    assert page.status_code == 200
    assert "<form method='post'>" in page.text
    confirmed = await client.post(_path(approve), data={"token": _token(approve)})
    assert confirmed.status_code == 200
    replayed = await client.post(_path(approve), data={"token": _token(approve)})
    assert replayed.status_code == 409
    assert len(ansible.playbook_runs) == 1


async def test_an_approval_link_without_its_token_does_nothing(client, ansible, authentik):  # noqa: F811
    await _approve_link(client)
    for headers in ({}, oauth(authentik)):
        response = await client.post("/patch/approve", data={"token": "x" * 43}, headers=headers)
        assert response.status_code == 404
    assert ansible.playbook_runs == []


# --- the whole app, nothing presented ---


async def test_only_health_and_the_metadata_answer_without_any_credential(client, app):
    probed = []
    for route in app.routes:
        path = getattr(route, "path", None)
        if path is None or path in OPEN_PATHS:
            continue
        for method in sorted((getattr(route, "methods", None) or {"GET", "POST"}) - {"HEAD"}):
            response = await client.request(method, path)
            probed.append((method, path, response.status_code))
            assert response.status_code >= 400, (method, path, response.status_code)
    assert {path for _m, path, _s in probed} >= {
        "/mcp",
        "/webhooks/dockhand",
        "/webhooks/patchmon",
        "/patch/approve",
        "/patch/cancel",
    }


async def test_mcp_itself_is_still_oauth_only_with_the_webhooks_on(client, authentik):
    no_token = await client.post("/mcp", json={}, headers={"Host": "localhost"})
    assert no_token.status_code == 401
    secret_as_token = await client.post(
        "/mcp", json={}, headers={"Host": "localhost", "Authorization": f"Bearer {DOCKHAND_SECRET}"}
    )
    assert secret_as_token.status_code == 401
    expired = await client.post(
        "/mcp",
        json={},
        headers={
            "Host": "localhost",
            "Authorization": f"Bearer {authentik.token(exp=int(time.time()) - 120)}",
        },
    )
    assert expired.status_code == 401
