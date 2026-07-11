"""Tests for cross-source linking and the aggregated full-context tool."""

import httpx
import pytest

import registry_mcp.tools.linking as linking_tools
from conftest import IsolatedSettings
from registry_mcp.discovery.base import DiscoveredService
from registry_mcp.discovery.engine import DiscoveryEngine
from registry_mcp.models import AuthMode, SourceType
from registry_mcp.server import build_server

TRAEFIK_BASE = "http://traefik.test"
AUTHENTIK_BASE = "https://auth.test/api/v3"

ROUTES = {
    "/api/http/routers/vault@docker": {
        "name": "vault@docker",
        "status": "enabled",
        "rule": "Host(`vault.lan`)",
        "middlewares": ["authentik@docker"],
    },
    "/api/v3/core/applications/vaultwarden/": {
        "slug": "vaultwarden",
        "name": "Vaultwarden",
        "provider": 1,
    },
}


def _transport():
    def handler(request: httpx.Request) -> httpx.Response:
        body = ROUTES.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"detail": "not found"})
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


class FakeSource:
    def __init__(self, source: SourceType, items: list[DiscoveredService]) -> None:
        self.source = source
        self.items = items

    async def discover(self) -> list[DiscoveredService]:
        return list(self.items)


async def call(server, name, args):
    return (await server.call_tool(name, args))[1]


# --- automatic linking via the reconciler ---------------------------------


async def test_authentik_links_to_traefik_by_host(store):
    # Different short names, same host: linking must happen on the URL host.
    traefik_item = DiscoveredService(
        source=SourceType.traefik,
        external_id="vault@docker",
        name="vault",
        traefik_router="vault@docker",
        urls=["https://vault.lan"],
    )
    authentik_item = DiscoveredService(
        source=SourceType.authentik,
        external_id="vaultwarden",
        name="vaultwarden",
        authentik_app_slug="vaultwarden",
        urls=["https://vault.lan"],
        auth_mode=AuthMode.forward_auth,
    )
    engine = DiscoveryEngine(
        store,
        {
            SourceType.traefik: FakeSource(SourceType.traefik, [traefik_item]),
            SourceType.authentik: FakeSource(SourceType.authentik, [authentik_item]),
        },
    )
    await engine.run_source(SourceType.traefik)
    await engine.run_source(SourceType.authentik)

    services = store.list_services()
    assert len(services) == 1  # resolved into a single linked service
    service = services[0]
    assert service.traefik_router == "vault@docker"
    assert service.authentik_app_slug == "vaultwarden"
    assert service.auth_mode == AuthMode.forward_auth


# --- manual linking tool --------------------------------------------------


async def test_service_link_authentik(tmp_path):
    server = build_server(IsolatedSettings(registry_db_path=str(tmp_path / "r.db")))
    added = await call(server, "registry_add_service", {"name": "vault", "display_name": "Vault"})
    linked = await call(
        server,
        "service_link_authentik",
        {"service_id": added["id"], "app_slug": "vaultwarden"},
    )
    assert linked["authentik_app_slug"] == "vaultwarden"


async def test_service_link_authentik_missing_service(tmp_path):
    server = build_server(IsolatedSettings(registry_db_path=str(tmp_path / "r.db")))
    result = await call(server, "service_link_authentik", {"service_id": "nope", "app_slug": "x"})
    assert "error" in result


# --- aggregated full-context tool -----------------------------------------


@pytest.fixture
def context_server(tmp_path, monkeypatch):
    transport = _transport()
    real_traefik = linking_tools.TraefikClient
    real_authentik = linking_tools.AuthentikClient

    def traefik_factory(base_url, **kwargs):
        kwargs["transport"] = transport
        kwargs["backoff"] = 0
        return real_traefik(base_url, **kwargs)

    def authentik_factory(base_url, token, **kwargs):
        kwargs["transport"] = transport
        kwargs["backoff"] = 0
        return real_authentik(base_url, token, **kwargs)

    monkeypatch.setattr(linking_tools, "TraefikClient", traefik_factory)
    monkeypatch.setattr(linking_tools, "AuthentikClient", authentik_factory)
    return build_server(
        IsolatedSettings(
            registry_db_path=str(tmp_path / "r.db"),
            traefik_api_url=TRAEFIK_BASE,
            authentik_api_url=AUTHENTIK_BASE,
            authentik_token="t",
        )
    )


async def test_full_context_resolves_chain(context_server):
    added = await call(
        context_server,
        "registry_add_service",
        {
            "name": "vault",
            "display_name": "Vault",
            "traefik_router": "vault@docker",
            "authentik_app_slug": "vaultwarden",
        },
    )
    context = await call(context_server, "service_get_full_context", {"id": added["id"]})

    assert context["service"]["name"] == "vault"
    assert context["traefik_router"]["status"] == "enabled"
    assert context["authentik_application"]["slug"] == "vaultwarden"
    assert any(e["field"] == "__created__" for e in context["recent_events"])


async def test_full_context_unlinked_service_has_null_sections(context_server):
    added = await call(
        context_server, "registry_add_service", {"name": "plain", "display_name": "Plain"}
    )
    context = await call(context_server, "service_get_full_context", {"id": added["id"]})
    assert context["traefik_router"] is None
    assert context["authentik_application"] is None


async def test_full_context_missing_service(context_server):
    result = await call(context_server, "service_get_full_context", {"id": "ghost"})
    assert "error" in result


# --- pre-update compatibility check prompt ---------------------------------


async def test_pre_update_compatibility_check_prompt(tmp_path):
    server = build_server(IsolatedSettings(registry_db_path=str(tmp_path / "r.db")))
    prompt = await server.get_prompt("pre_update_compatibility_check", {"name": "vault"})
    text = prompt.messages[0].content.text
    assert "vault" in text
    assert "registry_get_service" in text
    assert "service_get_full_context" in text
