"""PendingDockhandAction CRUD over the shared registry SQLite engine (ADR-021).

Modelled on `inventory/store.py`, with one difference that matters here: the
challenge is consumed by a conditional UPDATE (`status='pending' AND
expires_at > now`) whose row count decides the winner, so two concurrent
confirms of the same challenge cannot both run the action.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from sqlalchemy import update
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from registry_mcp.models import PendingDockhandAction, PendingDockhandActionStatus
from registry_mcp.models.service import utcnow

_PENDING = PendingDockhandActionStatus.pending
_RESULT_MAX = 300


class DockhandActionGateError(Exception):
    """Raised by `confirm()` with a human-readable reason."""


def _naive_now() -> datetime:
    """SQLite round-trips `datetime` columns as naive, so compare naive UTC."""
    return utcnow().replace(tzinfo=None)


def _is_past(expires_at: datetime) -> bool:
    return expires_at.replace(tzinfo=None) < _naive_now()


def describe_action(row: PendingDockhandAction) -> str:
    """The action in words, shown with the challenge so the operator sees what
    they are about to allow."""
    where = f"in environment {row.environment_id}"
    if row.kind == "stack" and row.action == "redeploy":
        pulled = "after pulling its images" if row.pull else "without pulling images"
        return f"redeploy stack `{row.target}` {where}, recreating every container in it, {pulled}"
    return f"{row.action} {row.kind} `{row.target}` {where}"


def _one_line(text: str) -> str:
    return " ".join(text.split())[:_RESULT_MAX]


class DockhandActionGateStore:
    """Persistence for :class:`PendingDockhandAction` records."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def request(
        self,
        *,
        kind: str,
        environment_id: str,
        target: str,
        action: str,
        actor: str,
        ttl_minutes: int,
        pull: bool = False,
    ) -> PendingDockhandAction:
        pending = PendingDockhandAction(
            kind=kind,
            environment_id=environment_id,
            target=target,
            action=action,
            pull=pull,
            x=random.randint(1, 9),
            y=random.randint(1, 9),
            actor=actor,
            expires_at=utcnow() + timedelta(minutes=ttl_minutes),
        )
        with Session(self.engine) as session:
            session.add(pending)
            session.commit()
            session.refresh(pending)
            return pending

    def get(self, request_id: str) -> PendingDockhandAction | None:
        with Session(self.engine) as session:
            return session.get(PendingDockhandAction, request_id)

    def _resolve(
        self,
        session: Session,
        request_id: str,
        status: PendingDockhandActionStatus,
        *,
        only_if_unexpired: bool = False,
        result: str | None = None,
    ) -> bool:
        """Move a still-pending row to `status`. True only if this call did it."""
        condition = [
            PendingDockhandAction.id == request_id,
            PendingDockhandAction.status == _PENDING,
        ]
        if only_if_unexpired:
            condition.append(PendingDockhandAction.expires_at > _naive_now())
        outcome = session.execute(
            update(PendingDockhandAction)
            .where(*condition)
            .values(status=status, resolved_at=_naive_now(), result=result)
        )
        session.commit()
        return outcome.rowcount == 1

    def confirm(self, request_id: str, answer: int) -> PendingDockhandAction:
        """Validate and claim a pending challenge.

        Returns the stored action with `status` flipped to `executing` only when
        the answer is right, the challenge is unexpired, and no other confirm got
        there first. Every other case raises `DockhandActionGateError`, and a
        wrong answer or an expiry invalidates the challenge for good.
        """
        with Session(self.engine) as session:
            row = session.get(PendingDockhandAction, request_id)
            if row is None:
                raise DockhandActionGateError(
                    f"no Dockhand action challenge found for {request_id!r}"
                )
            if row.status != _PENDING:
                raise DockhandActionGateError(
                    f"challenge is {row.status.value}, not pending: request the action "
                    "again to get a new math problem"
                )
            if _is_past(row.expires_at):
                self._resolve(session, request_id, PendingDockhandActionStatus.expired)
                raise DockhandActionGateError(
                    "challenge expired: request the action again for a new problem"
                )
            correct = isinstance(answer, int) and not isinstance(answer, bool)
            if not correct or answer != row.x + row.y:
                self._resolve(session, request_id, PendingDockhandActionStatus.wrong_answer)
                raise DockhandActionGateError(
                    f"incorrect answer to {row.x} + {row.y}: request the action again "
                    "for a new problem"
                )
            claimed = self._resolve(
                session,
                request_id,
                PendingDockhandActionStatus.executing,
                only_if_unexpired=True,
            )
            if not claimed:
                raise DockhandActionGateError(
                    "challenge was already used or has just expired: request the action again"
                )
            session.expire_all()
            return session.get(PendingDockhandAction, request_id)

    def finish(self, request_id: str, *, ok: bool, summary: str) -> None:
        """Record how a claimed action ended. Only an `executing` row is touched."""
        status = PendingDockhandActionStatus.executed if ok else PendingDockhandActionStatus.failed
        with Session(self.engine) as session:
            session.execute(
                update(PendingDockhandAction)
                .where(
                    PendingDockhandAction.id == request_id,
                    PendingDockhandAction.status == PendingDockhandActionStatus.executing,
                )
                .values(status=status, resolved_at=_naive_now(), result=_one_line(summary))
            )
            session.commit()

    def purge_expired(self) -> int:
        """Mark any pending challenge past its TTL as expired. Returns the count."""
        expired = 0
        with Session(self.engine) as session:
            rows = session.exec(
                select(PendingDockhandAction).where(PendingDockhandAction.status == _PENDING)
            ).all()
            for row in rows:
                if _is_past(row.expires_at) and self._resolve(
                    session, row.id, PendingDockhandActionStatus.expired
                ):
                    expired += 1
        return expired
