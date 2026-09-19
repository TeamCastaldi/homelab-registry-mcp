"""Tests for the Ansible inventory-write confirmation gate (ADR-015):
InventoryGateStore directly.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest

from registry_mcp.inventory import InventoryGateError, InventoryGateStore
from registry_mcp.models import PendingInventoryWriteStatus


@pytest.fixture
def gate(store):
    return InventoryGateStore(store.engine)


def _request(gate, **overrides):
    kwargs = dict(
        node_id="node-1",
        node_hostname="heimdall",
        actor="manual:test",
        ttl_minutes=5,
    )
    kwargs.update(overrides)
    return gate.request(**kwargs)


class TestInventoryGateStore:
    def test_request_returns_single_digit_challenge(self, gate):
        challenge = _request(gate)
        assert 1 <= challenge.x <= 9
        assert 1 <= challenge.y <= 9
        assert challenge.status == PendingInventoryWriteStatus.pending

    def test_confirm_correct_answer_marks_confirmed(self, gate):
        challenge = _request(gate)
        confirmed = gate.confirm(challenge.id, challenge.x + challenge.y)
        assert confirmed.status == PendingInventoryWriteStatus.confirmed
        assert confirmed.node_hostname == "heimdall"

    def test_confirm_wrong_answer_invalidates_challenge(self, gate):
        challenge = _request(gate)
        wrong = challenge.x + challenge.y + 1
        with pytest.raises(InventoryGateError):
            gate.confirm(challenge.id, wrong)
        assert gate.get(challenge.id).status == PendingInventoryWriteStatus.failed

    def test_confirm_after_failed_rejects_even_correct_answer(self, gate):
        challenge = _request(gate)
        with pytest.raises(InventoryGateError):
            gate.confirm(challenge.id, challenge.x + challenge.y + 1)
        with pytest.raises(InventoryGateError):
            gate.confirm(challenge.id, challenge.x + challenge.y)

    def test_confirm_unknown_request_id_raises(self, gate):
        with pytest.raises(InventoryGateError):
            gate.confirm("nonexistent", 1)

    def test_confirm_expired_raises_and_marks_expired(self, gate):
        challenge = _request(gate)
        with (
            patch(
                "registry_mcp.inventory.store.utcnow",
                return_value=challenge.expires_at + timedelta(hours=1),
            ),
            pytest.raises(InventoryGateError),
        ):
            gate.confirm(challenge.id, challenge.x + challenge.y)
        assert gate.get(challenge.id).status == PendingInventoryWriteStatus.expired

    def test_purge_expired_marks_stale_pending_challenges(self, gate):
        challenge = _request(gate)
        with patch(
            "registry_mcp.inventory.store.utcnow",
            return_value=challenge.expires_at + timedelta(hours=1),
        ):
            count = gate.purge_expired()
        assert count == 1
        assert gate.get(challenge.id).status == PendingInventoryWriteStatus.expired
