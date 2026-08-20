"""PendingDeletion CRUD over the shared registry SQLite engine."""

from __future__ import annotations

import random
from datetime import timedelta

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from registry_mcp.models import DeletionEntityType, PendingDeletion, PendingDeletionStatus
from registry_mcp.models.service import utcnow


class DeletionGateError(Exception):
    """Raised by `DeletionGateStore.confirm()` with a human-readable reason."""


def _is_past(expires_at) -> bool:
    """Compare against `utcnow()`, tolerating a naive `expires_at` — SQLite
    round-trips `datetime` columns as naive regardless of how they were
    written, the same quirk `AdoptionDraftStore._is_past` works around."""
    now = utcnow().replace(tzinfo=None) if expires_at.tzinfo is None else utcnow()
    return expires_at < now


class DeletionGateStore:
    """Persistence for :class:`PendingDeletion` records. Shares the RegistryStore engine."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def request(
        self,
        entity_type: DeletionEntityType,
        entity_id: str,
        entity_label: str,
        actor: str,
        ttl_minutes: int,
    ) -> PendingDeletion:
        challenge = PendingDeletion(
            entity_type=entity_type,
            entity_id=entity_id,
            entity_label=entity_label,
            x=random.randint(1, 9),
            y=random.randint(1, 9),
            actor=actor,
            expires_at=utcnow() + timedelta(minutes=ttl_minutes),
        )
        with Session(self.engine) as session:
            session.add(challenge)
            session.commit()
            session.refresh(challenge)
            return challenge

    def get(self, request_id: str) -> PendingDeletion | None:
        with Session(self.engine) as session:
            return session.get(PendingDeletion, request_id)

    def confirm(
        self, request_id: str, entity_type: DeletionEntityType, answer: int
    ) -> PendingDeletion:
        """Validate and consume a pending challenge.

        Raises `DeletionGateError` with a human-readable reason for every
        failure case (not found, wrong entity type, already resolved,
        expired, wrong answer). Only returns normally — with `status`
        flipped to `confirmed` — when the answer is correct and the
        challenge is still pending and unexpired.
        """
        with Session(self.engine) as session:
            challenge = session.get(PendingDeletion, request_id)
            if challenge is None:
                raise DeletionGateError(f"no deletion challenge found for {request_id!r}")
            if challenge.entity_type != entity_type:
                raise DeletionGateError(
                    f"request_id {request_id!r} was not issued for this delete tool"
                )
            if challenge.status != PendingDeletionStatus.pending:
                raise DeletionGateError(
                    f"challenge is {challenge.status.value}, not pending — call the delete "
                    "tool again to get a new math problem"
                )
            if _is_past(challenge.expires_at):
                challenge.status = PendingDeletionStatus.expired
                session.add(challenge)
                session.commit()
                raise DeletionGateError(
                    "challenge expired — call the delete tool again for a new problem"
                )
            if answer != challenge.x + challenge.y:
                challenge.status = PendingDeletionStatus.failed
                session.add(challenge)
                session.commit()
                raise DeletionGateError(
                    f"incorrect answer to {challenge.x} + {challenge.y} — call the delete "
                    "tool again for a new problem"
                )
            challenge.status = PendingDeletionStatus.confirmed
            session.add(challenge)
            session.commit()
            session.refresh(challenge)
            return challenge

    def purge_expired(self) -> int:
        """Mark any pending challenge past its TTL as expired. Returns the count."""
        expired = 0
        with Session(self.engine) as session:
            statement = select(PendingDeletion).where(
                PendingDeletion.status == PendingDeletionStatus.pending
            )
            for challenge in session.exec(statement).all():
                if not _is_past(challenge.expires_at):
                    continue
                challenge.status = PendingDeletionStatus.expired
                session.add(challenge)
                expired += 1
            session.commit()
        return expired
