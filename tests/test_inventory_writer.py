"""Tests for the comment-safe Ansible inventory YAML writer (ADR-015)."""

from __future__ import annotations

import textwrap

from ruamel.yaml import YAML

from registry_mcp.inventory.writer import upsert_host

_yaml = YAML()


def _read(path):
    with path.open("r", encoding="utf-8") as fh:
        return _yaml.load(fh)


class TestUpsertHost:
    def test_creates_file_and_skeleton_when_missing(self, tmp_path):
        inv = tmp_path / "inventory.yml"
        upsert_host(inv, "heimdall", "10.0.0.9", [])
        data = _read(inv)
        assert data["all"]["hosts"]["heimdall"]["ansible_host"] == "10.0.0.9"

    def test_adds_host_to_named_groups(self, tmp_path):
        inv = tmp_path / "inventory.yml"
        upsert_host(inv, "heimdall", "10.0.0.9", ["docker_hosts", "nas"])
        data = _read(inv)
        assert "heimdall" in data["all"]["children"]["docker_hosts"]["hosts"]
        assert "heimdall" in data["all"]["children"]["nas"]["hosts"]

    def test_preserves_existing_hosts_and_comments(self, tmp_path):
        inv = tmp_path / "inventory.yml"
        inv.write_text(
            textwrap.dedent(
                """\
                # top comment
                all:
                  hosts:
                    existing-host:
                      ansible_host: 10.0.0.5
                      ansible_user: pi  # inline comment
                """
            )
        )
        upsert_host(inv, "heimdall", "10.0.0.9", [])
        text = inv.read_text()
        assert "# top comment" in text
        assert "# inline comment" in text
        assert "ansible_user: pi" in text
        data = _read(inv)
        assert data["all"]["hosts"]["existing-host"]["ansible_host"] == "10.0.0.5"
        assert data["all"]["hosts"]["heimdall"]["ansible_host"] == "10.0.0.9"

    def test_updates_existing_host_ansible_host_without_clobbering_other_vars(self, tmp_path):
        inv = tmp_path / "inventory.yml"
        inv.write_text(
            textwrap.dedent(
                """\
                all:
                  hosts:
                    heimdall:
                      ansible_host: 10.0.0.1
                      ansible_user: pi
                """
            )
        )
        upsert_host(inv, "heimdall", "10.0.0.99", [])
        data = _read(inv)
        assert data["all"]["hosts"]["heimdall"]["ansible_host"] == "10.0.0.99"
        assert data["all"]["hosts"]["heimdall"]["ansible_user"] == "pi"

    def test_second_call_is_idempotent(self, tmp_path):
        inv = tmp_path / "inventory.yml"
        upsert_host(inv, "heimdall", "10.0.0.9", ["docker_hosts"])
        first = inv.read_text()
        upsert_host(inv, "heimdall", "10.0.0.9", ["docker_hosts"])
        second = inv.read_text()
        assert first == second

    def test_no_ansible_host_leaves_existing_value_untouched(self, tmp_path):
        inv = tmp_path / "inventory.yml"
        inv.write_text(
            textwrap.dedent(
                """\
                all:
                  hosts:
                    heimdall:
                      ansible_host: 10.0.0.1
                """
            )
        )
        upsert_host(inv, "heimdall", None, [])
        data = _read(inv)
        assert data["all"]["hosts"]["heimdall"]["ansible_host"] == "10.0.0.1"

    def test_does_not_duplicate_group_membership_across_calls(self, tmp_path):
        inv = tmp_path / "inventory.yml"
        upsert_host(inv, "heimdall", "10.0.0.9", ["nas"])
        upsert_host(inv, "heimdall", "10.0.0.9", ["nas"])
        data = _read(inv)
        assert list(data["all"]["children"]["nas"]["hosts"].keys()) == ["heimdall"]
