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

import pytest
import structlog
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session, select
from starlette.testclient import TestClient

from conftest import IsolatedSettings
from registry_mcp.adoption import AdoptionDraftStore
from registry_mcp.auth.setup import AuthConfigError
from registry_mcp.models import AdoptionDraft, AdoptionDraftStatus, DiscoveryStatus, SourceType
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


def test_build_app_purges_expired_adoption_drafts_at_startup(tmp_path):
    """G3: pending adoption drafts hold captured live secret values until the
    operator answers — the startup purge that expires anything past its TTL
    (`AdoptionDraftStore.purge_expired`, same idiom as the deletion/inventory
    gates) was never actually exercised through `build_app` by any test."""
    db_path = str(tmp_path / "r.db")
    seed = RegistryStore(db_path)
    adoption_store = AdoptionDraftStore(seed.engine)
    now = utcnow()
    with Session(seed.engine) as session:
        session.add(
            AdoptionDraft(
                service_id="svc-1",
                host="workload-01",
                ssh_user="root",
                container_name="plex",
                compose_path="/opt/plex/docker-compose.yml",
                target_file_path="nodes/workload-01/plex/compose.yaml",
                expires_at=now - timedelta(minutes=5),
            )
        )
        session.commit()

    build_app(_settings(registry_db_path=db_path, traefik_api_url=None))

    with Session(adoption_store.engine) as session:
        draft = session.exec(select(AdoptionDraft)).one()
    assert draft.status == AdoptionDraftStatus.expired


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


# --- MCP OAuth wiring (ADR-024). End-to-end status codes are in test_mcp_auth.py.

_AUTH = dict(
    mcp_auth_enabled=True,
    mcp_auth_issuer_url="https://sso.example.com/application/o/registry-mcp/",
    mcp_auth_audience="registry-mcp-client-id",
    mcp_auth_required_group="registry-mcp-admins",
    mcp_auth_resource_url="https://registry-mcp.example.com/mcp",
)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"mcp_auth_required_group": None, "mcp_auth_resource_url": None},
            "missing MCP_AUTH_REQUIRED_GROUP, MCP_AUTH_RESOURCE_URL",
        ),
        ({"mcp_auth_issuer_url": "http://sso.example.com/"}, "MCP_AUTH_ISSUER_URL must be"),
        ({"mcp_auth_jwks_url": "http://sso.example.com/jwks/"}, "MCP_AUTH_JWKS_URL must be"),
        ({"mcp_transport": "sse"}, "needs MCP_TRANSPORT=streamable-http"),
    ],
)
def test_auth_on_but_unusable_refuses_to_start(overrides, message):
    with pytest.raises(AuthConfigError, match=message):
        build_app(_settings(**{**_AUTH, **overrides}))


def test_auth_off_on_http_warns_at_startup():
    with structlog.testing.capture_logs() as logs:
        build_app(_settings())
    assert "mcp_auth_disabled" in [e["event"] for e in logs]
    with structlog.testing.capture_logs() as logs:
        build_app(_settings(mcp_transport="stdio"))
    assert "mcp_auth_disabled" not in [e["event"] for e in logs]


def test_auth_on_replaces_the_sdks_metadata_route_and_gates_after_authentication():
    server, runtime = build_app(_settings(**_AUTH))
    app = http_app(server, runtime, None)
    well_known = [r for r in app.routes if getattr(r, "path", "").startswith("/.well-known/")]
    assert [r.path for r in well_known] == [
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
    ]
    order = [m.cls.__name__ for m in app.user_middleware]
    assert order == ["AuthenticationMiddleware", "AuthContextMiddleware", "RequireGroupMiddleware"]


@pytest.mark.parametrize("auth", [{}, _AUTH], ids=["auth-off", "auth-on"])
def test_health_answers_without_a_token_and_says_nothing_else(auth):
    server, runtime = build_app(_settings(**auth))
    with TestClient(http_app(server, runtime, None)) as client:
        response = client.get("/health", headers={"Host": "localhost"})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
