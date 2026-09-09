"""Tests for the Dockhand discovery source and its reconciliation behavior."""

import httpx

from registry_mcp.discovery.dockhand import DockhandDiscoverySource
from registry_mcp.integrations.dockhand.client import DockhandClient
from registry_mcp.models import SourceType


def _transport(routes):
    def handler(request: httpx.Request) -> httpx.Response:
        body = routes.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={})
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


async def test_dockhand_source_parses_containers_with_stack_and_environment_context():
    routes = {
        "/api/environments": [{"id": "env1", "name": "homelab-docker"}],
        "/api/stacks": [{"id": "stack1", "name": "traefik-stack", "environment_id": "env1"}],
        "/api/containers": [
            {"id": "c1", "name": "/traefik", "stack_id": "stack1", "environment_id": "env1"}
        ],
    }
    client = DockhandClient("http://d", "t", transport=_transport(routes), backoff=0)
    items = await DockhandDiscoverySource(client).discover()
    assert len(items) == 1
    assert items[0].name == "traefik"  # leading "/" stripped, matching Docker's normalization
    assert items[0].source == SourceType.dockhand
    assert items[0].external_id == "c1"
    assert items[0].raw["stack"]["name"] == "traefik-stack"
    assert items[0].raw["environment"]["name"] == "homelab-docker"


async def test_dockhand_source_handles_standalone_container_with_no_stack():
    routes = {
        "/api/environments": [{"id": "env1", "name": "homelab-docker"}],
        "/api/stacks": [],
        "/api/containers": [{"id": "c2", "name": "adhoc", "environment_id": "env1"}],
    }
    client = DockhandClient("http://d", "t", transport=_transport(routes), backoff=0)
    items = await DockhandDiscoverySource(client).discover()
    assert items[0].name == "adhoc"
    assert items[0].raw["stack"] is None
    assert items[0].raw["environment"]["name"] == "homelab-docker"


async def test_dockhand_source_skips_containers_with_no_name():
    routes = {
        "/api/environments": [],
        "/api/stacks": [],
        "/api/containers": [{"id": "c3", "name": ""}],
    }
    client = DockhandClient("http://d", "t", transport=_transport(routes), backoff=0)
    items = await DockhandDiscoverySource(client).discover()
    assert items == []


async def test_dockhand_reconciles_against_existing_docker_discovered_service(store):
    from registry_mcp.discovery.base import DiscoveredService

    docker_item = DiscoveredService(
        source=SourceType.docker,
        external_id="abc123",
        name="traefik",
        urls=["https://traefik.lan"],
    )
    store.reconcile(SourceType.docker, [docker_item], stale_threshold=3)
    services = store.list_services()
    assert len(services) == 1
    service_id = services[0].id

    routes = {
        "/api/environments": [{"id": "env1", "name": "homelab-docker"}],
        "/api/stacks": [{"id": "stack1", "name": "traefik-stack", "environment_id": "env1"}],
        "/api/containers": [
            {"id": "c1", "name": "traefik", "stack_id": "stack1", "environment_id": "env1"}
        ],
    }
    client = DockhandClient("http://d", "t", transport=_transport(routes), backoff=0)
    dockhand_items = await DockhandDiscoverySource(client).discover()
    store.reconcile(SourceType.dockhand, dockhand_items, stale_threshold=3)

    services = store.list_services()
    assert len(services) == 1  # no duplicate row
    assert services[0].id == service_id
    assert store.get_source(service_id, SourceType.docker) is not None
    assert store.get_source(service_id, SourceType.dockhand) is not None
