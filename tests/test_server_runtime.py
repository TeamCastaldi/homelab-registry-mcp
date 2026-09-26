"""The server's single composition root and its transport-independent scheduler.

`main()` used to build a second, parallel set of engines for the scheduler and
only started it on streamable-http (by monkey-patching the SDK's runner), so
discovery never ran on stdio or SSE, and tools and scheduled jobs saw
different objects.
"""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import timedelta

from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session
from starlette.testclient import TestClient

from conftest import IsolatedSettings
from registry_mcp.models import DiscoveryStatus, SourceType
from registry_mcp.models.event import ChangeEvent
from registry_mcp.models.service import utcnow
from registry_mcp.registry import RegistryStore
from registry_mcp.server import _run_stdio, build_app, build_runtime_scheduler, http_app


def _settings(**overrides):
    base = dict(registry_db_path=":memory:", traefik_api_url="http://traefik.test:8080")
    base.update(overrides)
    return IsolatedSettings(**base)


async def test_scheduled_jobs_and_tools_share_one_object_graph():
    server, runtime = build_app(_settings())
    scheduler = build_runtime_scheduler(runtime)

    job = scheduler.get_job("discovery-traefik")
    assert job.func.__self__ is runtime.discovery

    # On an in-memory DB a second graph would be a second, empty database.
    now = utcnow()
    runtime.discovery._store.record_discovery_event(
        SourceType.traefik, started_at=now, finished_at=now, status=DiscoveryStatus.ok
    )
    _, structured = await server.call_tool("discovery_status", {})
    assert structured["sources"]["traefik"]["status"] == "ok"


def test_write_path_jobs_are_never_scheduled_in_read_only_mode():
    settings = _settings(
        git_base_url="https://git.test",
        git_token="tok",
        git_repo="nathan/homelab",
        proposal_comment_poll_enabled=True,
        normalization_enabled=True,
    )
    _, runtime = build_app(settings)

    healthy = build_runtime_scheduler(dataclasses.replace(runtime, read_only=False))
    assert {"proposal-comment-poll", "normalization-sweep"} <= {j.id for j in healthy.get_jobs()}

    degraded = build_runtime_scheduler(dataclasses.replace(runtime, read_only=True))
    assert {j.id for j in degraded.get_jobs()} == {"discovery-traefik"}


def test_normalization_sweep_runs_at_fixed_times_so_a_restart_doesnt_delay_it():
    settings = _settings(
        git_base_url="https://git.test",
        git_token="tok",
        git_repo="nathan/homelab",
        normalization_enabled=True,
    )
    _, runtime = build_app(settings)
    job = build_runtime_scheduler(dataclasses.replace(runtime, read_only=False)).get_job(
        "normalization-sweep"
    )
    assert isinstance(job.trigger, CronTrigger)
    assert str(job.trigger) == (
        "cron[month='*', day='*', day_of_week='wed,sat', hour='7', minute='0']"
    )
    assert job.misfire_grace_time == 3600


def test_build_app_purges_old_events_at_startup(tmp_path):
    """SR2: `build_app` must run `purge_old_events` itself — a discovery
    engine that stopped calling it, or called it with the wrong setting,
    would otherwise never be caught, since every other test here uses a
    fresh `:memory:` database with nothing to purge in the first place."""
    db_path = str(tmp_path / "r.db")
    seed = RegistryStore(db_path)
    now = utcnow()
    with Session(seed.engine) as session:
        session.add(
            ChangeEvent(
                field="notes",
                old=None,
                new="old",
                actor="manual",
                created_at=now - timedelta(days=10),
            )
        )
        session.add(
            ChangeEvent(
                field="notes",
                old=None,
                new="new",
                actor="manual",
                created_at=now - timedelta(hours=1),
            )
        )
        session.commit()

    build_app(_settings(registry_db_path=db_path, event_retention_days=7, traefik_api_url=None))

    remaining = {e.new for e in seed.list_change_events()}
    assert remaining == {"new"}


def test_nothing_to_schedule_returns_none():
    _, runtime = build_app(_settings(traefik_api_url=None))
    assert build_runtime_scheduler(runtime) is None


def test_streamable_http_app_runs_scheduler_and_session_manager():
    server, runtime = build_app(_settings(mcp_transport="streamable-http"))
    scheduler = build_runtime_scheduler(runtime)
    app = http_app(server, runtime, scheduler)

    with TestClient(app) as client:
        assert scheduler.running
        # The SDK's own lifespan (the session manager) must still run inside ours.
        response = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            },
            headers={"Accept": "application/json, text/event-stream", "Host": "localhost"},
        )
        assert response.status_code == 200
    assert not scheduler.running


def test_sse_app_runs_scheduler():
    server, runtime = build_app(_settings(mcp_transport="sse"))
    scheduler = build_runtime_scheduler(runtime)

    with TestClient(http_app(server, runtime, scheduler)):
        assert scheduler.running
    assert not scheduler.running


async def test_stdio_runs_scheduler_for_the_session():
    _, runtime = build_app(_settings(mcp_transport="stdio"))
    scheduler = build_runtime_scheduler(runtime)
    seen: list[bool] = []

    class FakeStdioServer:
        async def run_stdio_async(self):
            seen.append(scheduler.running)

    await _run_stdio(FakeStdioServer(), runtime, scheduler)
    # AsyncIOScheduler.shutdown() hands the actual stop to the loop via
    # call_soon_threadsafe, so give it one turn before checking.
    await asyncio.sleep(0)

    assert seen == [True]
    assert not scheduler.running
