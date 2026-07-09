"""Tests for the proposal MCP tool surface (graceful when write path is off)."""

from conftest import IsolatedSettings
from registry_mcp.server import build_server


async def call(server, name, args):
    return (await server.call_tool(name, args))[1]


def _server(tmp_path):
    return build_server(IsolatedSettings(registry_db_path=str(tmp_path / "r.db")))


def _healthy_server(tmp_path):
    """A server whose startup health checks all pass (read_only=False), so tests
    below exercise the write-path config guard rather than the read-only gate."""
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
        )
    )


async def test_proposal_create_disabled_returns_error(tmp_path):
    server = _healthy_server(tmp_path)
    added = await call(server, "registry_add_service", {"name": "plex", "display_name": "Plex"})
    result = await call(server, "proposal_create", {"service_id": added["id"]})
    assert "error" in result
    assert "write path not configured" in result["error"]


async def test_proposal_create_read_only_when_health_checks_fail(tmp_path):
    server = _server(tmp_path)
    added = await call(server, "registry_add_service", {"name": "plex", "display_name": "Plex"})
    result = await call(server, "proposal_create", {"service_id": added["id"]})
    assert "error" in result
    assert "read-only mode" in result["error"]


async def test_proposal_cancel_read_only_when_health_checks_fail(tmp_path):
    server = _server(tmp_path)
    result = await call(server, "proposal_cancel", {"proposal_id": "whatever"})
    assert "error" in result
    assert "read-only mode" in result["error"]


async def test_proposal_list_open_empty(tmp_path):
    server = _server(tmp_path)
    result = await call(server, "proposal_list_open", {})
    assert result["items"] == []


async def test_proposal_get_missing(tmp_path):
    server = _server(tmp_path)
    result = await call(server, "proposal_get", {"proposal_id": "nope"})
    assert "error" in result


async def test_proposal_verify_missing_service(tmp_path):
    server = _server(tmp_path)
    result = await call(server, "proposal_verify", {"service_id": "ghost"})
    assert "error" in result
