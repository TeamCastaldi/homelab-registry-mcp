"""PatchApproval: one Patchmon patch alert, parked until a human answers (ADR-020).

The Patchmon webhook never executes anything itself. A verified alert becomes a
`pending` row plus an email carrying two single-use links, Approve and Cancel.
Only a human following the Approve link and confirming the page it opens moves
the row on to execution; everything else (Cancel, the TTL running out, a second
click) resolves it without running anything.

The links carry random tokens; this table stores only their SHA-256 hashes, so a
copy of the database can't be replayed as a click.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel

from registry_mcp.models.service import new_uuid, utcnow


class PatchApprovalStatus(StrEnum):
    pending = "pending"  # email sent, awaiting a click
    approved = "approved"  # Approve confirmed; execution in progress
    cancelled = "cancelled"  # Cancel confirmed; nothing ran
    expired = "expired"  # TTL ran out before anyone answered; nothing ran
    executed = "executed"  # execution finished successfully
    failed = "failed"  # execution was attempted and did not succeed
    undelivered = "undelivered"  # the approval email could not be sent


class PatchApproval(SQLModel, table=True):
    """One row per Patchmon alert awaiting (or past) an operator's decision."""

    id: str = Field(default_factory=new_uuid, primary_key=True)
    event: str
    target_host: str = Field(index=True)
    service: str | None = None
    current_version: str | None = None
    target_version: str | None = None
    severity: str | None = None
    # PatchMon's own host UUID, when the alert carried one. PatchMon's API
    # addresses a host by this id, never by name.
    patchmon_host_id: str | None = None
    summary: str | None = None
    approve_token_hash: str = Field(index=True, unique=True)
    cancel_token_hash: str = Field(index=True, unique=True)
    status: PatchApprovalStatus = Field(default=PatchApprovalStatus.pending, index=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    expires_at: datetime = Field(index=True)
    resolved_at: datetime | None = None
    # "ansible" once the playbook path ran. Rows from before ADR-020's
    # 2026-09-27 amendment may read "patchmon" (its since-removed trigger API).
    executed_via: str | None = None
    detail: str | None = None
