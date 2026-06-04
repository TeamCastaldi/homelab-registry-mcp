"""Tests for change-event emission, event query tools, retention, and logging."""

import json
import logging

import structlog
from sqlmodel import Session

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
    return (await server.call_tool(name, args))[1]


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


def test_logging_redacts_secrets(tmp_path):
    log_file = tmp_path / "events.log"
    try:
        configure_logging(Settings(registry_log_path=str(log_file)))
        get_logger("test").info("probe", authentik_token="super-secret", host="auth.lan")

        line = log_file.read_text().strip().splitlines()[-1]
        record = json.loads(line)
        assert record["authentik_token"] == "***redacted***"
        assert record["host"] == "auth.lan"
        assert record["event"] == "probe"
    finally:
        root = logging.getLogger()
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        structlog.reset_defaults()
