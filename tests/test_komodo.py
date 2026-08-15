"""Tests for the Komodo client, tools, resource, and diagnostic prompt."""

import base64
import json

import httpx
import pytest

import registry_mcp.integrations.komodo.tools as komodo_tools
from conftest import IsolatedSettings
from registry_mcp.integrations.komodo import KomodoClient, KomodoError
from registry_mcp.server import build_server

STACKS = [
    {"name": "traefik", "status": "running", "services": ["traefik"]},
    {"name": "authentik", "status": "running", "services": ["authentik-server"]},
]

SERVICES = [
    {"id": "traefik", "name": "traefik", "state": "running"},
    {"id": "authentik-server", "name": "authentik-server", "state": "running"},
]


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path != "/rpc":
        return httpx.Response(404, json={"detail": "not found"})
    payload = json.loads(request.read())
    query = payload.get("query")
    if query == "ListStacks":
        return httpx.Response(200, json=STACKS)
    if query == "GetStack":
        name = payload.get("name")
        match = next((s for s in STACKS if s["name"] == name), None)
        if match is None:
            return httpx.Response(404, json={"detail": "not found"})
        return httpx.Response(200, json=match)
    if query == "ListServices":
        return httpx.Response(200, json=SERVICES)
    if query == "GetService":
        service_id = payload.get("serviceId")
        match = next((s for s in SERVICES if s["id"] == service_id), None)
        if match is None:
            return httpx.Response(404, json={"detail": "not found"})
        return httpx.Response(200, json=match)
    if query == "Health":
        return httpx.Response(200, json={"status": "ok"})
    if query == "ListUpdates":
        return httpx.Response(200, json=[{"stack": "traefik", "available": False}])
    if query == "GetLogs":
        return httpx.Response(200, json="log line one\nlog line two")
    return httpx.Response(404, json={"detail": "unknown query"})


def _transport():
    return httpx.MockTransport(_handler)


# --- client -----------------------------------------------------------------


async def test_client_builds_basic_auth_header():
    client = KomodoClient("http://k", "my-key", "my-secret", transport=_transport(), backoff=0)
    expected = base64.b64encode(b"my-key:my-secret").decode()
    assert client._auth_header == f"Basic {expected}"

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json=STACKS)

    client = KomodoClient(
        "http://k", "my-key", "my-secret", transport=httpx.MockTransport(handler), backoff=0
    )
    await client.list_stacks()
    assert captured["authorization"] == f"Basic {expected}"


async def test_client_parses_stacks():
    client = KomodoClient("http://k", "key", "secret", transport=_transport(), backoff=0)
    stacks = await client.list_stacks()
    assert {s["name"] for s in stacks} == {"traefik", "authentik"}
    assert (await client.get_stack("traefik"))["status"] == "running"
    assert (await client.health_check())["status"] == "ok"


async def test_client_parses_services():
    client = KomodoClient("http://k", "key", "secret", transport=_transport(), backoff=0)
    services = await client.list_services()
    assert {s["id"] for s in services} == {"traefik", "authentik-server"}
    assert (await client.get_service("traefik"))["state"] == "running"


async def test_client_health_check_reports_error():
    client = KomodoClient(
        "http://k",
        "key",
        "secret",
        transport=httpx.MockTransport(lambda _r: httpx.Response(500)),
        retries=1,
        backoff=0,
    )
    result = await client.health_check()
    assert result["status"] == "unhealthy"
    assert "error" in result


async def test_client_retries_then_succeeds():
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"status": "ok"})

    client = KomodoClient(
        "http://k", "key", "secret", transport=httpx.MockTransport(handler), retries=3, backoff=0
    )
    assert await client.health_check() == {"status": "ok"}
    assert calls["n"] == 3


async def test_client_4xx_fails_fast():
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(404)

    client = KomodoClient(
        "http://k", "key", "secret", transport=httpx.MockTransport(handler), retries=3, backoff=0
    )
    with pytest.raises(KomodoError):
        await client.list_stacks()
    assert calls["n"] == 1  # client errors are not retried


async def test_client_exhausts_retries():
    client = KomodoClient(
        "http://k",
        "key",
        "secret",
        transport=httpx.MockTransport(lambda _r: httpx.Response(503)),
        retries=2,
        backoff=0,
    )
    with pytest.raises(KomodoError):
        await client.list_stacks()


# --- tools / resource / prompt -----------------------------------------------


@pytest.fixture
def komodo_server(tmp_path, monkeypatch):
    transport = _transport()
    real = komodo_tools.KomodoClient

    def factory(base_url, api_key, api_secret, **kwargs):
        kwargs["transport"] = transport
        kwargs["backoff"] = 0
        return real(base_url, api_key, api_secret, **kwargs)

    monkeypatch.setattr(komodo_tools, "KomodoClient", factory)
    return build_server(
        IsolatedSettings(
            registry_db_path=str(tmp_path / "r.db"),
            komodo_api_url="http://komodo.test",
            komodo_api_key="key",
            komodo_api_secret="secret",
        )
    )


async def call(server, name, args):
    return (await server.call_tool(name, args))[1]


async def test_tool_list_stacks(komodo_server):
    result = await call(komodo_server, "komodo_list_stacks", {})
    assert {s["name"] for s in result["items"]} == {"traefik", "authentik"}


async def test_tool_get_stack_and_updates(komodo_server):
    stack = await call(komodo_server, "komodo_get_stack", {"name": "traefik"})
    assert stack["status"] == "running"
    updates = await call(komodo_server, "komodo_list_updates", {})
    assert updates["items"][0]["stack"] == "traefik"


async def test_tool_get_logs(komodo_server):
    result = await call(komodo_server, "komodo_get_logs", {"service_id": "traefik"})
    assert "log line one" in result["logs"]


async def test_tool_list_services_and_get_service(komodo_server):
    result = await call(komodo_server, "komodo_list_services", {})
    assert {s["id"] for s in result["items"]} == {"traefik", "authentik-server"}
    service = await call(komodo_server, "komodo_get_service", {"service_id": "traefik"})
    assert service["state"] == "running"


async def test_tool_unconfigured_returns_error(tmp_path):
    server = build_server(IsolatedSettings(registry_db_path=str(tmp_path / "r.db")))
    result = await call(server, "komodo_list_stacks", {})
    assert "error" in result


async def test_stack_resource(komodo_server):
    contents = await komodo_server.read_resource("komodo://stacks/traefik")
    assert "traefik" in contents[0].content


async def test_diagnose_stack_prompt(komodo_server):
    prompt = await komodo_server.get_prompt("diagnose_stack", {"name": "traefik"})
    assert "traefik" in prompt.messages[0].content.text
