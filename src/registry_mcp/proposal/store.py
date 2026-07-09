"""Proposal CRUD over the shared registry SQLite engine."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from registry_mcp.models import FindingType, Proposal, ProposalStatus
from registry_mcp.models.service import utcnow

_OPEN_STATUSES = {ProposalStatus.open}


class ProposalStore:
    """Persistence for :class:`Proposal` records. Shares the RegistryStore engine."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._migrate(engine)

    @staticmethod
    def _migrate(engine) -> None:
        """Apply any missing columns to the proposal table (forward-only, additive
        only) — same convention as `RegistryStore._migrate()`, so a `registry.db`
        from before `last_comment_id` was added keeps working without a fresh DB."""
        with engine.connect() as conn:
            existing = {row[1] for row in conn.execute(text("PRAGMA table_info(proposal)"))}
            if "last_comment_id" not in existing:
                conn.execute(text("ALTER TABLE proposal ADD COLUMN last_comment_id INTEGER"))
                conn.commit()

    def create(self, proposal: Proposal) -> Proposal:
        with Session(self.engine) as session:
            session.add(proposal)
            session.commit()
            session.refresh(proposal)
            return proposal

    def get(self, proposal_id: str) -> Proposal | None:
        with Session(self.engine) as session:
            return session.get(Proposal, proposal_id)

    def save(self, proposal: Proposal) -> Proposal:
        with Session(self.engine) as session:
            session.add(proposal)
            session.commit()
            session.refresh(proposal)
            return proposal

    def find_open(self, service_id: str, finding_type: FindingType) -> Proposal | None:
        with Session(self.engine) as session:
            statement = select(Proposal).where(
                Proposal.service_id == service_id,
                Proposal.finding_type == finding_type,
                Proposal.status == ProposalStatus.open,
            )
            return session.exec(statement).first()

    def list_open(self, *, exclude_normalization: bool = False) -> list[Proposal]:
        with Session(self.engine) as session:
            statement = select(Proposal).where(Proposal.status == ProposalStatus.open)
            if exclude_normalization:
                statement = statement.where(Proposal.finding_type != FindingType.normalization)
            statement = statement.order_by(col(Proposal.created_at).desc())
            return list(session.exec(statement).all())

    def list_all(self, *, limit: int = 100) -> list[Proposal]:
        with Session(self.engine) as session:
            statement = select(Proposal).order_by(col(Proposal.created_at).desc()).limit(limit)
            return list(session.exec(statement).all())

    def set_status(
        self, proposal_id: str, status: ProposalStatus, *, resolved: bool = False
    ) -> Proposal | None:
        with Session(self.engine) as session:
            proposal = session.get(Proposal, proposal_id)
            if proposal is None:
                return None
            proposal.status = status
            if resolved:
                proposal.resolved_at = utcnow()
            session.add(proposal)
            session.commit()
            session.refresh(proposal)
            return proposal
