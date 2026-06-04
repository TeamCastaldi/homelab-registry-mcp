"""Tests for discovery sources, the reconciler/engine, scheduler, and tools."""

import httpx
import pytest
from mcp.server.fastmcp import FastMCP

from conftest import IsolatedSettings
from registry_mcp.discovery.authentik import AuthentikDiscoverySource
from registry_mcp.discovery.base import DiscoveredService
from registry_mcp.discovery.docker import DockerDiscoverySource
from registry_mcp.discovery.engine import DiscoveryEngine, build_sources
from registry_mcp.discovery.outpost import outpost_bases_from_containers
from registry_mcp.discovery.scheduler import build_scheduler
from registry_mcp.discovery.traefik import TraefikDiscoverySource
from registry_mcp.integrations.authentik.client import AuthentikClient
from registry_mcp.integrations.traefik.client import TraefikClient
from registry_mcp.models import AuthMode, SourceType
from registry_mcp.tools import register_discovery_tools


def _transport(routes):
    def handler(request: httpx.Request) -> httpx.Response:
        body = routes.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={})
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


class FakeSource:
    def __init__(self, source: SourceType, items: list[DiscoveredService]) -> None:
        self.source = source
        self.items = items

    async def discover(self) -> list[DiscoveredService]:
        return list(self.items)


def _disc(name: str, **kwargs) -> DiscoveredService:
    return DiscoveredService(
        source=SourceType.traefik,
        external_id=f"{name}@docker",
        name=name,
        traefik_router=f"{name}@docker",
        **kwargs,
    )


# --- sources --------------------------------------------------------------


async def test_traefik_source_parses_routers_and_forward_auth():
    routes = {
        "/api/http/routers": [
            {"name": "vault@docker", "rule": "Host(`vault.lan`)", "middlewares": ["ak@docker"]},
            {"name": "plex@docker", "rule": "Host(`plex.lan`)", "middlewares": []},
        ],
        "/api/http/middlewares": [{"name": "ak@docker", "type": "forwardAuth"}],
    }
    client = TraefikClient("http://t", transport=_transport(routes), backoff=0)
    items = {i.name: i for i in await TraefikDiscoverySource(client).discover()}
    assert items["vault"].auth_mode == AuthMode.forward_auth
    assert items["vault"].traefik_router == "vault@docker"
    assert items["vault"].urls == ["https://vault.lan"]
    assert items["plex"].auth_mode == AuthMode.none


async def test_traefik_outpost_sidecar_is_forward_auth():
    # prowlarr-proxy@docker has no auth middleware, but a prowlarr_outpost
    # container exists -> the outpost is the forward-auth layer, not a conflict.
    routes = {
        "/api/http/routers": [
            {"name": "prowlarr-proxy@docker", "rule": "Host(`prowlarr.lan`)", "middlewares": []},
        ],
        "/api/http/middlewares": [],
    }
    client = TraefikClient("http://t", transport=_transport(routes), backoff=0)
    resolver = lambda: outpost_bases_from_containers(  # noqa: E731 - tiny test stub
        [{"name": "prowlarr_outpost", "image": "ghcr.io/goauthentik/proxy:2024.10"}]
    )
    source = TraefikDiscoverySource(client, outpost_resolver=resolver)
    items = {i.name: i for i in await source.discover()}
    assert items["prowlarr"].auth_mode == AuthMode.forward_auth


async def test_traefik_outpost_sidecar_underscore_router_is_forward_auth():
    # Router named with an underscore suffix (e.g. a Compose service
    # `prowlarr_proxy`) must normalise to `prowlarr` so it lines up with the
    # `prowlarr_outpost` base; otherwise it would be mislabelled auth_mode=none.
    routes = {
        "/api/http/routers": [
            {"name": "prowlarr_proxy@docker", "rule": "Host(`prowlarr.lan`)", "middlewares": []},
        ],
        "/api/http/middlewares": [],
    }
    client = TraefikClient("http://t", transport=_transport(routes), backoff=0)
    resolver = lambda: outpost_bases_from_containers(  # noqa: E731 - tiny test stub
        [{"name": "prowlarr_outpost", "image": "ghcr.io/goauthentik/proxy:2024.10"}]
    )
    source = TraefikDiscoverySource(client, outpost_resolver=resolver)
    items = {i.name: i for i in await source.discover()}
    assert items["prowlarr"].auth_mode == AuthMode.forward_auth


async def test_traefik_no_outpost_stays_none_genuine_conflict():
    # gitea@docker has no auth middleware and no outpost container -> genuinely
    # unauthenticated, so auth_mode stays none and a conflict can still fire.
    routes = {
        "/api/http/routers": [
            {"name": "gitea@docker", "rule": "Host(`gitea.lan`)", "middlewares": []},
        ],
        "/api/http/middlewares": [],
    }
    client = TraefikClient("http://t", transport=_transport(routes), backoff=0)
    resolver = lambda: outpost_bases_from_containers([])  # noqa: E731 - no outposts running
    source = TraefikDiscoverySource(client, outpost_resolver=resolver)
    items = {i.name: i for i in await source.discover()}
    assert items["gitea"].auth_mode == AuthMode.none


async def test_traefik_outpost_resolver_failure_degrades_gracefully():
    # A resolver that raises must not fail discovery; falls back to none.
    routes = {
        "/api/http/routers": [
            {"name": "prowlarr-proxy@docker", "rule": "Host(`prowlarr.lan`)", "middlewares": []},
        ],
        "/api/http/middlewares": [],
    }
    client = TraefikClient("http://t", transport=_transport(routes), backoff=0)

    def _boom() -> set[str]:
        raise RuntimeError("docker unavailable")

    source = TraefikDiscoverySource(client, outpost_resolver=_boom)
    items = {i.name: i for i in await source.discover()}
    assert items["prowlarr"].auth_mode == AuthMode.none


def test_outpost_bases_detects_by_name_and_image():
    bases = outpost_bases_from_containers(
        [
            {"name": "prowlarr_outpost", "image": "ghcr.io/goauthentik/proxy:2024.10"},
            {"name": "sabnzbd-outpost", "image": "anything"},
            {"name": "radarr_proxy", "image": "ghcr.io/goauthentik/proxy"},  # by image
            {"name": "gitea", "image": "gitea/gitea:1.21"},  # not an outpost
        ]
    )
    assert bases == {"prowlarr", "sabnzbd", "radarr"}


async def test_docker_source_parses_labelled_containers():
    class _Image:
        tags = ["plexinc/pms:1.0"]

    class _Container:
        id = "abc123def4567890"
        name = "/plex"
        status = "running"
        labels = {
            "traefik.enable": "true",
            "traefik.http.routers.plex.rule": "Host(`plex.lan`)",
        }
        image = _Image()

    class _Docker:
        class containers:  # noqa: N801 - mimics docker SDK attribute
            @staticmethod
            def list(filters=None):
                return [_Container()]

    items = await DockerDiscoverySource(client=_Docker()).discover()
    assert items[0].name == "plex"
    assert items[0].external_id == "abc123def456"
    assert items[0].urls == ["https://plex.lan"]
    assert items[0].raw["image"] == "plexinc/pms:1.0"


async def test_authentik_source_links_proxy_provider():
    routes = {
        "/api/v3/core/applications/": {
            "results": [
                {"slug": "vaultwarden", "name": "Vaultwarden", "provider": 1},
            ]
        },
        "/api/v3/providers/all/": {
            "results": [
                {
                    "pk": 1,
                    "component": "ak-provider-proxy-form",
                    "external_host": "https://vault.lan",
                }
            ]
        },
    }
    client = AuthentikClient("https://a/api/v3", "t", transport=_transport(routes), backoff=0)
    items = await AuthentikDiscoverySource(client).discover()
    assert items[0].authentik_app_slug == "vaultwarden"
    assert items[0].auth_mode == AuthMode.forward_auth
    assert "https://vault.lan" in items[0].urls


# --- engine / reconciler --------------------------------------------------


async def test_new_service_autoregisters_then_stale_on_removal(store):
    source = FakeSource(
        SourceType.traefik, [_disc("plex", urls=["https://plex.lan"]), _disc("vault")]
    )
    engine = DiscoveryEngine(store, {SourceType.traefik: source}, stale_threshold=2)

    event = await engine.run_source(SourceType.traefik)
    assert event.status == "ok"
    assert event.items_new == 2
    services = store.list_services()
    assert {s.name for s in services} == {"plex", "vault"}
    assert all(not s.manual for s in services)  # discovered, not manual

    # plex is removed from the deployment
    source.items = [_disc("vault")]
    await engine.run_source(SourceType.traefik)  # first miss
    assert store.list_stale_services() == []
    await engine.run_source(SourceType.traefik)  # second miss -> stale
    assert [s.name for s in store.list_stale_services()] == ["plex"]
    assert store.get_service("plex") is not None  # marked stale, not deleted


async def test_rediscovery_clears_stale_and_emits_changes(store):
    source = FakeSource(SourceType.traefik, [_disc("plex")])
    engine = DiscoveryEngine(store, {SourceType.traefik: source}, stale_threshold=1)
    await engine.run_source(SourceType.traefik)
    source.items = []
    await engine.run_source(SourceType.traefik)  # threshold 1 -> immediately stale
    assert [s.name for s in store.list_stale_services()] == ["plex"]

    source.items = [_disc("plex", urls=["https://plex.lan"])]
    event = await engine.run_source(SourceType.traefik)
    assert store.list_stale_services() == []
    assert event.items_changed == 1  # urls enriched
    plex = store.get_service("plex")
    assert plex.urls == ["https://plex.lan"]


async def test_failed_source_records_failed_event(store):
    class Boom:
        source = SourceType.traefik

        async def discover(self):
            raise RuntimeError("traefik down")

    engine = DiscoveryEngine(store, {SourceType.traefik: Boom()})
    event = await engine.run_source(SourceType.traefik)
    assert event.status == "failed"
    assert event.error is not None and "traefik down" in event.error


# --- tools ----------------------------------------------------------------


@pytest.fixture
def discovery_server(store):
    source = FakeSource(SourceType.traefik, [_disc("plex")])
    engine = DiscoveryEngine(store, {SourceType.traefik: source}, stale_threshold=1)
    mcp = FastMCP(name="test")
    register_discovery_tools(mcp, engine)
    return mcp


async def call(server, name, args):
    return (await server.call_tool(name, args))[1]


async def test_tool_run_now_and_status(discovery_server):
    run = await call(discovery_server, "discovery_run_now", {"source": "traefik"})
    assert run["status"] == "ok"
    status = await call(discovery_server, "discovery_status", {})
    assert status["sources"]["traefik"]["items_new"] == 1


async def test_tool_run_all(discovery_server):
    result = await call(discovery_server, "discovery_run_now", {})
    assert len(result["items"]) == 1


async def test_tool_run_now_rejects_unknown_and_disabled(discovery_server):
    assert "error" in await call(discovery_server, "discovery_run_now", {"source": "nope"})
    assert "error" in await call(discovery_server, "discovery_run_now", {"source": "docker"})


async def test_tool_list_stale(discovery_server):
    await call(discovery_server, "discovery_run_now", {"source": "traefik"})  # plex seen
    # nothing stale yet
    assert await call(discovery_server, "discovery_list_stale", {}) == {"items": []}


# --- wiring ---------------------------------------------------------------


def test_build_sources_respects_config():
    only_traefik = build_sources(IsolatedSettings(traefik_api_url="http://t"))
    assert set(only_traefik) == {SourceType.traefik}

    full = build_sources(
        IsolatedSettings(
            traefik_api_url="http://t",
            authentik_api_url="http://a/api/v3",
            authentik_token="x",
            docker_base_url="unix:///var/run/docker.sock",
        )
    )
    assert {SourceType.traefik, SourceType.authentik, SourceType.docker} == set(full)


def test_build_scheduler_adds_one_job_per_source(store):
    engine = DiscoveryEngine(store, {SourceType.traefik: FakeSource(SourceType.traefik, [])})
    scheduler = build_scheduler(engine, IsolatedSettings())
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert "discovery-traefik" in job_ids
    assert "discovery-docker" not in job_ids
