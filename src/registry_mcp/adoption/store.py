"""AdoptionDraft CRUD over the shared registry SQLite engine (Phase 7)."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from registry_mcp.models import AdoptionDraft, AdoptionDraftStatus
from registry_mcp.models.service import utcnow


def _is_past(expires_at) -> bool:
    """Compare against `utcnow()`, tolerating a naive `expires_at` — SQLite
    round-trips `datetime` columns as naive regardless of how they were
    written, the same quirk `ProposalEngine._age_days` works around."""
    now = utcnow().replace(tzinfo=None) if expires_at.tzinfo is None else utcnow()
    return expires_at < now


class AdoptionDraftStore:
    """Persistence for :class:`AdoptionDraft` records. Shares the RegistryStore engine."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create(self, draft: AdoptionDraft) -> AdoptionDraft:
        with Session(self.engine) as session:
            session.add(draft)
            session.commit()
            session.refresh(draft)
            return draft

    def get(self, draft_id: str) -> AdoptionDraft | None:
        with Session(self.engine) as session:
            return session.get(AdoptionDraft, draft_id)

    def get_pending(self, draft_id: str) -> AdoptionDraft | None:
        """Return the draft only if it is still pending and unexpired."""
        draft = self.get(draft_id)
        if draft is None or draft.status != AdoptionDraftStatus.pending:
            return None
        if _is_past(draft.expires_at):
            self.set_status(draft_id, AdoptionDraftStatus.expired)
            return None
        return draft

    def set_status(self, draft_id: str, status: AdoptionDraftStatus) -> AdoptionDraft | None:
        with Session(self.engine) as session:
            draft = session.get(AdoptionDraft, draft_id)
            if draft is None:
                return None
            draft.status = status
            session.add(draft)
            session.commit()
            session.refresh(draft)
            return draft

    def purge_expired(self) -> int:
        """Mark any pending draft past its TTL as expired. Returns the count."""
        expired = 0
        with Session(self.engine) as session:
            statement = select(AdoptionDraft).where(
                AdoptionDraft.status == AdoptionDraftStatus.pending
            )
            for draft in session.exec(statement).all():
                if not _is_past(draft.expires_at):
                    continue
                draft.status = AdoptionDraftStatus.expired
                session.add(draft)
                expired += 1
            session.commit()
        return expired

    @staticmethod
    def ttl_expiry(ttl_minutes: int):
        return utcnow() + timedelta(minutes=ttl_minutes)
