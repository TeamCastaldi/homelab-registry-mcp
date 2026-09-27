"""PatchApproval persistence over the shared registry SQLite engine.

Each approval gets two random tokens, one per link. Only their SHA-256 hashes
are stored. `check()` is the read-only look a confirmation page needs;
`consume()` is the one-way `pending -> approved|cancelled` transition, done as a
conditional UPDATE so two clicks racing on the same approval can't both win.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select, update

from registry_mcp.models import PatchApproval, PatchApprovalStatus
from registry_mcp.models.service import utcnow

# A real token is 43 characters (32 random bytes, base64url). Anything much
# longer is not one of ours and isn't worth hashing.
_MAX_TOKEN_CHARS = 128


class ApprovalAction(StrEnum):
    approve = "approve"
    cancel = "cancel"


class TokenState(StrEnum):
    valid = "valid"  # pending and unexpired: this link can still act
    invalid = "invalid"  # no approval issued this token for this action
    expired = "expired"  # the TTL ran out before anyone answered
    used = "used"  # already resolved, by this link or the other one


@dataclass(frozen=True)
class TokenCheck:
    state: TokenState
    approval: PatchApproval | None = None


@dataclass(frozen=True)
class IssuedApproval:
    """A new approval plus the plain tokens for its two links. The tokens exist
    only here, long enough to be put in the email; the row holds hashes."""

    approval: PatchApproval
    approve_token: str
    cancel_token: str


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _is_past(expires_at: datetime) -> bool:
    """Compare against `utcnow()`, tolerating a naive `expires_at` — SQLite
    round-trips `datetime` columns as naive, the same quirk
    `DeletionGateStore._is_past` works around."""
    now = utcnow().replace(tzinfo=None) if expires_at.tzinfo is None else utcnow()
    return expires_at < now


class PatchApprovalStore:
    """Persistence for :class:`PatchApproval` records. Shares the RegistryStore engine."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create(self, approval: PatchApproval, ttl_minutes: int) -> IssuedApproval:
        """Persist `approval` as pending with fresh tokens and an expiry."""
        approve_token = secrets.token_urlsafe(32)
        cancel_token = secrets.token_urlsafe(32)
        approval.approve_token_hash = hash_token(approve_token)
        approval.cancel_token_hash = hash_token(cancel_token)
        approval.status = PatchApprovalStatus.pending
        approval.expires_at = utcnow() + timedelta(minutes=ttl_minutes)
        with Session(self.engine) as session:
            session.add(approval)
            session.commit()
            session.refresh(approval)
        return IssuedApproval(approval, approve_token, cancel_token)

    def get(self, approval_id: str) -> PatchApproval | None:
        with Session(self.engine) as session:
            return session.get(PatchApproval, approval_id)

    def find_pending(
        self,
        *,
        event: str,
        target_host: str,
        service: str | None,
        target_version: str | None,
    ) -> PatchApproval | None:
        """An unexpired pending approval for the same alert, if one exists.

        PatchMon re-sends on a non-2xx and may repeat an alert on its own; one
        open question per alert is enough.
        """

        def same(column, value):
            # `column = NULL` is never true in SQL; a missing value must match
            # a missing value.
            return col(column).is_(None) if value is None else col(column) == value

        with Session(self.engine) as session:
            statement = select(PatchApproval).where(
                PatchApproval.status == PatchApprovalStatus.pending,
                PatchApproval.event == event,
                PatchApproval.target_host == target_host,
                same(PatchApproval.service, service),
                same(PatchApproval.target_version, target_version),
            )
            for approval in session.exec(statement).all():
                if not _is_past(approval.expires_at):
                    return approval
        return None

    def check(self, token: str, action: ApprovalAction) -> TokenCheck:
        """Classify `token` for `action` without changing anything."""
        if not token or len(token) > _MAX_TOKEN_CHARS:
            return TokenCheck(TokenState.invalid)
        column = (
            PatchApproval.approve_token_hash
            if action is ApprovalAction.approve
            else PatchApproval.cancel_token_hash
        )
        with Session(self.engine) as session:
            statement = select(PatchApproval).where(column == hash_token(token))
            approval = session.exec(statement).first()
        if approval is None:
            return TokenCheck(TokenState.invalid)
        if approval.status == PatchApprovalStatus.expired:
            return TokenCheck(TokenState.expired, approval)
        if approval.status != PatchApprovalStatus.pending:
            return TokenCheck(TokenState.used, approval)
        if _is_past(approval.expires_at):
            return TokenCheck(TokenState.expired, approval)
        return TokenCheck(TokenState.valid, approval)

    def consume(self, token: str, action: ApprovalAction) -> TokenCheck:
        """Resolve the approval `token` belongs to, at most once.

        Returns `valid` with the updated row only for the one caller whose
        UPDATE moved it off `pending`; every other outcome leaves it unchanged,
        apart from marking a pending row found past its TTL as expired.
        """
        found = self.check(token, action)
        if found.state is TokenState.expired and found.approval is not None:
            self._expire(found.approval.id)
        if found.state is not TokenState.valid or found.approval is None:
            return found

        target = (
            PatchApprovalStatus.approved
            if action is ApprovalAction.approve
            else PatchApprovalStatus.cancelled
        )
        now = utcnow()
        with Session(self.engine) as session:
            moved = session.exec(
                update(PatchApproval)
                .where(
                    col(PatchApproval.id) == found.approval.id,
                    col(PatchApproval.status) == PatchApprovalStatus.pending,
                    col(PatchApproval.expires_at) > now,
                )
                .values(status=target, resolved_at=now)
            ).rowcount
            session.commit()
        current = self.get(found.approval.id)
        if moved != 1:
            # Another click got there first, or the TTL ran out in between.
            if current is not None and current.status == PatchApprovalStatus.pending:
                self._expire(current.id)
                return TokenCheck(TokenState.expired, self.get(current.id))
            return TokenCheck(TokenState.used, current)
        return TokenCheck(TokenState.valid, current)

    def record_outcome(
        self,
        approval_id: str,
        status: PatchApprovalStatus,
        *,
        executed_via: str | None = None,
        detail: str | None = None,
    ) -> None:
        with Session(self.engine) as session:
            approval = session.get(PatchApproval, approval_id)
            if approval is None:
                return
            approval.status = status
            approval.executed_via = executed_via
            approval.detail = detail
            approval.resolved_at = approval.resolved_at or utcnow()
            session.add(approval)
            session.commit()

    def _expire(self, approval_id: str) -> None:
        with Session(self.engine) as session:
            session.exec(
                update(PatchApproval)
                .where(
                    col(PatchApproval.id) == approval_id,
                    col(PatchApproval.status) == PatchApprovalStatus.pending,
                )
                .values(status=PatchApprovalStatus.expired)
            )
            session.commit()

    def purge_expired(self) -> int:
        """Mark any pending approval past its TTL as expired. Returns the count."""
        expired = 0
        with Session(self.engine) as session:
            statement = select(PatchApproval).where(
                PatchApproval.status == PatchApprovalStatus.pending
            )
            for approval in session.exec(statement).all():
                if not _is_past(approval.expires_at):
                    continue
                approval.status = PatchApprovalStatus.expired
                session.add(approval)
                expired += 1
            session.commit()
        return expired
