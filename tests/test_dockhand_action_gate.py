"""The Dockhand action confirmation gate (ADR-023): DockhandActionGateStore."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlmodel import Session

from registry_mcp.integrations.dockhand.gate import (
    DockhandActionGateError,
    DockhandActionGateStore,
    describe_action,
)
from registry_mcp.models import PendingDockhandAction, PendingDockhandActionStatus

S = PendingDockhandActionStatus
STORE = "registry_mcp.integrations.dockhand.gate.utcnow"


@pytest.fixture
def gate(store):
    return DockhandActionGateStore(store.engine)


def _request(gate, **overrides):
    kwargs = dict(
        kind="container",
        environment_id="2",
        target="abc123",
        action="restart",
        actor="manual:test",
        ttl_minutes=5,
    )
    kwargs.update(overrides)
    return gate.request(**kwargs)


def _later(challenge, **delta):
    return patch(STORE, return_value=challenge.expires_at + timedelta(**(delta or {"hours": 1})))


def test_a_request_stores_exactly_what_will_run_with_a_single_digit_challenge(gate):
    challenge = _request(gate, kind="stack", target="web", action="redeploy", pull=True)
    stored = gate.get(challenge.id)
    assert (stored.kind, stored.environment_id, stored.target, stored.action, stored.pull) == (
        "stack",
        "2",
        "web",
        "redeploy",
        True,
    )
    assert 1 <= stored.x <= 9
    assert 1 <= stored.y <= 9
    assert stored.status == S.pending


def test_the_correct_answer_claims_the_challenge_and_returns_the_stored_action(gate):
    challenge = _request(gate)
    claimed = gate.confirm(challenge.id, challenge.x + challenge.y)
    assert claimed.status == S.executing
    assert (claimed.kind, claimed.environment_id, claimed.target, claimed.action) == (
        "container",
        "2",
        "abc123",
        "restart",
    )
    assert claimed.resolved_at is not None


def test_a_challenge_can_be_claimed_only_once(gate):
    challenge = _request(gate)
    gate.confirm(challenge.id, challenge.x + challenge.y)
    with pytest.raises(DockhandActionGateError, match="executing"):
        gate.confirm(challenge.id, challenge.x + challenge.y)


def test_a_wrong_answer_invalidates_the_challenge_with_no_retry(gate):
    challenge = _request(gate)
    with pytest.raises(DockhandActionGateError, match="incorrect"):
        gate.confirm(challenge.id, challenge.x + challenge.y + 1)
    assert gate.get(challenge.id).status == S.wrong_answer
    with pytest.raises(DockhandActionGateError, match="wrong_answer"):
        gate.confirm(challenge.id, challenge.x + challenge.y)


@pytest.mark.parametrize("answer", [7.0, "7", None])
def test_an_answer_that_is_not_an_integer_is_wrong_even_when_it_equals_the_sum(gate, store, answer):
    challenge = _request(gate)
    with Session(store.engine) as session:
        row = session.get(PendingDockhandAction, challenge.id)
        row.x, row.y = 3, 4  # 7.0 == 7 in Python, so only the type check rejects it
        session.add(row)
        session.commit()
    with pytest.raises(DockhandActionGateError, match="incorrect"):
        gate.confirm(challenge.id, answer)
    assert gate.get(challenge.id).status == S.wrong_answer


def test_an_unknown_challenge_is_rejected(gate):
    with pytest.raises(DockhandActionGateError, match="no Dockhand action challenge"):
        gate.confirm("nonexistent", 1)


def test_an_expired_challenge_is_rejected_and_marked_expired(gate):
    challenge = _request(gate)
    with _later(challenge), pytest.raises(DockhandActionGateError, match="expired"):
        gate.confirm(challenge.id, challenge.x + challenge.y)
    assert gate.get(challenge.id).status == S.expired


def test_the_ttl_is_the_one_asked_for(gate):
    challenge = _request(gate, ttl_minutes=2)
    just_before = challenge.expires_at - timedelta(seconds=5)
    with patch(STORE, return_value=just_before):
        assert gate.confirm(challenge.id, challenge.x + challenge.y).status == S.executing


def test_a_challenge_that_expires_between_the_check_and_the_claim_does_not_run(gate):
    """The claim itself re-checks the expiry, so the check-then-act gap is closed."""
    challenge = _request(gate)
    with (
        # the pre-check sees it as live; the claim's own `expires_at > now` does not
        patch("registry_mcp.integrations.dockhand.gate._is_past", return_value=False),
        _later(challenge),
        pytest.raises(DockhandActionGateError, match="already used or has just expired"),
    ):
        gate.confirm(challenge.id, challenge.x + challenge.y)
    assert gate.get(challenge.id).status == S.pending


def test_a_row_already_claimed_elsewhere_cannot_be_claimed_again(gate, store):
    """Two confirms that both pass the read check: only one UPDATE can win."""
    challenge = _request(gate)
    answer = challenge.x + challenge.y
    original_get = Session.get
    seen = {"n": 0}

    def get_then_lose_the_race(self, entity, ident, *args, **kwargs):
        row = original_get(self, entity, ident, *args, **kwargs)
        if entity is PendingDockhandAction and seen["n"] == 0:
            seen["n"] += 1
            # the other confirm lands between this read and this claim
            with Session(store.engine) as other:
                stolen = original_get(other, PendingDockhandAction, ident)
                stolen.status = S.executing
                other.add(stolen)
                other.commit()
        return row

    with (
        patch.object(Session, "get", get_then_lose_the_race),
        pytest.raises(DockhandActionGateError, match="already used"),
    ):
        gate.confirm(challenge.id, answer)


def test_finish_records_the_outcome_of_a_claimed_action(gate):
    challenge = _request(gate)
    gate.confirm(challenge.id, challenge.x + challenge.y)
    gate.finish(challenge.id, ok=True, summary="HTTP 200\n  restarted")
    done = gate.get(challenge.id)
    assert done.status == S.executed
    assert done.result == "HTTP 200 restarted"


def test_finish_marks_a_failed_call_and_clips_the_summary(gate):
    challenge = _request(gate)
    gate.confirm(challenge.id, challenge.x + challenge.y)
    gate.finish(challenge.id, ok=False, summary="x" * 1000)
    done = gate.get(challenge.id)
    assert done.status == S.failed
    assert len(done.result) == 300


def test_finish_never_touches_a_challenge_that_was_not_claimed(gate):
    challenge = _request(gate)
    gate.finish(challenge.id, ok=True, summary="should not land")
    row = gate.get(challenge.id)
    assert row.status == S.pending
    assert row.result is None


def test_purge_expired_marks_only_stale_pending_challenges(gate):
    stale = _request(gate)
    fresh = _request(gate, ttl_minutes=60)
    claimed = _request(gate)
    gate.confirm(claimed.id, claimed.x + claimed.y)
    with _later(stale, minutes=1):
        assert gate.purge_expired() == 1
    assert gate.get(stale.id).status == S.expired
    assert gate.get(fresh.id).status == S.pending
    assert gate.get(claimed.id).status == S.executing


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        (
            {},
            "restart container `abc123` in environment 2",
        ),
        (
            {"kind": "stack", "target": "web", "action": "stop"},
            "stop stack `web` in environment 2",
        ),
        (
            {"kind": "stack", "target": "web", "action": "redeploy"},
            "redeploy stack `web` in environment 2, recreating every container in it, "
            "without pulling images",
        ),
        (
            {"kind": "stack", "target": "web", "action": "redeploy", "pull": True},
            "redeploy stack `web` in environment 2, recreating every container in it, "
            "after pulling its images",
        ),
    ],
)
def test_the_action_is_described_in_words(gate, overrides, expected):
    assert describe_action(_request(gate, **overrides)) == expected
