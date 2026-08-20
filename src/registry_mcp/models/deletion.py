"""PendingDeletion: a math-problem confirmation gate in front of every hard
delete in this server.

`registry_delete_service` and `hardware-delete-node` are the only tools that
permanently remove a row (see `registry/store.py`'s `delete_service` and
`hardware/store.py`'s `delete_node`) — everywhere else, discovery marks
things `stale` rather than deleting them. Rather than letting a single tool
call perform that removal, deletion is split into a request/confirm pair:
the request step returns a small arithmetic challenge (`x + y = ?`) that a
human must solve; only a matching answer, submitted before the row's short
TTL, actually performs the delete. This is a human-in-the-loop friction
control against an agent (or a fat-fingered id) deleting something
irreversible — not a cryptographic one, so the numbers are deliberately
trivial single digits that appear in the challenge text itself.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel

from registry_mcp.models.service import new_uuid, utcnow


class DeletionEntityType(StrEnum):
    service = "service"
    hardware_node = "hardware_node"


class PendingDeletionStatus(StrEnum):
    pending = "pending"
    confirmed = "confirmed"
    expired = "expired"
    failed = "failed"  # wrong answer — challenge invalidated, no retry


class PendingDeletion(SQLModel, table=True):
    """One row per in-flight delete-request -> confirm pause."""

    id: str = Field(default_factory=new_uuid, primary_key=True)
    entity_type: DeletionEntityType
    entity_id: str
    entity_label: str
    x: int
    y: int
    status: PendingDeletionStatus = Field(default=PendingDeletionStatus.pending, index=True)
    actor: str
    created_at: datetime = Field(default_factory=utcnow, index=True)
    expires_at: datetime = Field(index=True)
