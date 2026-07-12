"""Tests for the `hardware-discover-now` tool orchestration (Phase 9b)."""

from unittest.mock import AsyncMock, patch

from registry_mcp.hardware import ansible_facts
from registry_mcp.tools.hardware import _discover_now_unavailable, discover_now


def test_discover_now_unavailable_without_ansible_cfg(settings):
    assert "ANSIBLE_CFG_PATH" in _discover_now_unavailable(settings)


def test_discover_now_unavailable_without_ssh_key(settings):
    settings.ansible_cfg_path = "/opt/homelab/ansible.cfg"
    assert "SSH_KEY_PATH" in _discover_now_unavailable(settings)


def test_discover_now_available_when_both_configured(settings):
    settings.ansible_cfg_path = "/opt/homelab/ansible.cfg"
    settings.ssh_key_path = "/opt/homelab/.ssh/id_ed25519"
    assert _discover_now_unavailable(settings) is None


async def test_discover_now_creates_and_updates_nodes(hardware_store, settings):
    settings.ansible_cfg_path = "/opt/homelab/ansible.cfg"
    settings.ssh_key_path = "/opt/homelab/.ssh/id_ed25519"

    hardware_store.upsert_from_discovery(
        hostname="existing", ansible_host="10.0.0.9", ansible_groups=[], fields={}
    )

    facts_by_host = {
        "existing": {"ansible_hostname": "existing", "ansible_memtotal_mb": 8192},
        "10.0.0.7": {"ansible_hostname": "new-node", "ansible_memtotal_mb": 16384},
    }
    with patch.object(
        ansible_facts, "gather_facts", new=AsyncMock(return_value=(facts_by_host, {}))
    ):
        result = await discover_now(hardware_store, settings, host=None)

    assert result["status"] == "ok"
    assert result["pattern"] == "all"
    assert result["nodes_updated"] == ["existing"]
    assert result["nodes_created"] == ["new-node"]
    assert hardware_store.get_node("new-node").ram_gb == 16.0


async def test_discover_now_reports_failures(hardware_store, settings):
    settings.ansible_cfg_path = "/opt/homelab/ansible.cfg"
    settings.ssh_key_path = "/opt/homelab/.ssh/id_ed25519"

    with patch.object(
        ansible_facts,
        "gather_facts",
        new=AsyncMock(return_value=({}, {"unreachable-node": "Failed to connect"})),
    ):
        result = await discover_now(hardware_store, settings, host=None)

    assert result["status"] == "ok"
    assert result["nodes_created"] == []
    assert result["failures"] == {"unreachable-node": "Failed to connect"}


async def test_discover_now_uses_host_pattern(hardware_store, settings):
    settings.ansible_cfg_path = "/opt/homelab/ansible.cfg"
    settings.ssh_key_path = "/opt/homelab/.ssh/id_ed25519"

    with patch.object(
        ansible_facts, "gather_facts", new=AsyncMock(return_value=({}, {}))
    ) as mock_gather:
        result = await discover_now(hardware_store, settings, host="workload-01")

    assert result["pattern"] == "workload-01"
    assert mock_gather.call_args.kwargs["pattern"] == "workload-01"


async def test_discover_now_returns_error_on_ansible_failure(hardware_store, settings):
    settings.ansible_cfg_path = "/opt/homelab/ansible.cfg"
    settings.ssh_key_path = "/opt/homelab/.ssh/id_ed25519"

    with patch.object(
        ansible_facts,
        "gather_facts",
        new=AsyncMock(side_effect=ansible_facts.AnsibleFactsError("boom")),
    ):
        result = await discover_now(hardware_store, settings, host=None)

    assert result == {"status": "error", "error": "boom"}
