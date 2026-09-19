"""PendingInventoryWrite CRUD over the shared registry SQLite engine."""

from __future__ import annotations

import random
from datetime import timedelta

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from registry_mcp.models import PendingInventoryWrite, PendingInventoryWriteStatus
from registry_mcp.models.service import utcnow


class InventoryGateError(Exception):
    """Raised by `InventoryGateStore.confirm()` with a human-readable reason."""


def _is_past(expires_at) -> bool:
    """Compare against `utcnow()`, tolerating a naive `expires_at` — SQLite
    round-trips `datetime` columns as naive regardless of how they were
    written, the same quirk `DeletionGateStore._is_past` works around."""
    now = utcnow().replace(tzinfo=None) if expires_at.tzinfo is None else utcnow()
    return expires_at < now


class InventoryGateStore:
    """Persistence for :class:`PendingInventoryWrite` records. Shares the RegistryStore engine."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def request(
        self,
        node_id: str,
        node_hostname: str,
        actor: str,
        ttl_minutes: int,
    ) -> PendingInventoryWrite:
        challenge = PendingInventoryWrite(
            node_id=node_id,
            node_hostname=node_hostname,
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

    def get(self, request_id: str) -> PendingInventoryWrite | None:
        with Session(self.engine) as session:
            return session.get(PendingInventoryWrite, request_id)

    def confirm(self, request_id: str, answer: int) -> PendingInventoryWrite:
        """Validate and consume a pending challenge.

        Raises `InventoryGateError` with a human-readable reason for every
        failure case (not found, already resolved, expired, wrong answer).
        Only returns normally — with `status` flipped to `confirmed` — when
        the answer is correct and the challenge is still pending and
        unexpired.
        """
        with Session(self.engine) as session:
            challenge = session.get(PendingInventoryWrite, request_id)
            if challenge is None:
                raise InventoryGateError(f"no inventory-write challenge found for {request_id!r}")
            if challenge.status != PendingInventoryWriteStatus.pending:
                raise InventoryGateError(
                    f"challenge is {challenge.status.value}, not pending — call "
                    "ansible-inventory-sync-node again to get a new math problem"
                )
            if _is_past(challenge.expires_at):
                challenge.status = PendingInventoryWriteStatus.expired
                session.add(challenge)
                session.commit()
                raise InventoryGateError(
                    "challenge expired — call ansible-inventory-sync-node again for a new problem"
                )
            if answer != challenge.x + challenge.y:
                challenge.status = PendingInventoryWriteStatus.failed
                session.add(challenge)
                session.commit()
                raise InventoryGateError(
                    f"incorrect answer to {challenge.x} + {challenge.y} — call "
                    "ansible-inventory-sync-node again for a new problem"
                )
            challenge.status = PendingInventoryWriteStatus.confirmed
            session.add(challenge)
            session.commit()
            session.refresh(challenge)
            return challenge

    def purge_expired(self) -> int:
        """Mark any pending challenge past its TTL as expired. Returns the count."""
        expired = 0
        with Session(self.engine) as session:
            statement = select(PendingInventoryWrite).where(
                PendingInventoryWrite.status == PendingInventoryWriteStatus.pending
            )
            for challenge in session.exec(statement).all():
                if not _is_past(challenge.expires_at):
                    continue
                challenge.status = PendingInventoryWriteStatus.expired
                expired += 1
                session.add(challenge)
            session.commit()
        return expired
