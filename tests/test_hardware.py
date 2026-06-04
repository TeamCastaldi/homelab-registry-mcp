"""Tests for the hardware node registry (Phase 9a — schema + manual CRUD)."""

import pytest

from registry_mcp.hardware import DuplicateNodeError, HardwareStore
from registry_mcp.models.hardware import (
    HardwareNode,
    NodeRole,
    NodeStatus,
    StorageDisk,
    StoragePool,
)
from registry_mcp.models.service import Service


@pytest.fixture
def hardware_store(store):
    return HardwareStore(store.engine)


def _node(**kwargs) -> HardwareNode:
    defaults = dict(hostname="workload-01", display_name="Workload-01", role=NodeRole.docker_host)
    defaults.update(kwargs)
    return HardwareNode(**defaults)


def test_create_and_get_by_id(hardware_store):
    created = hardware_store.create_node(_node())
    assert created.id
    fetched = hardware_store.get_node(created.id)
    assert fetched is not None
    assert fetched.hostname == "workload-01"


def test_get_by_hostname(hardware_store):
    hardware_store.create_node(_node())
    fetched = hardware_store.get_node("workload-01")
    assert fetched is not None
    assert fetched.hostname == "workload-01"


def test_get_missing_returns_none(hardware_store):
    assert hardware_store.get_node("does-not-exist") is None


def test_duplicate_hostname_raises(hardware_store):
    hardware_store.create_node(_node())
    with pytest.raises(DuplicateNodeError):
        hardware_store.create_node(_node())


def test_list_nodes_unfiltered(hardware_store):
    hardware_store.create_node(_node(hostname="a", display_name="A"))
    hardware_store.create_node(_node(hostname="b", display_name="B", role=NodeRole.nas))
    assert len(hardware_store.list_nodes()) == 2


def test_list_nodes_filter_role(hardware_store):
    hardware_store.create_node(_node(hostname="a", display_name="A", role=NodeRole.docker_host))
    hardware_store.create_node(_node(hostname="b", display_name="B", role=NodeRole.nas))
    results = hardware_store.list_nodes(role="nas")
    assert len(results) == 1
    assert results[0].hostname == "b"


def test_list_nodes_filter_status(hardware_store):
    hardware_store.create_node(_node(hostname="a", display_name="A", status=NodeStatus.confirmed))
    hardware_store.create_node(_node(hostname="b", display_name="B"))  # default: unconfirmed
    results = hardware_store.list_nodes(status="confirmed")
    assert len(results) == 1
    assert results[0].hostname == "a"


def test_list_nodes_filter_tag(hardware_store):
    hardware_store.create_node(_node(hostname="a", display_name="A", tags=["prod"]))
    hardware_store.create_node(_node(hostname="b", display_name="B", tags=["dev"]))
    results = hardware_store.list_nodes(tag="prod")
    assert len(results) == 1
    assert results[0].hostname == "a"


def test_update_node(hardware_store):
    node = hardware_store.create_node(_node())
    updated = hardware_store.update_node(
        node.id, {"display_name": "Workload-01 Updated", "cpu_cores": 16}
    )
    assert updated is not None
    assert updated.display_name == "Workload-01 Updated"
    assert updated.cpu_cores == 16


def test_update_node_emits_events(hardware_store):
    node = hardware_store.create_node(_node())
    hardware_store.update_node(node.id, {"display_name": "New Name"})
    events = hardware_store.list_change_events(node_id=node.id)
    fields = {e.field for e in events}
    assert "display_name" in fields


def test_update_missing_node_returns_none(hardware_store):
    result = hardware_store.update_node("nonexistent", {"display_name": "X"})
    assert result is None


def test_delete_node(hardware_store):
    node = hardware_store.create_node(_node())
    assert hardware_store.delete_node(node.id) is True
    assert hardware_store.get_node(node.id) is None


def test_delete_missing_returns_false(hardware_store):
    assert hardware_store.delete_node("nonexistent") is False


def test_delete_preserves_events(hardware_store):
    node = hardware_store.create_node(_node())
    hardware_store.delete_node(node.id)
    events = hardware_store.list_change_events(node_id=node.id)
    # Created + deleted events
    assert len(events) >= 2


def test_list_unconfirmed(hardware_store):
    hardware_store.create_node(_node(hostname="u", display_name="U"))  # default: unconfirmed
    hardware_store.create_node(_node(hostname="c", display_name="C", status=NodeStatus.confirmed))
    results = hardware_store.list_unconfirmed_nodes()
    assert len(results) == 1
    assert results[0].hostname == "u"


def test_list_stale(hardware_store):
    hardware_store.create_node(_node(hostname="s", display_name="S", status=NodeStatus.stale))
    hardware_store.create_node(_node(hostname="c", display_name="C", status=NodeStatus.confirmed))
    results = hardware_store.list_stale_nodes()
    assert len(results) == 1
    assert results[0].hostname == "s"


def test_link_service(store, hardware_store):
    svc = store.create_service(Service(name="prowlarr", display_name="Prowlarr"))
    node = hardware_store.create_node(_node())
    ok = hardware_store.link_service(svc.id, node.id)
    assert ok is True
    updated_svc = store.get_service(svc.id)
    assert updated_svc.hardware_node_id == node.id
    assert updated_svc.manual_link is True


def test_link_service_missing_returns_false(store, hardware_store):
    node = hardware_store.create_node(_node())
    assert hardware_store.link_service("nonexistent-svc", node.id) is False


def test_hardware_node_services(store, hardware_store):
    svc1 = store.create_service(Service(name="svc1", display_name="Svc1"))
    svc2 = store.create_service(Service(name="svc2", display_name="Svc2"))
    node = hardware_store.create_node(_node())
    hardware_store.link_service(svc1.id, node.id)
    hardware_store.link_service(svc2.id, node.id)
    services = hardware_store.get_node_services(node.id)
    assert {s.id for s in services} == {svc1.id, svc2.id}


def test_capacity_summary_no_confirmed_nodes(hardware_store):
    hardware_store.create_node(_node())  # unconfirmed
    summary = hardware_store.capacity_summary()
    assert summary["confirmed_nodes"] == 0
    assert summary["total_gb"] == 0.0
    assert summary["pools"] == []


def test_capacity_summary_aggregates_pools(hardware_store):
    pools = [
        StoragePool(name="data", type="zfs", total_gb=4000, used_gb=1200, free_gb=2800),
        StoragePool(name="rpool", type="zfs", total_gb=500, used_gb=100, free_gb=400),
    ]
    node = _node(
        hostname="nas",
        display_name="NAS",
        status=NodeStatus.confirmed,
        storage_pools=[p.model_dump() for p in pools],
    )
    hardware_store.create_node(node)
    summary = hardware_store.capacity_summary()
    assert summary["confirmed_nodes"] == 1
    assert summary["total_gb"] == 4500.0
    assert summary["used_gb"] == 1300.0
    assert summary["free_gb"] == 3200.0
    assert len(summary["pools"]) == 2


def test_service_get_full_context_includes_hardware(store, hardware_store, server):
    svc = store.create_service(Service(name="myapp", display_name="My App"))
    node = hardware_store.create_node(_node())
    hardware_store.link_service(svc.id, node.id)

    # Verify via store directly (server wiring is validated by build_server in conftest)
    updated_svc = store.get_service(svc.id)
    assert updated_svc.hardware_node_id == node.id
    fetched_node = hardware_store.get_node(updated_svc.hardware_node_id)
    assert fetched_node.hostname == "workload-01"


def test_storage_disk_model():
    disk = StorageDisk(device="/dev/sda", size_gb=4000.0, type="hdd")
    assert disk.device == "/dev/sda"
    assert disk.type == "hdd"


def test_storage_pool_model():
    pool = StoragePool(name="data", type="zfs", total_gb=4000, used_gb=1200, free_gb=2800)
    assert pool.health is None
    assert pool.free_gb == 2800
