"""Discovery source protocol and the shared discovered-service shape."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from registry_mcp.models import AuthMode, Category, SourceType


class DiscoveredService(BaseModel):
    """A candidate service reported by a discovery source for one pass."""

    source: SourceType
    external_id: str
    name: str
    display_name: str | None = None
    category: Category | None = None
    host: str | None = None
    urls: list[str] = Field(default_factory=list)
    traefik_router: str | None = None
    authentik_app_slug: str | None = None
    auth_mode: AuthMode | None = None
    tags: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class DiscoverySource(Protocol):
    """An authoritative source the engine can poll for running services."""

    source: SourceType

    async def discover(self) -> list[DiscoveredService]: ...
