"""Event log query tools."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from registry_mcp.models import SourceType
from registry_mcp.registry import RegistryStore

# SQLite reads a negative LIMIT as "no limit", so a bare int let one call dump
# the whole event log. The bounds are validated and published in the input schema.
EventLimit = Annotated[int, Field(ge=1, le=1000, description="Events to return, 1-1000.")]


def register_event_tools(mcp: FastMCP, store: RegistryStore) -> None:
    """Register the event-log query tools on the server."""

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def events_list_discoveries(
        source: SourceType | None = None,
        limit: EventLimit = 100,
    ) -> list[dict[str, Any]]:
        """List recent discovery passes, newest first, optionally filtered by source."""
        events = store.list_discovery_events(
            source=source.value if source else None,
            limit=limit,
        )
        return [e.model_dump(mode="json") for e in events]

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def events_list_changes(
        service_id: str | None = None,
        limit: EventLimit = 100,
    ) -> list[dict[str, Any]]:
        """List recent registry change events, newest first, optionally for one service."""
        events = store.list_change_events(service_id=service_id, limit=limit)
        return [e.model_dump(mode="json") for e in events]

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def events_get_for_service(service_id: str, limit: EventLimit = 100) -> list[dict[str, Any]]:
        """List all change events recorded for a single service, newest first."""
        events = store.list_change_events(service_id=service_id, limit=limit)
        return [e.model_dump(mode="json") for e in events]
