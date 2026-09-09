"""Discover services from Dockhand-managed containers."""

from __future__ import annotations

from registry_mcp.discovery.base import DiscoveredService
from registry_mcp.integrations.dockhand.client import DockhandClient
from registry_mcp.models import SourceType


class DockhandDiscoverySource:
    """Every Dockhand-visible container becomes a candidate service.

    Deliberately minimal: no display_name/category/urls/auth_mode inference —
    Dockhand carries no routing or auth information, unlike Traefik/Authentik.
    A container Dockhand and Docker both see reconciles to the same `Service`
    row (`registry/reconcile.py` matches by `name` first), so this source's
    only job is correct `name`/`external_id` population plus useful
    provenance in `raw` (stack + environment context) — live status (pending
    updates, CVE counts) stays a per-call tool query, never baked into the
    registry row.
    """

    source = SourceType.dockhand

    def __init__(self, client: DockhandClient) -> None:
        self._client = client

    async def discover(self) -> list[DiscoveredService]:
        environments = {e.get("id"): e for e in await self._client.list_environments()}
        stacks = {s.get("id"): s for s in await self._client.list_stacks()}
        containers = await self._client.list_containers()

        discovered: list[DiscoveredService] = []
        for container in containers:
            # Mirrors DockerDiscoverySource's own normalization — matching
            # between the two sources depends on both producing the same name.
            name = (container.get("name") or "").lstrip("/")
            if not name:
                continue
            discovered.append(
                DiscoveredService(
                    source=SourceType.dockhand,
                    external_id=str(container.get("id")),
                    name=name,
                    raw={
                        "container": container,
                        "stack": stacks.get(container.get("stack_id")),
                        "environment": environments.get(container.get("environment_id")),
                    },
                )
            )
        return discovered
