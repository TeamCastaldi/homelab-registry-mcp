"""APScheduler wiring: run each enabled discovery source on its own interval."""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from registry_mcp.config import Settings
from registry_mcp.discovery.engine import DiscoveryEngine
from registry_mcp.models import SourceType


def build_scheduler(engine: DiscoveryEngine, settings: Settings) -> AsyncIOScheduler:
    """Create a scheduler with one interval job per enabled discovery source."""
    intervals = {
        SourceType.traefik: settings.discovery_traefik_interval_seconds,
        SourceType.docker: settings.discovery_docker_interval_seconds,
        SourceType.authentik: settings.discovery_authentik_interval_seconds,
    }
    scheduler = AsyncIOScheduler()
    for source in engine.sources:
        scheduler.add_job(
            engine.run_source,
            "interval",
            seconds=intervals[source],
            args=[source],
            id=f"discovery-{source.value}",
            replace_existing=True,
        )
    return scheduler
