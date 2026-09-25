"""Tests for change-event emission, event query tools, retention, and logging."""

import json
import logging

import pytest
import structlog
from mcp.server.fastmcp.exceptions import ToolError
from sqlmodel import Session

from conftest import tool_payload
from registry_mcp.config import Settings
from registry_mcp.logging import configure_logging, get_logger
from registry_mcp.models import (
    FIELD_CREATED,
    FIELD_DELETED,
    Category,
    DiscoveryEvent,
    Service,
    SourceType,
)


async def call(server, name, args):
    return tool_payload(await server.call_tool(name, args))


def test_crud_emits_change_events(store):
    created = store.create_service(
        Service(name="plex", display_name="Plex", category=Category.media)
    )
    store.update_service(created.id, {"notes": "hello"})
    store.update_service(created.id, {"notes": "hello"})  # no-op, no event

    events = store.list_change_events(service_id=created.id)
    fields = [e.field for e in events]
    assert fields == ["notes", FIELD_CREATED]  # newest first
    assert events[0].actor == "manual"

    store.delete_service(created.id)
    events = store.list_change_events(service_id=created.id)
    assert events[0].field == FIELD_DELETED  # preserved after the service is gone
    assert events[0].old == "plex"


async def test_event_tools_query_changes(server):
    added = await call(
        server,
        "registry_add_service",
        {"name": "gitea", "display_name": "Gitea", "category": "infra"},
    )
    sid = added["id"]
    await call(server, "registry_update_service", {"id": sid, "notes": "patched"})

    changes = await call(server, "events_list_changes", {})
    assert {e["field"] for e in changes["result"]} == {FIELD_CREATED, "notes"}
    assert changes["result"][0]["actor"].startswith("manual:")

    for_service = await call(server, "events_get_for_service", {"service_id": sid})
    assert all(e["service_id"] == sid for e in for_service["result"])


async def test_event_tools_discoveries(server, store):
    empty = await call(server, "events_list_discoveries", {})
    assert empty["result"] == []

    with Session(store.engine) as session:
        session.add(DiscoveryEvent(source=SourceType.traefik, items_seen=7))
        session.commit()

    listed = await call(server, "events_list_discoveries", {"source": "traefik"})
    assert listed["result"][0]["items_seen"] == 7
    assert listed["result"][0]["source"] == "traefik"


def test_purge_old_events(store):
    created = store.create_service(Service(name="vw", display_name="VW"))
    assert store.list_change_events(service_id=created.id)
    purged = store.purge_old_events(-1)  # cutoff in the future -> remove everything
    assert purged["change_events"] >= 1
    assert store.list_change_events(service_id=created.id) == []


@pytest.fixture
def logged(tmp_path):
    """Log one `probe` event with the given fields; return the JSON record written."""
    log_file = tmp_path / "events.log"
    configure_logging(Settings(registry_log_path=str(log_file)))

    def _log(**fields):
        get_logger("test").info("probe", **fields)
        return json.loads(log_file.read_text().strip().splitlines()[-1])

    yield _log
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    structlog.reset_defaults()


def test_logging_redacts_secrets(logged):
    record = logged(authentik_token="super-secret", host="auth.lan")
    assert record["authentik_token"] == "***redacted***"
    assert record["host"] == "auth.lan"
    assert record["event"] == "probe"


def test_logging_redacts_key_named_fields_but_not_key_lists_or_paths(logged):
    record = logged(
        key="k-value",
        access_key="ak-value",
        keys=["DB_PASSWORD", "API_TOKEN"],
        key_path="/etc/git-crypt.key",
    )
    assert record["key"] == "***redacted***"
    assert record["access_key"] == "***redacted***"
    # Names of secrets and a path to one are diagnostics, not the secret itself.
    assert record["keys"] == ["DB_PASSWORD", "API_TOKEN"]
    assert record["key_path"] == "/etc/git-crypt.key"


def test_logging_redacts_secrets_nested_in_dicts_and_lists(logged):
    payload = {
        "source": {"git_token": "t-value", "host": "git.lan"},
        "headers": [{"authorization": "Bearer b-value"}, {"accept": "json"}],
    }
    record = logged(payload=payload)
    assert record["payload"] == {
        "source": {"git_token": "***redacted***", "host": "git.lan"},
        "headers": [{"authorization": "***redacted***"}, {"accept": "json"}],
    }
    # The caller's own dict is never edited.
    assert payload["source"]["git_token"] == "t-value"


_EVENT_TOOLS = {
    "events_list_discoveries": {},
    "events_list_changes": {},
    "events_get_for_service": {"service_id": "any"},
}


@pytest.mark.parametrize("tool", _EVENT_TOOLS)
@pytest.mark.parametrize("limit", [-1, 0, 1001])
async def test_event_tools_reject_an_out_of_range_limit(server, tool, limit):
    # SQLite reads a negative LIMIT as "no limit": -1 would return the whole log.
    with pytest.raises(ToolError, match="limit"):
        await server.call_tool(tool, {**_EVENT_TOOLS[tool], "limit": limit})


@pytest.mark.parametrize("tool", _EVENT_TOOLS)
async def test_event_tools_publish_the_limit_bounds(server, tool):
    listed = {t.name: t for t in await server.list_tools()}
    limit = listed[tool].inputSchema["properties"]["limit"]
    assert (limit["minimum"], limit["maximum"], limit["default"]) == (1, 1000, 100)


async def test_event_tools_honor_a_limit_in_range(server):
    added = await call(server, "registry_add_service", {"name": "gitea", "display_name": "Gitea"})
    await call(server, "registry_update_service", {"id": added["id"], "notes": "patched"})

    one = await call(server, "events_list_changes", {"limit": 1})
    assert [e["field"] for e in one["result"]] == ["notes"]
