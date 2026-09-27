"""Tests for the comment-safe Ansible inventory YAML writer (ADR-015)."""

from __future__ import annotations

import textwrap

import pytest
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

    def test_group_membership_upsert_never_overwrites_an_existing_host_entry(self, tmp_path):
        """W1: `list(group_hosts.keys()) == ["heimdall"]` above can't fail by
        construction — a mapping's keys are already unique, so even a broken guard
        that unconditionally reassigns `group_hosts[hostname] = CommentedMap()` on
        every call would still show exactly one key. Pointing this at a
        *pre-populated* group-host entry pins the behavior the guard actually
        exists for (this module's own docstring's promise): an existing value
        under a host inside a group survives, and is only ever skipped, never
        replaced."""
        inv = tmp_path / "inventory.yml"
        inv.write_text(
            textwrap.dedent(
                """\
                all:
                  hosts:
                    heimdall:
                      ansible_host: 10.0.0.9
                  children:
                    nas:
                      hosts:
                        heimdall:
                          nas_export: /mnt/data  # hand-set, must survive
                """
            )
        )
        upsert_host(inv, "heimdall", "10.0.0.9", ["nas"])
        data = _read(inv)
        assert data["all"]["children"]["nas"]["hosts"]["heimdall"]["nas_export"] == "/mnt/data"


class TestNullEntriesAndAtomicWrite:
    """Ansible's YAML inventories routinely write a bare `host:` or `group:` —
    ruamel loads those as None, which `setdefault()` handed straight back."""

    def test_fills_a_null_host_entry_and_keeps_everything_else(self, tmp_path):
        inv = tmp_path / "inventory.yml"
        inv.write_text(
            textwrap.dedent(
                """\
                # managed by hand
                all:
                  hosts:
                    heimdall:
                    waldorf:
                      ansible_host: 10.0.0.2  # pinned
                  children:
                    docker:
                """
            )
        )
        upsert_host(inv, "heimdall", "10.0.0.1", ["docker"])

        text = inv.read_text()
        assert "# managed by hand" in text
        assert "ansible_host: 10.0.0.2  # pinned" in text
        data = _read(inv)
        assert data["all"]["hosts"]["heimdall"]["ansible_host"] == "10.0.0.1"
        assert "heimdall" in data["all"]["children"]["docker"]["hosts"]

    def test_fills_a_null_all_section(self, tmp_path):
        inv = tmp_path / "inventory.yml"
        inv.write_text("all:\n")
        upsert_host(inv, "heimdall", "10.0.0.1", [])
        assert _read(inv)["all"]["hosts"]["heimdall"]["ansible_host"] == "10.0.0.1"

    def test_refuses_a_non_mapping_inventory_without_touching_it(self, tmp_path):
        inv = tmp_path / "inventory.yml"
        inv.write_text("- not\n- an\n- inventory\n")
        with pytest.raises(ValueError, match="expected a mapping"):
            upsert_host(inv, "heimdall", "10.0.0.1", [])
        assert inv.read_text() == "- not\n- an\n- inventory\n"

    def test_a_failed_dump_leaves_the_live_file_intact(self, tmp_path, monkeypatch):
        import registry_mcp.inventory.writer as writer

        inv = tmp_path / "inventory.yml"
        original = "all:\n  hosts:\n    waldorf:\n      ansible_host: 10.0.0.2\n"
        inv.write_text(original)

        def _boom(*args, **kwargs):
            raise RuntimeError("disk full")

        monkeypatch.setattr(writer._yaml, "dump", _boom)
        with pytest.raises(RuntimeError, match="disk full"):
            upsert_host(inv, "heimdall", "10.0.0.1", [])

        assert inv.read_text() == original
        assert [p.name for p in tmp_path.iterdir()] == ["inventory.yml"]  # no temp left

    def test_keeps_the_file_mode(self, tmp_path):
        inv = tmp_path / "inventory.yml"
        inv.write_text("all:\n  hosts: {}\n")
        inv.chmod(0o640)
        upsert_host(inv, "heimdall", "10.0.0.1", [])
        assert inv.stat().st_mode & 0o777 == 0o640
