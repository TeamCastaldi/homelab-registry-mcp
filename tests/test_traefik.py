"""Tests for the Traefik client, tools, resource, and diagnostic prompt."""

import httpx
import pytest

import registry_mcp.integrations.traefik.tools as traefik_tools
from conftest import IsolatedSettings, tool_payload
from http_fakes import strict_transport
from registry_mcp.integrations.traefik import TraefikClient, TraefikError
from registry_mcp.server import build_server

ROUTES = {
    "GET /api/overview": {"http": {"routers": {"total": 2}}},
    "GET /api/http/routers": [
        {"name": "web@docker", "status": "enabled", "middlewares": ["authentik@docker"]},
        {"name": "api@docker", "status": "enabled", "middlewares": []},
    ],
    # Distinct from the http routers above: proves `protocol` actually
    # reaches the request path (R1) rather than being ignored in favor of
    # the "http" default — a dropped protocol would 404 here, not silently
    # match the http route.
    "GET /api/tcp/routers": [{"name": "postgres@docker", "status": "enabled", "middlewares": []}],
    "GET /api/http/routers/web@docker": {
        "name": "web@docker",
        "status": "enabled",
        "middlewares": ["authentik@docker"],
    },
    "GET /api/http/middlewares": [{"name": "authentik@docker"}],
    "GET /api/rawdata": {"tls": {"certificates": [{"domain": "example.lan"}]}},
}


def _transport(routes):
    return strict_transport(routes)


# --- client ---------------------------------------------------------------


async def test_client_parses_endpoints():
    client = TraefikClient("http://t", transport=_transport(ROUTES), backoff=0)
    routers = await client.list_routers()
    assert {r["name"] for r in routers} == {"web@docker", "api@docker"}
    assert (await client.get_router("web@docker"))["status"] == "enabled"
    assert "http" in await client.overview()


async def test_client_list_routers_protocol_reaches_the_request():
    """R1: `list_routers(protocol=...)` must reach the request path, not
    silently stay on "http" — the tcp route returns a distinct record, so a
    dropped protocol argument gets a 404 or the wrong routers, not a pass."""
    client = TraefikClient("http://t", transport=_transport(ROUTES), backoff=0)
    routers = await client.list_routers(protocol="tcp")
    assert {r["name"] for r in routers} == {"postgres@docker"}


async def test_client_retries_then_succeeds():
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    client = TraefikClient("http://t", transport=httpx.MockTransport(handler), retries=3, backoff=0)
    assert await client.overview() == {"ok": True}
    assert calls["n"] == 3


async def test_client_4xx_fails_fast():
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(404)

    client = TraefikClient("http://t", transport=httpx.MockTransport(handler), retries=3, backoff=0)
    with pytest.raises(TraefikError):
        await client.overview()
    assert calls["n"] == 1  # client errors are not retried


async def test_client_exhausts_retries():
    client = TraefikClient(
        "http://t",
        transport=httpx.MockTransport(lambda _r: httpx.Response(503)),
        retries=2,
        backoff=0,
    )
    with pytest.raises(TraefikError):
        await client.overview()


# --- tools / resource / prompt -------------------------------------------


@pytest.fixture
def traefik_server(tmp_path, monkeypatch):
    transport = _transport(ROUTES)
    real = traefik_tools.TraefikClient

    def factory(base_url, **kwargs):
        kwargs["transport"] = transport
        kwargs["backoff"] = 0
        return real(base_url, **kwargs)

    monkeypatch.setattr(traefik_tools, "TraefikClient", factory)
    return build_server(
        IsolatedSettings(
            registry_db_path=str(tmp_path / "r.db"), traefik_api_url="http://traefik.test"
        )
    )


async def call(server, name, args):
    return tool_payload(await server.call_tool(name, args))


async def test_tool_list_routers(traefik_server):
    result = await call(traefik_server, "traefik_list_routers", {})
    assert {r["name"] for r in result["items"]} == {"web@docker", "api@docker"}


async def test_exit_criteria_routers_using_middleware(traefik_server):
    # "which routers are using middleware X" in one tool call
    result = await call(traefik_server, "traefik_list_routers", {})
    using = [r["name"] for r in result["items"] if "authentik@docker" in r.get("middlewares", [])]
    assert using == ["web@docker"]


async def test_tool_get_router_and_tls(traefik_server):
    router = await call(traefik_server, "traefik_get_router", {"name": "web@docker"})
    assert router["status"] == "enabled"
    tls = await call(traefik_server, "traefik_list_tls_certificates", {})
    assert tls["tls"]["certificates"][0]["domain"] == "example.lan"


async def test_tool_unconfigured_returns_error(tmp_path):
    server = build_server(IsolatedSettings(registry_db_path=str(tmp_path / "r.db")))
    result = await call(server, "traefik_list_routers", {})
    assert "error" in result


async def test_router_resource(traefik_server):
    contents = await traefik_server.read_resource("traefik://routers/web@docker")
    assert "web@docker" in contents[0].content


async def test_diagnose_router_prompt(traefik_server):
    prompt = await traefik_server.get_prompt("diagnose_router", {"name": "web@docker"})
    assert "web@docker" in prompt.messages[0].content.text
