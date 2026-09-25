"""Discovery engine: runs sources, reconciles results, records discovery events."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from registry_mcp.config import Settings
from registry_mcp.discovery.authentik import AuthentikDiscoverySource
from registry_mcp.discovery.base import DiscoveredService, DiscoverySource
from registry_mcp.discovery.docker import DockerDiscoverySource
from registry_mcp.discovery.dockhand import DockhandDiscoverySource
from registry_mcp.discovery.traefik import TraefikDiscoverySource
from registry_mcp.dspy import Reasoner
from registry_mcp.integrations.authentik.client import AuthentikClient
from registry_mcp.integrations.dockhand.client import DockhandClient
from registry_mcp.integrations.traefik.client import TraefikClient
from registry_mcp.logging import get_logger
from registry_mcp.models import DiscoveryEvent, DiscoveryStatus, Service, SourceType
from registry_mcp.models.service import utcnow
from registry_mcp.registry import RegistryStore
from registry_mcp.registry.reconcile import match_service

_log = get_logger("discovery.engine")


def _service_summary(service: Service) -> dict[str, Any]:
    """Flatten a registry service into the dict shape the reasoning layer sees."""
    return {
        "name": service.name,
        "display_name": service.display_name,
        "category": service.category.value,
        "host": service.host,
        "urls": list(service.urls),
        "traefik_router": service.traefik_router,
        "authentik_app_slug": service.authentik_app_slug,
        "auth_mode": service.auth_mode.value,
    }


def _candidate_summary(item: DiscoveredService) -> dict[str, Any]:
    return {
        "source": item.source.value,
        "name": item.name,
        "display_name": item.display_name,
        "host": item.host,
        "urls": list(item.urls),
        "traefik_router": item.traefik_router,
        "authentik_app_slug": item.authentik_app_slug,
        "auth_mode": item.auth_mode.value if item.auth_mode else None,
    }


def build_sources(settings: Settings) -> dict[SourceType, DiscoverySource]:
    """Build the set of enabled discovery sources from configuration."""
    sources: dict[SourceType, DiscoverySource] = {}
    # Build Docker first so the Traefik source can consult it for outpost
    # sidecars; the resolver is best-effort and never required.
    docker_source = (
        DockerDiscoverySource(base_url=settings.docker_base_url)
        if settings.docker_base_url
        else None
    )
    if settings.traefik_api_url:
        sources[SourceType.traefik] = TraefikDiscoverySource(
            TraefikClient(
                settings.traefik_api_url,
                timeout=settings.traefik_timeout_seconds,
                retries=settings.traefik_retries,
            ),
            outpost_resolver=(docker_source.list_outpost_bases if docker_source else None),
        )
    if settings.authentik_api_url and settings.authentik_token:
        sources[SourceType.authentik] = AuthentikDiscoverySource(
            AuthentikClient(
                settings.authentik_api_url,
                settings.authentik_token.get_secret_value(),
                timeout=settings.authentik_timeout_seconds,
                retries=settings.authentik_retries,
            )
        )
    if docker_source is not None:
        sources[SourceType.docker] = docker_source
    if settings.dockhand_api_url and settings.dockhand_token:
        sources[SourceType.dockhand] = DockhandDiscoverySource(
            DockhandClient(
                settings.dockhand_api_url,
                settings.dockhand_token.get_secret_value(),
                timeout=settings.dockhand_timeout_seconds,
                retries=settings.dockhand_retries,
            )
        )
    return sources


class DiscoveryEngine:
    """Coordinates discovery passes and exposes their status."""

    def __init__(
        self,
        store: RegistryStore,
        sources: dict[SourceType, DiscoverySource],
        *,
        stale_threshold: int = 3,
        reasoner: Reasoner | None = None,
        on_pass_complete: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._store = store
        self._sources = sources
        self._stale_threshold = stale_threshold
        self._reasoner = reasoner
        self._on_pass_complete = on_pass_complete
        self._hook_lock = asyncio.Lock()
        self._hook_pending = False

    @property
    def sources(self) -> list[SourceType]:
        return list(self._sources)

    def _reconcile_extra(self) -> dict[str, Any]:
        """Build the optional reasoning callables passed to ``store.reconcile``.

        Returns an empty mapping when the reasoning layer is absent or disabled,
        so the deterministic path runs unchanged.
        """
        reasoner = self._reasoner
        if reasoner is None or not reasoner.enabled:
            return {}

        def identity_resolver(item: DiscoveredService, services: list[Service]) -> Service | None:
            existing = [_service_summary(s) for s in services]
            name = reasoner.resolve_identity(_candidate_summary(item), existing)
            if not name:
                return None
            return next((s for s in services if s.name == name), None)

        def metadata_enricher(item: DiscoveredService) -> dict[str, Any] | None:
            # Only Traefik-only discoveries carry the routing context the
            # InferServiceMetadata signature reasons from.
            if item.source != SourceType.traefik:
                return None
            raw = item.raw or {}
            return reasoner.infer_metadata(
                router_rule=str(raw.get("rule") or ""),
                middlewares=list(raw.get("middlewares") or []),
                service_name=raw.get("service") or item.name,
            )

        return {"identity_resolver": identity_resolver, "metadata_enricher": metadata_enricher}

    async def _reasoning_for(self, discovered: list[DiscoveredService]) -> dict[str, Any]:
        """Run the reasoning calls in a worker thread *before* reconcile opens its
        write session, and hand reconcile pure lookups of the answers.

        The calls are blocking LLM round-trips: made inside reconcile they froze
        the event loop (every MCP session, the webhook, the scheduler) and held
        SQLite's write lock for their whole duration. Only candidates with no
        deterministic match are sent — reconcile tries `match_service` first and
        consults the resolver only when it fails, so the answers line up.
        """
        extra = self._reconcile_extra()
        if not extra:
            return {}
        resolve, enrich = extra["identity_resolver"], extra["metadata_enricher"]
        services = self._store.list_services()
        matched: dict[str, str | None] = {}
        enriched: dict[str, dict[str, Any] | None] = {}

        def work() -> None:
            for item in discovered:
                if match_service(services, item) is not None:
                    continue
                hit = resolve(item, services)
                matched[item.external_id] = hit.name if hit else None
                if hit is None:
                    enriched[item.external_id] = enrich(item)

        await asyncio.to_thread(work)

        def identity_resolver(item: DiscoveredService, current: list[Service]) -> Service | None:
            name = matched.get(item.external_id)
            return next((s for s in current if s.name == name), None) if name else None

        def metadata_enricher(item: DiscoveredService) -> dict[str, Any] | None:
            return enriched.get(item.external_id)

        return {"identity_resolver": identity_resolver, "metadata_enricher": metadata_enricher}

    async def _pass_complete(self) -> None:
        """Run the pass-complete hook once at a time, coalescing requests.

        Sources run on their own intervals and usually fire together, so passes
        finish seconds apart. One hook run reads everything they have written,
        so a pass that finishes while a run is in progress only asks for one
        more run after it, rather than a run of its own. Serializing also keeps
        two runs from auto-creating a proposal for the same finding at once.
        """
        if self._on_pass_complete is None:
            return
        self._hook_pending = True
        if self._hook_lock.locked():
            return  # the run in progress loops once more for this request
        async with self._hook_lock:
            while self._hook_pending:
                self._hook_pending = False
                # The proposal sweep/auto-create hook; never let it break discovery.
                try:
                    await self._on_pass_complete()
                except Exception as exc:
                    _log.warning("on_pass_complete_failed", error=str(exc))

    async def run_source(self, source: SourceType) -> DiscoveryEvent:
        event = await self._discover(source)
        await self._pass_complete()
        return event

    async def run_all(self) -> list[DiscoveryEvent]:
        events = [await self._discover(source) for source in self._sources]
        await self._pass_complete()
        return events

    async def _discover(self, source: SourceType) -> DiscoveryEvent:
        started = utcnow()
        src = self._sources.get(source)
        if src is None:
            return self._store.record_discovery_event(
                source,
                started_at=started,
                finished_at=utcnow(),
                status=DiscoveryStatus.failed,
                error=f"discovery source {source} is not enabled",
            )
        try:
            discovered = await src.discover()
            reasoning = await self._reasoning_for(discovered)
            counts = self._store.reconcile(
                source,
                discovered,
                stale_threshold=self._stale_threshold,
                **reasoning,
            )
            status = DiscoveryStatus.ok
            error = None
        except Exception as exc:  # record a failed pass rather than crash the scheduler
            _log.warning("discovery_failed", source=str(source), error=str(exc))
            counts = {}
            status = DiscoveryStatus.failed
            error = str(exc)
        return self._store.record_discovery_event(
            source,
            started_at=started,
            finished_at=utcnow(),
            status=status,
            counts=counts,
            error=error,
        )

    def status(self) -> dict[str, dict | None]:
        result: dict[str, dict | None] = {}
        for source in self._sources:
            events = self._store.list_discovery_events(source=source.value, limit=1)
            result[source.value] = events[0].model_dump(mode="json") if events else None
        return result

    def list_stale(self) -> list[Service]:
        return self._store.list_stale_services()
