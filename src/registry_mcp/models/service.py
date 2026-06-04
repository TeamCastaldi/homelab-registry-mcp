"""SQLModel table definitions for the service registry."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import JSON
from sqlmodel import Field, SQLModel


class Category(StrEnum):
    infra = "infra"
    app = "app"
    media = "media"
    monitoring = "monitoring"
    security = "security"
    other = "other"


class AuthMode(StrEnum):
    none = "none"
    forward_auth = "forward_auth"
    oauth2_proxy = "oauth2_proxy"
    oauth2_oidc = "oauth2_oidc"  # Authentik as OIDC IdP; app handles the client flow
    basic = "basic"
    internal = "internal"
    unknown = "unknown"


class SourceType(StrEnum):
    manual = "manual"
    traefik = "traefik"
    docker = "docker"
    network = "network"
    authentik = "authentik"


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_uuid() -> str:
    return str(uuid4())


class Service(SQLModel, table=True):
    """One row per logical service, regardless of how many sources see it."""

    id: str = Field(default_factory=new_uuid, primary_key=True)
    name: str = Field(index=True, unique=True)
    display_name: str
    category: Category = Field(default=Category.other, index=True)
    host: str | None = Field(default=None, index=True)
    urls: list[str] = Field(default_factory=list, sa_type=JSON)
    traefik_router: str | None = Field(default=None)
    authentik_app_slug: str | None = Field(default=None)
    auth_mode: AuthMode = Field(default=AuthMode.unknown)
    traefik_auth_mode: AuthMode | None = Field(default=None, nullable=True)
    authentik_auth_mode: AuthMode | None = Field(default=None, nullable=True)
    tags: list[str] = Field(default_factory=list, sa_type=JSON)
    notes: str = Field(default="")
    auth_mode_conflict: bool = Field(default=False)
    hardware_node_id: str | None = Field(default=None, nullable=True)
    manual_link: bool = Field(default=False)
    manual: bool = Field(default=True)
    stale: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    last_seen_at: datetime | None = Field(default=None)


class ServiceSource(SQLModel, table=True):
    """Provenance: every source that has reported a given service."""

    id: str = Field(default_factory=new_uuid, primary_key=True)
    service_id: str = Field(foreign_key="service.id", index=True)
    source: SourceType
    external_id: str
    raw: dict = Field(default_factory=dict, sa_type=JSON)
    missed_passes: int = Field(default=0)
    first_seen_at: datetime = Field(default_factory=utcnow)
    last_seen_at: datetime = Field(default_factory=utcnow)
