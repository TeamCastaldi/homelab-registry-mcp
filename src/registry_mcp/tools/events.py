"""Event log query tools."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from registry_mcp.models import SourceType
from registry_mcp.registry import RegistryStore


def register_event_tools(mcp: FastMCP, store: RegistryStore) -> None:
    """Register the event-log query tools on the server."""

    @mcp.tool()
    def events_list_discoveries(
        source: SourceType | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List recent discovery passes, newest first, optionally filtered by source."""
        events = store.list_discovery_events(
            source=source.value if source else None,
            limit=limit,
        )
        return [e.model_dump(mode="json") for e in events]

    @mcp.tool()
    def events_list_changes(
        service_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List recent registry change events, newest first, optionally for one service."""
        events = store.list_change_events(service_id=service_id, limit=limit)
        return [e.model_dump(mode="json") for e in events]

    @mcp.tool()
    def events_get_for_service(service_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """List all change events recorded for a single service, newest first."""
        events = store.list_change_events(service_id=service_id, limit=limit)
        return [e.model_dump(mode="json") for e in events]
