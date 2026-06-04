"""SQLModel table definitions for the hardware registry."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import JSON
from sqlmodel import Field, SQLModel


class NodeRole(StrEnum):
    pve_host = "pve_host"
    docker_host = "docker_host"
    nas = "nas"
    workstation = "workstation"
    pi = "pi"
    other = "other"


class NodeStatus(StrEnum):
    confirmed = "confirmed"
    unconfirmed = "unconfirmed"
    stale = "stale"
    offline = "offline"


class DiskType(StrEnum):
    hdd = "hdd"
    ssd = "ssd"
    nvme = "nvme"
    unknown = "unknown"


class PoolType(StrEnum):
    zfs = "zfs"
    lvm = "lvm"
    mdraid = "mdraid"
    btrfs = "btrfs"
    plain = "plain"
    unknown = "unknown"


class StorageDisk(BaseModel):
    device: str
    model: str | None = None
    size_gb: float
    type: DiskType = DiskType.unknown
    pool: str | None = None


class StoragePool(BaseModel):
    name: str
    type: PoolType = PoolType.unknown
    total_gb: float
    used_gb: float
    free_gb: float
    health: str | None = None
    mount_point: str | None = None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_uuid() -> str:
    return str(uuid4())


class HardwareNode(SQLModel, table=True):
    """One row per physical or virtual node in the homelab."""

    id: str = Field(default_factory=_new_uuid, primary_key=True)
    hostname: str = Field(index=True, unique=True)
    display_name: str
    role: NodeRole = Field(default=NodeRole.other, index=True)
    status: NodeStatus = Field(default=NodeStatus.unconfirmed, index=True)
    ip_address: str | None = Field(default=None)
    mac_address: str | None = Field(default=None)
    os: str | None = Field(default=None)
    cpu_model: str | None = Field(default=None)
    cpu_cores: int | None = Field(default=None)
    ram_gb: float | None = Field(default=None)
    gpu_model: str | None = Field(default=None)
    storage: list[StorageDisk] = Field(default_factory=list, sa_type=JSON)
    storage_pools: list[StoragePool] = Field(default_factory=list, sa_type=JSON)
    tags: list[str] = Field(default_factory=list, sa_type=JSON)
    notes: str = Field(default="")
    # Stub fields — schema only, no discovery logic in Phase 9
    location: str | None = Field(default=None)
    ups_backed: bool | None = Field(default=None)
    obtained_at: str | None = Field(default=None)  # ISO date string
    cluster_member: str | None = Field(default=None)
    # Provenance
    ansible_host: str | None = Field(default=None)
    ansible_groups: list[str] = Field(default_factory=list, sa_type=JSON)
    last_confirmed_at: datetime | None = Field(default=None)
    last_seen_at: datetime | None = Field(default=None)
    manual: bool = Field(default=True)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class HardwareChangeEvent(SQLModel, table=True):
    """Append-only audit log for hardware node field changes."""

    id: str = Field(default_factory=_new_uuid, primary_key=True)
    node_id: str | None = Field(default=None, index=True)
    field: str
    old: str | None = Field(default=None)
    new: str | None = Field(default=None)
    actor: str
    created_at: datetime = Field(default_factory=_utcnow)
