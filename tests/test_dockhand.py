"""Tests for the Dockhand client, tools, resource, and diagnostic prompt."""

import httpx
import pytest

import registry_mcp.integrations.dockhand.tools as dockhand_tools
from conftest import IsolatedSettings
from registry_mcp.integrations.dockhand import DockhandClient, DockhandError
from registry_mcp.server import build_server

ROUTES = {
    "/api/environments": [{"id": "env1", "name": "homelab-docker"}],
    "/api/environments/env1": {"id": "env1", "name": "homelab-docker"},
    "/api/stacks": [{"id": "stack1", "name": "traefik-stack", "environment_id": "env1"}],
    "/api/stacks/stack1": {"id": "stack1", "name": "traefik-stack", "environment_id": "env1"},
    "/api/containers": [
        {"id": "c1", "name": "traefik", "stack_id": "stack1", "environment_id": "env1"}
    ],
    "/api/containers/check-updates": [
        {"container_id": "c1", "current_tag": "v3.1", "latest_tag": "v3.2"}
    ],
    "/api/vulnerabilities": [{"container_id": "c1", "cve": "CVE-2024-1234", "severity": "high"}],
}


def _transport(routes, captured=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        body = routes.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"detail": "not found"})
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


# --- client ---------------------------------------------------------------


async def test_client_sends_bearer_token():
    captured: list[httpx.Request] = []
    client = DockhandClient(
        "http://d", "dh_secret", transport=_transport(ROUTES, captured), backoff=0
    )
    await client.list_environments()
    assert captured[0].headers["Authorization"] == "Bearer dh_secret"


async def test_client_parses_endpoints():
    client = DockhandClient("http://d", "t", transport=_transport(ROUTES), backoff=0)
    assert (await client.list_environments())[0]["name"] == "homelab-docker"
    assert (await client.get_environment("env1"))["id"] == "env1"
    assert (await client.list_stacks())[0]["name"] == "traefik-stack"
    assert (await client.get_stack("stack1"))["id"] == "stack1"
    assert (await client.list_containers())[0]["name"] == "traefik"
    assert (await client.list_pending_updates())[0]["latest_tag"] == "v3.2"
    assert (await client.list_vulnerabilities())[0]["cve"] == "CVE-2024-1234"


async def test_client_retries_then_succeeds():
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json=[])

    client = DockhandClient(
        "http://d", "t", transport=httpx.MockTransport(handler), retries=3, backoff=0
    )
    assert await client.list_environments() == []
    assert calls["n"] == 3


async def test_client_4xx_fails_fast():
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(404)

    client = DockhandClient(
        "http://d", "t", transport=httpx.MockTransport(handler), retries=3, backoff=0
    )
    with pytest.raises(DockhandError):
        await client.list_environments()
    assert calls["n"] == 1  # client errors are not retried


async def test_client_exhausts_retries():
    client = DockhandClient(
        "http://d",
        "t",
        transport=httpx.MockTransport(lambda _r: httpx.Response(503)),
        retries=2,
        backoff=0,
    )
    with pytest.raises(DockhandError):
        await client.list_environments()


# --- tools / resource / prompt -------------------------------------------


@pytest.fixture
def dockhand_server(tmp_path, monkeypatch):
    transport = _transport(ROUTES)
    real = dockhand_tools.DockhandClient

    def factory(base_url, token, **kwargs):
        kwargs["transport"] = transport
        kwargs["backoff"] = 0
        return real(base_url, token, **kwargs)

    monkeypatch.setattr(dockhand_tools, "DockhandClient", factory)
    return build_server(
        IsolatedSettings(
            registry_db_path=str(tmp_path / "r.db"),
            dockhand_api_url="http://dockhand.test",
            dockhand_token="dh_secret",
        )
    )


async def call(server, name, args):
    return (await server.call_tool(name, args))[1]


async def test_tool_list_environments(dockhand_server):
    result = await call(dockhand_server, "dockhand_list_environments", {})
    assert result["items"][0]["name"] == "homelab-docker"


async def test_tool_list_stacks(dockhand_server):
    result = await call(dockhand_server, "dockhand_list_stacks", {})
    assert result["items"][0]["name"] == "traefik-stack"


async def test_tool_get_stack(dockhand_server):
    result = await call(dockhand_server, "dockhand_get_stack", {"stack_id": "stack1"})
    assert result["id"] == "stack1"


async def test_tool_list_containers(dockhand_server):
    result = await call(dockhand_server, "dockhand_list_containers", {})
    assert result["items"][0]["name"] == "traefik"


async def test_tool_list_pending_updates(dockhand_server):
    result = await call(dockhand_server, "dockhand_list_pending_updates", {})
    assert result["items"][0]["latest_tag"] == "v3.2"


async def test_tool_list_vulnerabilities(dockhand_server):
    result = await call(dockhand_server, "dockhand_list_vulnerabilities", {})
    assert result["items"][0]["cve"] == "CVE-2024-1234"


async def test_tool_unconfigured_returns_error(tmp_path):
    server = build_server(IsolatedSettings(registry_db_path=str(tmp_path / "r.db")))
    result = await call(server, "dockhand_list_environments", {})
    assert "error" in result


async def test_stack_resource(dockhand_server):
    contents = await dockhand_server.read_resource("dockhand://stacks/stack1")
    assert "stack1" in contents[0].content


async def test_diagnose_stack_prompt(dockhand_server):
    prompt = await dockhand_server.get_prompt("diagnose_stack", {"stack_id": "stack1"})
    assert "stack1" in prompt.messages[0].content.text
