"""Tests for the Authentik client, tools, resource, and audit prompt."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

import registry_mcp.integrations.authentik.tools as authentik_tools
from conftest import IsolatedSettings, tool_payload
from http_fakes import strict_transport
from registry_mcp.integrations.authentik import AuthentikClient, AuthentikError
from registry_mcp.server import build_server

_NOW = datetime.now(UTC)


def _paginated(results):
    return {"pagination": {"count": len(results)}, "results": results}


# `applications/` is keyed with its query string: `list_applications` always
# sends `superuser_full_list=true` (U1) so the service account sees every
# application, not just ones it has per-user access to — a client that
# dropped that param would 404 here instead of matching a looser route.
ROUTES = {
    "GET /api/v3/core/applications/?superuser_full_list=true": _paginated(
        [{"slug": "vaultwarden", "name": "Vaultwarden", "provider": 1}]
    ),
    "GET /api/v3/core/applications/vaultwarden/": {
        "slug": "vaultwarden",
        "name": "Vaultwarden",
        "provider": 1,
    },
    "GET /api/v3/providers/all/": _paginated([{"pk": 1, "name": "vaultwarden-proxy"}]),
    "GET /api/v3/outposts/instances/": _paginated([{"pk": "out-1", "name": "embedded-outpost"}]),
    "GET /api/v3/outposts/instances/out-1/health/": [
        {"version": "2024.1", "version_outdated": False}
    ],
    "GET /api/v3/policies/all/": _paginated([{"pk": "p1", "name": "deny-after-hours"}]),
    "GET /api/v3/core/users/": _paginated([{"pk": 7, "username": "nathan"}]),
    "GET /api/v3/core/groups/": _paginated([{"pk": "g1", "name": "admins"}]),
    "GET /api/v3/events/events/?ordering=-created&page_size=100": _paginated(
        [
            {"action": "login", "created": _NOW.isoformat()},
            {"action": "login_failed", "created": (_NOW - timedelta(days=3)).isoformat()},
        ]
    ),
}

BASE = "https://auth.test/api/v3"


def _transport(routes, captured=None):
    return strict_transport(routes, captured=captured)


# --- client ---------------------------------------------------------------


async def test_client_unwraps_pagination_and_sends_token():
    captured: list[httpx.Request] = []
    client = AuthentikClient(
        BASE, "secret-token", transport=_transport(ROUTES, captured), backoff=0
    )
    apps = await client.list_applications()
    assert apps[0]["slug"] == "vaultwarden"
    assert captured[0].headers["Authorization"] == "Bearer secret-token"


async def test_client_get_application():
    client = AuthentikClient(BASE, "t", transport=_transport(ROUTES), backoff=0)
    assert (await client.get_application("vaultwarden"))["name"] == "Vaultwarden"


async def test_client_4xx_fails_fast():
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(403)

    client = AuthentikClient(
        BASE, "t", transport=httpx.MockTransport(handler), retries=3, backoff=0
    )
    with pytest.raises(AuthentikError):
        await client.list_applications()
    assert calls["n"] == 1


async def test_client_retries_5xx():
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(502)
        return httpx.Response(200, json=_paginated([]))

    client = AuthentikClient(
        BASE, "t", transport=httpx.MockTransport(handler), retries=3, backoff=0
    )
    assert await client.list_applications() == []
    assert calls["n"] == 2


# --- tools / resource / prompt -------------------------------------------


@pytest.fixture
def authentik_server(tmp_path, monkeypatch):
    transport = _transport(ROUTES)
    real = authentik_tools.AuthentikClient

    def factory(base_url, token, **kwargs):
        kwargs["transport"] = transport
        kwargs["backoff"] = 0
        return real(base_url, token, **kwargs)

    monkeypatch.setattr(authentik_tools, "AuthentikClient", factory)
    return build_server(
        IsolatedSettings(
            registry_db_path=str(tmp_path / "r.db"),
            authentik_api_url=BASE,
            authentik_token="t",
        )
    )


async def call(server, name, args):
    return tool_payload(await server.call_tool(name, args))


async def test_tool_list_applications(authentik_server):
    result = await call(authentik_server, "authentik_list_applications", {})
    assert result["items"][0]["slug"] == "vaultwarden"


async def test_tool_outpost_status(authentik_server):
    result = await call(
        authentik_server, "authentik_get_outpost_status", {"name": "embedded-outpost"}
    )
    assert result["outpost"]["pk"] == "out-1"
    assert result["health"][0]["version"] == "2024.1"


async def test_tool_outpost_status_missing(authentik_server):
    result = await call(authentik_server, "authentik_get_outpost_status", {"name": "ghost"})
    assert "error" in result


async def test_tool_outpost_status_health_failure_surfaces_top_level_error(tmp_path, monkeypatch):
    # Outpost exists, but its health endpoint 500s: the error must surface at top level.
    routes = dict(ROUTES)
    del routes["GET /api/v3/outposts/instances/out-1/health/"]  # -> 404, retried then errors
    transport = _transport(routes)
    real = authentik_tools.AuthentikClient

    def factory(base_url, token, **kwargs):
        kwargs["transport"] = transport
        kwargs["backoff"] = 0
        return real(base_url, token, **kwargs)

    monkeypatch.setattr(authentik_tools, "AuthentikClient", factory)
    server = build_server(
        IsolatedSettings(
            registry_db_path=str(tmp_path / "r.db"), authentik_api_url=BASE, authentik_token="t"
        )
    )
    result = await call(server, "authentik_get_outpost_status", {"name": "embedded-outpost"})
    assert "error" in result
    assert "health" not in result


async def test_exit_criteria_policies_and_recent_events(authentik_server):
    # Pull the policy chain and the last 24h of events for a protected service.
    policies = await call(authentik_server, "authentik_list_policies", {})
    assert policies["items"][0]["name"] == "deny-after-hours"

    events = await call(authentik_server, "authentik_search_events", {"within_hours": 24})
    actions = [e["action"] for e in events["items"]]
    assert actions == ["login"]  # the 3-day-old event is filtered out


async def test_tool_unconfigured_returns_error(tmp_path):
    server = build_server(IsolatedSettings(registry_db_path=str(tmp_path / "r.db")))
    result = await call(server, "authentik_list_applications", {})
    assert "error" in result


async def test_application_resource(authentik_server):
    contents = await authentik_server.read_resource("authentik://applications/vaultwarden")
    assert "vaultwarden" in contents[0].content


async def test_audit_prompt(authentik_server):
    prompt = await authentik_server.get_prompt("audit_application_access", {"slug": "vaultwarden"})
    assert "vaultwarden" in prompt.messages[0].content.text
