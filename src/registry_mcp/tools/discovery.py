"""Discovery control and status tools."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from registry_mcp.discovery.engine import DiscoveryEngine
from registry_mcp.models import SourceType


def register_discovery_tools(mcp: FastMCP, engine: DiscoveryEngine) -> None:
    """Register tools to trigger discovery and inspect its results."""

    @mcp.tool()
    async def discovery_run_now(source: str | None = None) -> dict[str, Any]:
        """Run a discovery pass now. Without `source`, runs every enabled source.

        `source` must be one of the enabled sources (traefik, docker, authentik).
        """
        if source is None:
            events = await engine.run_all()
            return {"items": [e.model_dump(mode="json") for e in events]}
        try:
            source_type = SourceType(source)
        except ValueError:
            return {"error": f"unknown source {source!r}"}
        if source_type not in engine.sources:
            enabled = [s.value for s in engine.sources]
            return {"error": f"source {source!r} is not enabled; enabled: {enabled}"}
        event = await engine.run_source(source_type)
        return event.model_dump(mode="json")

    @mcp.tool()
    def discovery_status() -> dict[str, Any]:
        """Return the most recent discovery pass summary for each enabled source."""
        return {"sources": engine.status()}

    @mcp.tool()
    def discovery_list_stale() -> dict[str, Any]:
        """List services that have gone stale (not seen for the configured threshold)."""
        return {"items": [s.model_dump(mode="json") for s in engine.list_stale()]}
