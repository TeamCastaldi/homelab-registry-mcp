"""PendingDockhandAction: the math-problem gate in front of a Dockhand
container or stack action (ADR-023).

Like `PendingDeletion` and `PendingInventoryWrite`, the action is split into a
request/confirm pair: the request step stores *exactly what will run* and
returns a small arithmetic challenge (`x + y = ?`); only a matching answer,
submitted before the short TTL, runs it, and it runs the stored values, never
anything supplied at confirm time. A sibling of the other two rather than a new
entity type in `PendingDeletion` (this acts on a live host; it deletes nothing).

The row outlives the action: it keeps the outcome and a short result summary as
the audit trail, since a restart has no registry entity to hang a ChangeEvent on.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel

from registry_mcp.models.service import new_uuid, utcnow


class PendingDockhandActionStatus(StrEnum):
    pending = "pending"
    executing = "executing"  # answer accepted and claimed; the call is in flight
    executed = "executed"
    failed = "failed"  # the Dockhand call was made and errored
    expired = "expired"
    wrong_answer = "wrong_answer"  # challenge invalidated, no retry


class PendingDockhandAction(SQLModel, table=True):
    """One row per in-flight request -> confirm pause, kept after it resolves."""

    id: str = Field(default_factory=new_uuid, primary_key=True)
    kind: str  # "container" | "stack"
    environment_id: str
    target: str  # a container id or a stack name
    action: str
    pull: bool = False  # only meaningful for a stack redeploy
    x: int
    y: int
    status: PendingDockhandActionStatus = Field(
        default=PendingDockhandActionStatus.pending, index=True
    )
    actor: str
    created_at: datetime = Field(default_factory=utcnow, index=True)
    expires_at: datetime = Field(index=True)
    resolved_at: datetime | None = None
    result: str | None = None  # short summary of the outcome
