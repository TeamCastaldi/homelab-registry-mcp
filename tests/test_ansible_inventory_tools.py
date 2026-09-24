"""Tests for the ansible-inventory-sync-node / *_confirm MCP tool pair (ADR-015)."""

from __future__ import annotations

from ruamel.yaml import YAML

from conftest import IsolatedSettings
from registry_mcp.server import build_server

_yaml = YAML()


async def call(server, name, args):
    return (await server.call_tool(name, args))[1]


def _answer(challenge_text: str) -> int:
    x, _plus, y, *_rest = challenge_text.split(" ")
    return int(x) + int(y)


def _healthy_server(tmp_path, *, inventory_path=None):
    """A server whose startup health checks all pass (read_only=False)."""
    repo = tmp_path / "homelab"
    (repo / ".git").mkdir(parents=True)
    ansible_cfg = tmp_path / "ansible.cfg"
    ansible_cfg.write_text("")
    ssh_key = tmp_path / "id_ed25519"
    ssh_key.write_text("")
    return build_server(
        IsolatedSettings(
            registry_db_path=str(tmp_path / "r.db"),
            secrets_repo_path=str(repo),
            ansible_cfg_path=str(ansible_cfg),
            ssh_key_path=str(ssh_key),
            ansible_inventory_path=str(inventory_path) if inventory_path else None,
        )
    )


async def test_sync_disabled_without_inventory_path(tmp_path):
    server = _healthy_server(tmp_path)
    added = await call(server, "hardware-add-node", {"hostname": "heimdall", "display_name": "H"})
    result = await call(server, "ansible-inventory-sync-node", {"id_or_hostname": added["id"]})
    assert "ANSIBLE_INVENTORY_PATH" in result["error"]


async def test_sync_read_only_blocks_request(tmp_path):
    server = build_server(IsolatedSettings(registry_db_path=str(tmp_path / "r.db")))
    result = await call(server, "ansible-inventory-sync-node", {"id_or_hostname": "heimdall"})
    assert result["status"] == "error"
    assert "read-only" in result["error"]


async def test_sync_unknown_node_returns_error(tmp_path):
    inv = tmp_path / "inventory.yml"
    server = _healthy_server(tmp_path, inventory_path=inv)
    result = await call(server, "ansible-inventory-sync-node", {"id_or_hostname": "nonexistent"})
    assert "error" in result
    assert not inv.exists()


async def test_sync_request_does_not_write_file(tmp_path):
    inv = tmp_path / "inventory.yml"
    server = _healthy_server(tmp_path, inventory_path=inv)
    added = await call(server, "hardware-add-node", {"hostname": "heimdall", "display_name": "H"})
    requested = await call(server, "ansible-inventory-sync-node", {"id_or_hostname": added["id"]})
    assert "request_id" in requested
    assert "challenge" in requested
    assert not inv.exists()


async def test_sync_confirm_correct_answer_writes_inventory(tmp_path):
    inv = tmp_path / "inventory.yml"
    server = _healthy_server(tmp_path, inventory_path=inv)
    added = await call(
        server,
        "hardware-add-node",
        {"hostname": "heimdall", "display_name": "H", "ip_address": "10.0.0.9"},
    )
    requested = await call(server, "ansible-inventory-sync-node", {"id_or_hostname": added["id"]})
    confirmed = await call(
        server,
        "ansible-inventory-sync-node-confirm",
        {"request_id": requested["request_id"], "answer": _answer(requested["challenge"])},
    )
    assert confirmed["synced"] is True
    assert confirmed["hostname"] == "heimdall"
    with inv.open() as fh:
        data = _yaml.load(fh)
    assert data["all"]["hosts"]["heimdall"]["ansible_host"] == "10.0.0.9"


async def test_sync_confirm_wrong_answer_leaves_file_untouched(tmp_path):
    inv = tmp_path / "inventory.yml"
    server = _healthy_server(tmp_path, inventory_path=inv)
    added = await call(server, "hardware-add-node", {"hostname": "heimdall", "display_name": "H"})
    requested = await call(server, "ansible-inventory-sync-node", {"id_or_hostname": added["id"]})
    wrong = await call(
        server,
        "ansible-inventory-sync-node-confirm",
        {"request_id": requested["request_id"], "answer": _answer(requested["challenge"]) + 1},
    )
    assert "error" in wrong
    assert not inv.exists()


async def test_sync_confirm_read_only_blocks_write(tmp_path):
    server = build_server(IsolatedSettings(registry_db_path=str(tmp_path / "r.db")))
    result = await call(
        server, "ansible-inventory-sync-node-confirm", {"request_id": "whatever", "answer": 1}
    )
    assert result["status"] == "error"
    assert "read-only" in result["error"]


async def test_sync_confirm_reports_an_unusable_inventory_shape(tmp_path):
    inv = tmp_path / "inventory.yml"
    inv.write_text("- not an inventory\n")
    server = _healthy_server(tmp_path, inventory_path=inv)
    added = await call(server, "hardware-add-node", {"hostname": "heimdall", "display_name": "H"})
    requested = await call(server, "ansible-inventory-sync-node", {"id_or_hostname": added["id"]})
    confirmed = await call(
        server,
        "ansible-inventory-sync-node-confirm",
        {"request_id": requested["request_id"], "answer": _answer(requested["challenge"])},
    )
    assert "inventory not updated" in confirmed["error"]
    assert inv.read_text() == "- not an inventory\n"
