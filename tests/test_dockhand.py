"""Tests for the Dockhand client, tools, resource, and diagnostic prompt."""

import httpx
import pytest

import registry_mcp.integrations.dockhand.tools as dockhand_tools
from conftest import IsolatedSettings, tool_payload
from http_fakes import strict_transport
from registry_mcp.integrations.dockhand import DockhandClient, DockhandError
from registry_mcp.server import build_server

# Query-string routes are exact: a client that dropped `?env=`, or added a
# parameter nothing asked for, gets a 404 rather than a list. Shapes follow
# the live instance (#193): integer environment ids, and containers, stacks,
# and update checks scoped to one environment by `?env=<id>`, with nothing
# answered for a request that leaves it out. Environment 7 answers in a
# `results` envelope so `_list`'s unwrap (K2) is exercised.
ROUTES = {
    "GET /api/environments": [{"id": 1, "name": "Control Plane"}, {"id": 7, "name": "Heimdall"}],
    "GET /api/environments/1": {"id": 1, "name": "Control Plane"},
    "GET /api/stacks?env=1": [{"name": "core", "status": "running"}],
    "GET /api/stacks?env=7": [
        {"name": "core", "status": "running"},
        {"name": "bentopdf", "status": "running"},
    ],
    "GET /api/containers?env=1": [
        {"id": "c1", "name": "traefik", "labels": {"com.docker.compose.project": "core"}}
    ],
    "GET /api/containers?env=7": {
        "results": [
            {"id": "c2", "name": "bentopdf", "labels": {"com.docker.compose.project": "bentopdf"}},
            {"id": "c3", "name": "adhoc", "labels": {}},
        ]
    },
    "GET /api/containers/check-updates?env=1": [
        {"container_id": "c1", "current_tag": "v3.1", "latest_tag": "v3.2"}
    ],
    "GET /api/containers/check-updates?env=7": [],
    "GET /api/vulnerabilities": [
        {"container_id": "c1", "cve": "CVE-2024-1234", "severity": "high"}
    ],
}


def _transport(routes, captured=None):
    return strict_transport(routes, captured=captured)


def _client(captured=None):
    return DockhandClient("http://d", "t", transport=_transport(ROUTES, captured), backoff=0)


# --- client ---------------------------------------------------------------


async def test_client_sends_bearer_token():
    captured: list[httpx.Request] = []
    client = DockhandClient(
        "http://d", "dh_secret", transport=_transport(ROUTES, captured), backoff=0
    )
    await client.list_environments()
    assert captured[0].headers["Authorization"] == "Bearer dh_secret"


async def test_client_parses_endpoints():
    client = _client()
    assert (await client.list_environments())[0]["name"] == "Control Plane"
    assert (await client.get_environment("1"))["id"] == 1
    assert (await client.list_vulnerabilities())[0]["cve"] == "CVE-2024-1234"


async def test_client_unfiltered_lists_ask_every_environment_and_tag_items():
    """#193: with no environment, each list is read per environment, never
    without `?env=` (which Dockhand answers with an empty list)."""
    client = _client()
    containers = await client.list_containers()
    assert [(c["name"], c["environment_id"]) for c in containers] == [
        ("traefik", "1"),
        ("bentopdf", "7"),
        ("adhoc", "7"),
    ]
    stacks = await client.list_stacks()
    assert [(s["name"], s["environment_id"]) for s in stacks] == [
        ("core", "1"),
        ("core", "7"),
        ("bentopdf", "7"),
    ]
    updates = await client.list_pending_updates()
    assert [(u["container_id"], u["environment_id"]) for u in updates] == [("c1", "1")]


async def test_client_one_environment_sends_only_that_env():
    captured: list[httpx.Request] = []
    containers = await _client(captured).list_containers(environment_id="7")
    assert [c["name"] for c in containers] == ["bentopdf", "adhoc"]  # `results` unwrapped
    assert [str(r.url) for r in captured] == ["http://d/api/containers?env=7"]


async def test_client_rejects_a_malformed_environment_before_any_request():
    captured: list[httpx.Request] = []
    with pytest.raises(DockhandError, match="digits only"):
        await _client(captured).list_containers(environment_id="1&all=false")
    assert captured == []


async def test_client_list_containers_filters_by_stack_name():
    containers = await _client().list_containers(stack_id="bentopdf")
    assert [c["id"] for c in containers] == ["c2"]


async def test_client_get_stack_by_name():
    stack = await _client().get_stack("bentopdf")
    assert stack["environment_id"] == "7"


async def test_client_get_stack_needs_environment_when_name_is_ambiguous():
    with pytest.raises(DockhandError, match="environments 1, 7"):
        await _client().get_stack("core")
    assert (await _client().get_stack("core", "1"))["environment_id"] == "1"


async def test_client_get_stack_unknown_name():
    with pytest.raises(DockhandError, match="no Dockhand stack named 'nope'"):
        await _client().get_stack("nope")


# K1 (the read-only invariant) needs no dedicated test: every client test in
# this file now runs against `strict_transport`, which 405s on anything but
# GET, so a client that started sending POST would fail every test here, not
# just a hand-picked one.


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
    return tool_payload(await server.call_tool(name, args))


async def test_tool_list_environments(dockhand_server):
    result = await call(dockhand_server, "dockhand_list_environments", {})
    assert result["items"][0]["name"] == "Control Plane"


async def test_tool_list_stacks(dockhand_server):
    result = await call(dockhand_server, "dockhand_list_stacks", {"environment_id": "7"})
    assert [s["name"] for s in result["items"]] == ["core", "bentopdf"]


async def test_tool_get_stack(dockhand_server):
    result = await call(
        dockhand_server, "dockhand_get_stack", {"stack_id": "core", "environment_id": "7"}
    )
    assert (result["name"], result["environment_id"]) == ("core", "7")


async def test_tool_list_containers(dockhand_server):
    result = await call(dockhand_server, "dockhand_list_containers", {"stack_id": "core"})
    assert [c["name"] for c in result["items"]] == ["traefik"]


async def test_tool_list_pending_updates(dockhand_server):
    result = await call(dockhand_server, "dockhand_list_pending_updates", {"environment_id": "1"})
    assert result["items"][0]["latest_tag"] == "v3.2"


async def test_tool_list_vulnerabilities(dockhand_server):
    result = await call(dockhand_server, "dockhand_list_vulnerabilities", {})
    assert result["items"][0]["cve"] == "CVE-2024-1234"


async def test_tool_unconfigured_returns_error(tmp_path):
    server = build_server(IsolatedSettings(registry_db_path=str(tmp_path / "r.db")))
    result = await call(server, "dockhand_list_environments", {})
    assert "error" in result


async def test_stack_resource(dockhand_server):
    contents = await dockhand_server.read_resource("dockhand://stacks/bentopdf")
    assert '"environment_id": "7"' in contents[0].content


async def test_diagnose_stack_prompt(dockhand_server):
    prompt = await dockhand_server.get_prompt("diagnose_stack", {"stack_id": "stack1"})
    assert "stack1" in prompt.messages[0].content.text
