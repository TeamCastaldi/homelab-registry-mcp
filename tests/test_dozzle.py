"""Dozzle MCP client, output parsers, and the dozzle_* tools.

`FakeDozzle` is no more forgiving than Dozzle's documented behavior: it rejects a
missing `host`/`container_id` and a bad `stream` with an MCP error, answers in the
NDJSON-plus-prose formats the real log tools use, and refuses unknown tools.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

import registry_mcp.integrations.dozzle.tools as dozzle_tools
from conftest import IsolatedSettings, tool_payload
from registry_mcp.integrations.dozzle import DozzleClient, DozzleError
from registry_mcp.integrations.dozzle.parse import NO_LOGS, parse_log_text
from registry_mcp.models.service import Service
from registry_mcp.server import build_server


def _text(text: str, *, is_error: bool = False) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], isError=is_error)


def _ndjson(entries: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(e) for e in entries)


def _entry(i: int, level: str = "info", message: Any = None) -> dict[str, Any]:
    return {
        "timestamp": f"2026-10-10T20:00:{i:02d}Z",
        "level": level,
        "stream": "stdout",
        "type": "single",
        "message": message if message is not None else f"line {i}",
    }


CONTAINERS = [
    {
        "id": "abc123",
        "name": "authentik-server",
        "image": "ghcr.io/goauthentik/server",
        "state": "running",
        "health": "healthy",
        "host": "host-cp",
        "created": "2026-10-01T00:00:00Z",
        "group": "authentik",
        "labels": {
            "com.docker.compose.service": "authentik",
            "com.docker.compose.project": "authentik",
            "traefik.http.middlewares.auth.basicauth.users": "admin:$apr1$secrethash",
        },
    },
    {
        "id": "def456",
        "name": "traefik",
        "image": "traefik:v3",
        "state": "running",
        "health": "healthy",
        "host": "host-cp",
        "created": "2026-10-01T00:00:00Z",
        "group": "",
        "labels": {"traefik.http.routers.dash.rule": "Host(`t.lan`)"},
    },
]


class FakeDozzle:
    """A fake Dozzle MCP session. `logs` is what the log tools return."""

    def __init__(self, containers=None, logs: str | CallToolResult | None = None) -> None:
        self.containers = CONTAINERS if containers is None else containers
        self.logs = logs if logs is not None else _ndjson([_entry(i) for i in range(3)])
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None):
        args = arguments or {}
        self.calls.append((name, args))
        if name == "list_hosts":
            return _text(json.dumps([{"id": "host-cp", "name": "control-plane"}]))
        if name == "list_containers":
            wanted = args.get("state")
            rows = [c for c in self.containers if not wanted or c["state"] == wanted]
            return _text(json.dumps(rows))
        if name in {"get_container_logs", "search_container_logs", "get_container_stats"}:
            if not args.get("host") or not args.get("container_id"):
                return _text("host and container_id are required", is_error=True)
            if args.get("stream") not in (None, "stdout", "stderr", "all"):
                return _text(f"invalid stream {args['stream']!r}", is_error=True)
            if name == "get_container_stats":
                return _text(json.dumps({"cpu": [1.0, 2.0], "memory": [10.0, 11.0]}))
            if isinstance(self.logs, CallToolResult):
                return self.logs
            return _text(self.logs)
        return _text(f"unknown tool {name}", is_error=True)


def _factory(fake):
    @asynccontextmanager
    async def factory():
        yield fake

    return factory


def _raising_factory(exc):
    @asynccontextmanager
    async def factory():
        raise exc
        yield  # pragma: no cover

    return factory


# --- client -----------------------------------------------------------------


async def test_client_returns_text_and_passes_arguments():
    fake = FakeDozzle()
    client = DozzleClient("http://d", session_factory=_factory(fake))
    await client.call_tool("list_containers", {"state": "running"})
    assert fake.calls == [("list_containers", {"state": "running"})]


async def test_client_refuses_a_tool_outside_the_allowlist():
    fake = FakeDozzle()
    client = DozzleClient("http://d", session_factory=_factory(fake))
    with pytest.raises(DozzleError, match="not an allowed"):
        await client.call_tool("restart_container", {"id": "abc"})
    assert fake.calls == []


async def test_client_raises_on_tool_error_and_empty_result():
    client = DozzleClient("http://d", session_factory=_factory(FakeDozzle()))
    with pytest.raises(DozzleError, match="host and container_id are required"):
        await client.call_tool("get_container_logs", {})
    empty = CallToolResult(content=[], isError=False)
    client = DozzleClient("http://d", session_factory=_factory(FakeDozzle(logs=empty)))
    with pytest.raises(DozzleError, match="no content"):
        await client.call_tool("get_container_logs", {"host": "h", "container_id": "c"})


async def test_client_wraps_transport_failure():
    client = DozzleClient("http://d", session_factory=_raising_factory(OSError("refused")))
    with pytest.raises(DozzleError, match="refused"):
        await client.call_tool("list_hosts")


# --- parser -----------------------------------------------------------------


def test_parser_reads_ndjson_entries():
    parsed = parse_log_text(_ndjson([_entry(1), _entry(2)]))
    assert [e["message"] for e in parsed["entries"]] == ["line 1", "line 2"]
    assert parsed["scanned"] == 2 and parsed["matches"] is None


def test_parser_reads_the_empty_range_literal():
    assert parse_log_text(NO_LOGS)["entries"] == []


def test_parser_reads_a_search_header_and_truncation_note():
    text = 'Found 1 matches for "boom" (scanned 400 entries):\n' + _ndjson([_entry(1)])
    text += "\n(output truncated at 1 MB)"
    parsed = parse_log_text(text)
    assert (parsed["matches"], parsed["scanned"]) == (1, 400)
    assert parsed["notes"] == ["(output truncated at 1 MB)"]


def test_parser_rejects_an_unrecognized_shape_instead_of_guessing():
    with pytest.raises(DozzleError, match="not recognized"):
        parse_log_text("Something entirely different happened")
    with pytest.raises(DozzleError, match="not JSON"):
        parse_log_text('{"timestamp": broken')


# --- tools ------------------------------------------------------------------


@pytest.fixture
def wired(tmp_path, monkeypatch):
    fake = FakeDozzle()
    real = dozzle_tools.DozzleClient
    monkeypatch.setattr(
        dozzle_tools,
        "DozzleClient",
        lambda url, token=None, **kw: real(url, token, session_factory=_factory(fake), **kw),
    )
    server = build_server(
        IsolatedSettings(
            registry_db_path=str(tmp_path / "r.db"),
            dozzle_mcp_url="http://dozzle.test/api/mcp",
            dozzle_mcp_max_log_entries=5,
            dozzle_mcp_max_since_minutes=60,
        )
    )
    return server, fake


async def _call(server, name, args=None):
    return tool_payload(await server.call_tool(name, args or {}))


async def test_tools_are_absent_when_unconfigured(tmp_path):
    server = build_server(IsolatedSettings(registry_db_path=str(tmp_path / "r.db")))
    assert not {t.name for t in await server.list_tools() if t.name.startswith("dozzle_")}


async def test_exactly_the_typed_tools_are_registered(wired):
    server, _ = wired
    names = {t.name for t in await server.list_tools() if t.name.startswith("dozzle_")}
    assert names == {
        "dozzle_list_hosts",
        "dozzle_list_containers",
        "dozzle_get_container_logs",
        "dozzle_search_container_logs",
        "dozzle_get_container_stats",
        "dozzle_get_service_logs",
    }


async def test_container_list_drops_every_label_but_the_compose_ones(wired):
    server, _ = wired
    payload = await _call(server, "dozzle_list_containers")
    auth = payload["containers"][0]
    assert auth["labels"] == {
        "com.docker.compose.service": "authentik",
        "com.docker.compose.project": "authentik",
    }
    assert "secrethash" not in json.dumps(payload)


async def test_container_list_filters_by_state_upstream_and_host_locally(wired):
    server, fake = wired
    await _call(server, "dozzle_list_containers", {"state": "exited"})
    assert fake.calls[-1] == ("list_containers", {"state": "exited"})
    other = await _call(server, "dozzle_list_containers", {"host": "no-such-host"})
    assert other == {"containers": []}


async def test_logs_pass_the_required_arguments_through(wired):
    server, fake = wired
    payload = await _call(
        server,
        "dozzle_get_container_logs",
        {"host": "host-cp", "container_id": "abc123", "since_minutes": 10, "stream": "stderr"},
    )
    assert fake.calls[-1] == (
        "get_container_logs",
        {"host": "host-cp", "container_id": "abc123", "since_minutes": 10, "stream": "stderr"},
    )
    assert payload["returned"] == 3 and payload["omitted"] == 0
    assert payload["entries"][0]["message"] == "line 0"
    assert "type" not in payload["entries"][0]


async def test_logs_keep_the_newest_entries_and_count_the_rest(wired):
    server, fake = wired
    fake.logs = _ndjson([_entry(i) for i in range(12)])
    payload = await _call(server, "dozzle_get_container_logs", {"host": "h", "container_id": "c"})
    assert [e["message"] for e in payload["entries"]][-1] == "line 11"
    assert payload["returned"] == 5 and payload["omitted"] == 7 and payload["scanned"] == 12


async def test_level_filter_applies_before_the_cap(wired):
    server, fake = wired
    fake.logs = _ndjson([_entry(0, "error"), _entry(1), _entry(2, "ERROR"), _entry(3)])
    payload = await _call(
        server, "dozzle_get_container_logs", {"host": "h", "container_id": "c", "level": "error"}
    )
    assert [e["message"] for e in payload["entries"]] == ["line 0", "line 2"]


async def test_credentials_in_messages_are_scrubbed_and_counted(wired):
    server, fake = wired
    secret = "hunter2hunter2hunter2hunter2"
    fake.logs = _ndjson(
        [
            _entry(0, message=f"DB_PASSWORD={secret}"),
            _entry(1, message={"event": "login", "api_key": f"API_KEY={secret}"}),
            _entry(2),
        ]
    )
    payload = await _call(server, "dozzle_get_container_logs", {"host": "h", "container_id": "c"})
    assert secret not in json.dumps(payload)
    assert payload["scrubbed"] == 2


async def test_a_response_near_dozzles_limit_is_flagged_oldest_first(wired):
    server, fake = wired
    fake.logs = _ndjson([_entry(i % 60, message="x" * 900) for i in range(1100)])
    payload = await _call(server, "dozzle_get_container_logs", {"host": "h", "container_id": "c"})
    assert payload["upstream_truncated"] is True
    assert "OLDEST" in payload["note"]


async def test_search_sends_its_arguments_and_reports_the_match_count(wired):
    server, fake = wired
    fake.logs = 'Found 1 matches for "boom" (scanned 400 entries):\n' + _ndjson([_entry(1)])
    payload = await _call(
        server,
        "dozzle_search_container_logs",
        {"host": "h", "container_id": "c", "query": "boom", "case_sensitive": True},
    )
    assert fake.calls[-1][1]["query"] == "boom" and fake.calls[-1][1]["case_sensitive"] is True
    assert payload["matches"] == 1 and payload["scanned"] == 400


async def test_bad_bounds_are_rejected_before_dozzle_is_called(wired):
    server, fake = wired
    base = {"host": "h", "container_id": "c"}
    for args in (
        {"since_minutes": 0},
        {"since_minutes": 61},
        {"stream": "both"},
        {"max_entries": 6},
    ):
        payload = await _call(server, "dozzle_get_container_logs", {**base, **args})
        assert "error" in payload
    empty_query = await _call(server, "dozzle_search_container_logs", {**base, "query": " "})
    assert "error" in empty_query
    assert fake.calls == []


async def test_a_dozzle_error_is_a_reported_error(wired):
    server, _ = wired
    payload = await _call(server, "dozzle_get_container_logs", {"host": "", "container_id": ""})
    assert "required" in payload["error"]


async def test_stats_are_returned_as_parsed_json(wired):
    server, _ = wired
    payload = await _call(server, "dozzle_get_container_stats", {"host": "h", "container_id": "c"})
    assert payload["stats"] == {"cpu": [1.0, 2.0], "memory": [10.0, 11.0]}


# --- registry join ----------------------------------------------------------


def _add(server_store, **kwargs):
    return server_store.create_service(Service(display_name=kwargs["name"], **kwargs))


@pytest.fixture
def joined(tmp_path, monkeypatch):
    """Like `wired`, but with the registry store reachable for adding services."""
    fake = FakeDozzle()
    real = dozzle_tools.DozzleClient
    monkeypatch.setattr(
        dozzle_tools,
        "DozzleClient",
        lambda url, token=None, **kw: real(url, token, session_factory=_factory(fake), **kw),
    )
    from registry_mcp.registry import RegistryStore

    db = str(tmp_path / "r.db")
    settings = IsolatedSettings(registry_db_path=db, dozzle_mcp_url="http://dozzle.test/api/mcp")
    server = build_server(settings)
    return server, fake, RegistryStore(db)


async def test_service_logs_resolve_by_compose_service_label(joined):
    server, fake, store = joined
    svc = _add(store, name="authentik")
    payload = await _call(server, "dozzle_get_service_logs", {"service_id": svc.id})
    assert payload["container"]["id"] == "abc123" and payload["service"] == "authentik"
    assert fake.calls[-1][1]["container_id"] == "abc123"
    assert fake.calls[-1][1]["host"] == "host-cp"


async def test_service_logs_fall_back_to_container_name_then_router(joined):
    server, _, store = joined
    by_name = _add(store, name="traefik")
    assert (await _call(server, "dozzle_get_service_logs", {"service_id": by_name.id}))[
        "container"
    ]["id"] == "def456"
    by_router = _add(store, name="dashboard", traefik_router="dash@docker")
    assert (await _call(server, "dozzle_get_service_logs", {"service_id": by_router.id}))[
        "container"
    ]["id"] == "def456"


async def test_service_logs_with_no_match_return_an_error_and_no_logs_call(joined):
    server, fake, store = joined
    svc = _add(store, name="ghost")
    payload = await _call(server, "dozzle_get_service_logs", {"service_id": svc.id})
    assert "no container matches" in payload["error"] and payload["candidates"] == []
    assert [c[0] for c in fake.calls] == ["list_containers"]


async def test_service_logs_with_several_matches_return_candidates_not_a_guess(joined):
    server, fake, store = joined
    twin = {**CONTAINERS[1], "id": "zzz999", "host": "host-2"}
    fake.containers = [*CONTAINERS, twin]
    svc = _add(store, name="traefik")
    payload = await _call(server, "dozzle_get_service_logs", {"service_id": svc.id})
    assert "pick one" in payload["error"]
    assert {c["id"] for c in payload["candidates"]} == {"def456", "zzz999"}
    assert [c[0] for c in fake.calls] == ["list_containers"]


async def test_service_logs_for_an_unknown_service(joined):
    server, fake, _ = joined
    payload = await _call(server, "dozzle_get_service_logs", {"service_id": "nope"})
    assert "not found" in payload["error"] and fake.calls == []


async def test_the_diagnose_prompt_names_the_service_and_the_tools(wired):
    server, _ = wired
    prompt = await server.get_prompt("diagnose_service_logs", {"service_id": "authentik"})
    text = prompt.messages[0].content.text
    assert "authentik" in text and "dozzle_get_service_logs" in text
