"""Proposal model: tracks the lifecycle of each proposed remediation (Phase 8).

A proposal is the server's record of a degree-3 agentic action — it opens a
pull request and waits for a human to merge. The server never merges its own
PRs and never writes to the filesystem; this table is the audit trail of what
was proposed and how it resolved.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel

from registry_mcp.models.service import new_uuid, utcnow


class FindingType(StrEnum):
    auth_mode_conflict = "auth_mode_conflict"
    missing_auth = "missing_auth"
    missing_security_headers = "missing_security_headers"
    exposed_dashboard = "exposed_dashboard"
    normalization = "normalization"


class ProposalStatus(StrEnum):
    open = "open"
    merged = "merged"
    cancelled = "cancelled"
    snoozed = "snoozed"
    verified = "verified"
    rejected = "rejected"


class Proposal(SQLModel, table=True):
    """One row per proposed remediation, security or normalization."""

    id: str = Field(default_factory=new_uuid, primary_key=True)
    service_id: str | None = Field(default=None, index=True)
    finding_type: FindingType
    pr_url: str = Field(default="")
    pr_number: int | None = Field(default=None)
    branch: str = Field(default="")
    file_path: str = Field(default="")
    # The complete proposed file content (Git computes the actual diff on merge).
    diff: str = Field(default="")
    status: ProposalStatus = Field(default=ProposalStatus.open, index=True)
    rejection_reason: str | None = Field(default=None)
    confidence: float | None = Field(default=None)
    actor: str = Field(default="discovery")
    created_at: datetime = Field(default_factory=utcnow, index=True)
    resolved_at: datetime | None = Field(default=None)
