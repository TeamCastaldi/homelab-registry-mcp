"""Tests for the deletion confirmation gate: DeletionGateStore directly, plus
the registry_delete_service* / hardware-delete-node* MCP tool flows built on
top of it.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest

from registry_mcp.deletion import DeletionGateError, DeletionGateStore
from registry_mcp.models import DeletionEntityType, PendingDeletionStatus

# ---------------------------------------------------------------------------
# DeletionGateStore
# ---------------------------------------------------------------------------


@pytest.fixture
def gate(store):
    return DeletionGateStore(store.engine)


def _request(gate, **overrides):
    kwargs = dict(
        entity_type=DeletionEntityType.service,
        entity_id="svc-1",
        entity_label="plex",
        actor="manual:test",
        ttl_minutes=5,
    )
    kwargs.update(overrides)
    return gate.request(**kwargs)


class TestDeletionGateStore:
    def test_request_returns_single_digit_challenge(self, gate):
        challenge = _request(gate)
        assert 1 <= challenge.x <= 9
        assert 1 <= challenge.y <= 9
        assert challenge.status == PendingDeletionStatus.pending

    def test_confirm_correct_answer_marks_confirmed(self, gate):
        challenge = _request(gate)
        confirmed = gate.confirm(
            challenge.id, DeletionEntityType.service, challenge.x + challenge.y
        )
        assert confirmed.status == PendingDeletionStatus.confirmed
        assert confirmed.entity_id == "svc-1"

    def test_confirm_wrong_answer_invalidates_challenge(self, gate):
        challenge = _request(gate)
        wrong = challenge.x + challenge.y + 1
        with pytest.raises(DeletionGateError):
            gate.confirm(challenge.id, DeletionEntityType.service, wrong)
        assert gate.get(challenge.id).status == PendingDeletionStatus.failed

    def test_confirm_after_failed_rejects_even_correct_answer(self, gate):
        challenge = _request(gate)
        with pytest.raises(DeletionGateError):
            gate.confirm(challenge.id, DeletionEntityType.service, challenge.x + challenge.y + 1)
        with pytest.raises(DeletionGateError):
            gate.confirm(challenge.id, DeletionEntityType.service, challenge.x + challenge.y)

    def test_confirm_wrong_entity_type_raises(self, gate):
        challenge = _request(gate)
        with pytest.raises(DeletionGateError):
            gate.confirm(challenge.id, DeletionEntityType.hardware_node, challenge.x + challenge.y)
        assert gate.get(challenge.id).status == PendingDeletionStatus.pending

    def test_confirm_unknown_request_id_raises(self, gate):
        with pytest.raises(DeletionGateError):
            gate.confirm("nonexistent", DeletionEntityType.service, 1)

    def test_confirm_expired_raises_and_marks_expired(self, gate):
        challenge = _request(gate)
        with (
            patch(
                "registry_mcp.deletion.store.utcnow",
                return_value=challenge.expires_at + timedelta(hours=1),
            ),
            pytest.raises(DeletionGateError),
        ):
            gate.confirm(challenge.id, DeletionEntityType.service, challenge.x + challenge.y)
        assert gate.get(challenge.id).status == PendingDeletionStatus.expired

    def test_purge_expired_marks_stale_pending_challenges(self, gate):
        challenge = _request(gate)
        with patch(
            "registry_mcp.deletion.store.utcnow",
            return_value=challenge.expires_at + timedelta(hours=1),
        ):
            count = gate.purge_expired()
        assert count == 1
        assert gate.get(challenge.id).status == PendingDeletionStatus.expired


# ---------------------------------------------------------------------------
# registry_delete_service / registry_delete_service_confirm MCP tools
# ---------------------------------------------------------------------------


async def call(server, name, args):
    return (await server.call_tool(name, args))[1]


def _answer(challenge_text: str) -> int:
    x, _plus, y, *_rest = challenge_text.split(" ")
    return int(x) + int(y)


async def test_registry_delete_service_request_does_not_delete(server):
    added = await call(server, "registry_add_service", {"name": "plex", "display_name": "Plex"})
    service_id = added["id"]

    requested = await call(server, "registry_delete_service", {"id": service_id})
    assert "request_id" in requested
    assert "challenge" in requested

    still_there = await call(server, "registry_get_service", {"id_or_name": "plex"})
    assert still_there["id"] == service_id


async def test_registry_delete_service_confirm_correct_answer_deletes(server):
    added = await call(server, "registry_add_service", {"name": "plex", "display_name": "Plex"})
    service_id = added["id"]

    requested = await call(server, "registry_delete_service", {"id": service_id})
    confirmed = await call(
        server,
        "registry_delete_service_confirm",
        {"request_id": requested["request_id"], "answer": _answer(requested["challenge"])},
    )
    assert confirmed["deleted"] is True
    assert confirmed["id"] == service_id

    gone = await call(server, "registry_get_service", {"id_or_name": "plex"})
    assert "error" in gone


async def test_registry_delete_service_confirm_wrong_answer_leaves_service(server):
    added = await call(server, "registry_add_service", {"name": "plex", "display_name": "Plex"})
    service_id = added["id"]

    requested = await call(server, "registry_delete_service", {"id": service_id})
    wrong = await call(
        server,
        "registry_delete_service_confirm",
        {"request_id": requested["request_id"], "answer": _answer(requested["challenge"]) + 1},
    )
    assert "error" in wrong

    still_there = await call(server, "registry_get_service", {"id_or_name": "plex"})
    assert still_there["id"] == service_id


async def test_registry_delete_service_missing_id_returns_error(server):
    result = await call(server, "registry_delete_service", {"id": "nonexistent"})
    assert "error" in result


# ---------------------------------------------------------------------------
# hardware-delete-node / hardware-delete-node-confirm MCP tools
# ---------------------------------------------------------------------------


async def test_hardware_delete_node_request_does_not_delete(server):
    added = await call(
        server,
        "hardware-add-node",
        {"hostname": "workload-01", "display_name": "Workload 01"},
    )
    node_id = added["id"]

    requested = await call(server, "hardware-delete-node", {"id": node_id})
    assert "request_id" in requested
    assert "challenge" in requested

    still_there = await call(server, "hardware-get-node", {"id": node_id})
    assert still_there["id"] == node_id


async def test_hardware_delete_node_confirm_correct_answer_deletes(server):
    added = await call(
        server,
        "hardware-add-node",
        {"hostname": "workload-01", "display_name": "Workload 01"},
    )
    node_id = added["id"]

    requested = await call(server, "hardware-delete-node", {"id": node_id})
    confirmed = await call(
        server,
        "hardware-delete-node-confirm",
        {"request_id": requested["request_id"], "answer": _answer(requested["challenge"])},
    )
    assert confirmed["deleted"] is True
    assert confirmed["id"] == node_id

    gone = await call(server, "hardware-get-node", {"id": node_id})
    assert "error" in gone


async def test_hardware_delete_node_confirm_wrong_answer_leaves_node(server):
    added = await call(
        server,
        "hardware-add-node",
        {"hostname": "workload-01", "display_name": "Workload 01"},
    )
    node_id = added["id"]

    requested = await call(server, "hardware-delete-node", {"id": node_id})
    wrong = await call(
        server,
        "hardware-delete-node-confirm",
        {"request_id": requested["request_id"], "answer": _answer(requested["challenge"]) + 1},
    )
    assert "error" in wrong

    still_there = await call(server, "hardware-get-node", {"id": node_id})
    assert still_there["id"] == node_id
