"""Append-only event log schemas: change events and discovery events."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel

from registry_mcp.models.service import SourceType, new_uuid, utcnow

# Sentinel field names for whole-record change events.
FIELD_CREATED = "__created__"
FIELD_DELETED = "__deleted__"


class DiscoveryStatus(StrEnum):
    ok = "ok"
    partial = "partial"
    failed = "failed"


class ChangeEvent(SQLModel, table=True):
    """One row per registry mutation, from manual edit or discovery."""

    id: str = Field(default_factory=new_uuid, primary_key=True)
    service_id: str | None = Field(default=None, index=True)
    field: str
    old: str | None = Field(default=None)
    new: str | None = Field(default=None)
    actor: str
    created_at: datetime = Field(default_factory=utcnow, index=True)


class DiscoveryEvent(SQLModel, table=True):
    """One row per discovery pass. Populated by the discovery engine (Phase 6)."""

    id: str = Field(default_factory=new_uuid, primary_key=True)
    source: SourceType = Field(index=True)
    started_at: datetime = Field(default_factory=utcnow, index=True)
    finished_at: datetime | None = Field(default=None)
    status: DiscoveryStatus = Field(default=DiscoveryStatus.ok)
    items_seen: int = Field(default=0)
    items_new: int = Field(default=0)
    items_changed: int = Field(default=0)
    items_missing: int = Field(default=0)
    error: str | None = Field(default=None)
