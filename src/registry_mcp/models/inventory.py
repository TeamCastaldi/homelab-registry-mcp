"""PendingInventoryWrite: a math-problem confirmation gate in front of the
Ansible inventory-sync tool (ADR-015).

`ansible-inventory-sync-node` is the only tool that writes to the operator's
real Ansible inventory file. Like `deletion/store.py`'s `PendingDeletion`,
the write is split into a request/confirm pair: the request step returns a
small arithmetic challenge (`x + y = ?`) that a human must solve; only a
matching answer, submitted before the row's short TTL, actually performs the
write. Kept as a sibling of `PendingDeletion` rather than folded into it —
this gates a write, not a delete, and the two are semantically distinct even
though the challenge mechanism is identical.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel

from registry_mcp.models.service import new_uuid, utcnow


class PendingInventoryWriteStatus(StrEnum):
    pending = "pending"
    confirmed = "confirmed"
    expired = "expired"
    failed = "failed"  # wrong answer — challenge invalidated, no retry


class PendingInventoryWrite(SQLModel, table=True):
    """One row per in-flight inventory-sync request -> confirm pause."""

    id: str = Field(default_factory=new_uuid, primary_key=True)
    node_id: str
    node_hostname: str
    x: int
    y: int
    status: PendingInventoryWriteStatus = Field(
        default=PendingInventoryWriteStatus.pending, index=True
    )
    actor: str
    created_at: datetime = Field(default_factory=utcnow, index=True)
    expires_at: datetime = Field(index=True)
