"""Tests for the Ansible `setup` fact-gather parsing/mapping (Phase 9b)."""

from unittest.mock import AsyncMock, patch

import pytest

from registry_mcp.hardware import ansible_facts


def test_parse_setup_output_success():
    stdout = (
        'nas | SUCCESS => {"ansible_facts": {"ansible_hostname": "nas", '
        '"ansible_memtotal_mb": 16000}, "changed": false}'
    )
    facts_by_host, failures = ansible_facts.parse_setup_output(stdout)
    assert facts_by_host == {"nas": {"ansible_hostname": "nas", "ansible_memtotal_mb": 16000}}
    assert failures == {}


def test_parse_setup_output_unreachable():
    stdout = 'workload-02 | UNREACHABLE! => {"msg": "Failed to connect", "unreachable": true}'
    facts_by_host, failures = ansible_facts.parse_setup_output(stdout)
    assert facts_by_host == {}
    assert failures == {"workload-02": "Failed to connect"}


def test_parse_setup_output_mixed_hosts():
    stdout = "\n".join(
        [
            'a | SUCCESS => {"ansible_facts": {"ansible_hostname": "a"}}',
            'b | UNREACHABLE! => {"msg": "timed out"}',
            "not a matching line",
        ]
    )
    facts_by_host, failures = ansible_facts.parse_setup_output(stdout)
    assert set(facts_by_host) == {"a"}
    assert set(failures) == {"b"}


def test_parse_setup_output_ignores_garbage_lines():
    facts_by_host, failures = ansible_facts.parse_setup_output("PLAY [all] ***\n\nok: [a]")
    assert facts_by_host == {}
    assert failures == {}


def test_node_fields_from_facts_maps_network_and_os():
    facts = {
        "ansible_default_ipv4": {"address": "10.0.0.5", "macaddress": "aa:bb:cc:dd:ee:ff"},
        "ansible_distribution": "Debian",
        "ansible_distribution_version": "12",
    }
    fields = ansible_facts.node_fields_from_facts(facts)
    assert fields["ip_address"] == "10.0.0.5"
    assert fields["mac_address"] == "aa:bb:cc:dd:ee:ff"
    assert fields["os"] == "Debian 12"


def test_node_fields_from_facts_maps_cpu_and_ram():
    facts = {
        "ansible_processor": ["0", "GenuineIntel", "Intel(R) Xeon(R) CPU E3-1275"],
        "ansible_processor_vcpus": 8,
        "ansible_memtotal_mb": 32768,
    }
    fields = ansible_facts.node_fields_from_facts(facts)
    assert fields["cpu_model"] == "Intel(R) Xeon(R) CPU E3-1275"
    assert fields["cpu_cores"] == 8
    assert fields["ram_gb"] == 32.0


def test_node_fields_from_facts_maps_disks():
    facts = {
        "ansible_devices": {
            "sda": {"size": "500.00 GB", "model": "Samsung SSD", "rotational": "0"},
            "loop0": {"size": "100.00 MB", "rotational": "0"},
            "sr0": {"size": "1.00 GB", "rotational": "1"},
        }
    }
    fields = ansible_facts.node_fields_from_facts(facts)
    assert len(fields["storage"]) == 1
    disk = fields["storage"][0]
    assert disk["device"] == "/dev/sda"
    assert disk["model"] == "Samsung SSD"
    assert disk["size_gb"] == 500.0
    assert disk["type"] == "ssd"


def test_node_fields_from_facts_empty_when_no_recognized_keys():
    assert ansible_facts.node_fields_from_facts({"some_unrelated_fact": 1}) == {}


def test_hostname_from_facts_prefers_ansible_hostname():
    assert (
        ansible_facts.hostname_from_facts({"ansible_hostname": "nas", "ansible_fqdn": "nas.lan"})
        == "nas"
    )


def test_hostname_from_facts_falls_back_to_fqdn():
    assert ansible_facts.hostname_from_facts({"ansible_fqdn": "nas.lan"}) == "nas.lan"


async def test_gather_facts_raises_on_missing_ansible_binary():
    with (
        patch.object(ansible_facts, "_run", new=AsyncMock(side_effect=FileNotFoundError())),
        pytest.raises(ansible_facts.AnsibleFactsError),
    ):
        await ansible_facts.gather_facts(
            pattern="all", ansible_cfg_path="/etc/ansible.cfg", ssh_key_path="/key"
        )


async def test_gather_facts_raises_when_no_output_and_stderr():
    with (
        patch.object(ansible_facts, "_run", new=AsyncMock(return_value=(1, "", "no inventory"))),
        pytest.raises(ansible_facts.AnsibleFactsError),
    ):
        await ansible_facts.gather_facts(
            pattern="all", ansible_cfg_path="/etc/ansible.cfg", ssh_key_path="/key"
        )


async def test_gather_facts_parses_stdout():
    stdout = 'nas | SUCCESS => {"ansible_facts": {"ansible_hostname": "nas"}}'
    with patch.object(ansible_facts, "_run", new=AsyncMock(return_value=(0, stdout, ""))):
        facts_by_host, failures = await ansible_facts.gather_facts(
            pattern="all", ansible_cfg_path="/etc/ansible.cfg", ssh_key_path="/key"
        )
    assert facts_by_host == {"nas": {"ansible_hostname": "nas"}}
    assert failures == {}
