"""AdoptionDraft: a pending brownfield-adoption proposal awaiting a human
secret-handling decision (Phase 7).

`proposal_adopt_service` inspects a live container and asks the reasoning
layer to sanitize its compose file, but must not commit anything until a human
has chosen whether to keep the live secret values or rotate them. This table
is the durable pause point between those two tool calls.

The live secret values captured during inspection are held here, in the
(non-git-crypt-encrypted) registry SQLite, only long enough for the human to
answer — `expires_at` bounds that window and `AdoptionDraftStore.purge_expired`
sweeps anything left unanswered.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel
from sqlalchemy import JSON
from sqlmodel import Field, SQLModel

from registry_mcp.models.service import new_uuid, utcnow


class AdoptionDraftStatus(StrEnum):
    pending = "pending"
    finalized = "finalized"
    cancelled = "cancelled"
    expired = "expired"


class DetectedSecret(BaseModel):
    """One environment variable the reasoning layer flagged as a real secret."""

    key: str
    live_value: str


class AdoptionDraft(SQLModel, table=True):
    """One row per in-flight `proposal_adopt_service` → `_finalize` pause."""

    id: str = Field(default_factory=new_uuid, primary_key=True)
    service_id: str = Field(index=True)
    host: str
    ssh_user: str
    container_name: str
    compose_path: str  # absolute path of docker-compose.yml on the remote host
    target_file_path: str  # repo-relative path the sanitized compose lands at
    sanitized_compose: str = Field(default="")
    detected_secrets: list[DetectedSecret] = Field(default_factory=list, sa_type=JSON)
    confidence: float = Field(default=0.0)
    reasoning: str = Field(default="")
    status: AdoptionDraftStatus = Field(default=AdoptionDraftStatus.pending, index=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    expires_at: datetime = Field(index=True)
