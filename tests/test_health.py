"""Phase 1 smoke tests: server builds and the health tool returns OK."""

from conftest import IsolatedSettings
from registry_mcp import __version__
from registry_mcp.health import check_health
from registry_mcp.server import build_server


def test_build_server_registers_health(server):
    tools = {tool.name for tool in server._tool_manager.list_tools()}
    assert "health" in tools


async def test_health_returns_ok(server):
    result = await server.call_tool("health", {})
    # call_tool returns (content_blocks, structured_result); inspect the structured payload.
    payload = result[1]
    assert payload["status"] == "ok"
    assert payload["service"] == "homelab-registry-mcp"
    assert payload["version"] == __version__


# ---------------------------------------------------------------------------
# Phase 2: startup health checks (check_health)
# ---------------------------------------------------------------------------


def _healthy_paths(tmp_path):
    repo = tmp_path / "homelab"
    (repo / ".git").mkdir(parents=True)
    ansible_cfg = tmp_path / "ansible.cfg"
    ansible_cfg.write_text("")
    ssh_key = tmp_path / "id_ed25519"
    ssh_key.write_text("")
    return repo, ansible_cfg, ssh_key


def test_check_health_all_pass(tmp_path):
    repo, ansible_cfg, ssh_key = _healthy_paths(tmp_path)
    settings = IsolatedSettings(
        secrets_repo_path=str(repo),
        ansible_cfg_path=str(ansible_cfg),
        ssh_key_path=str(ssh_key),
    )
    report = check_health(settings)
    assert report.healthy
    assert {c.name for c in report.checks} == {"git_repo", "ansible_cfg", "ssh_key"}
    assert all(c.ok for c in report.checks)


def test_check_health_fails_when_nothing_configured(tmp_path):
    settings = IsolatedSettings()
    report = check_health(settings)
    assert not report.healthy
    assert all(not c.ok for c in report.checks)


def test_check_health_git_repo_missing_dot_git(tmp_path):
    repo, ansible_cfg, ssh_key = _healthy_paths(tmp_path)
    # A directory that exists but was never git-initialised.
    plain_dir = tmp_path / "not-a-repo"
    plain_dir.mkdir()
    settings = IsolatedSettings(
        secrets_repo_path=str(plain_dir),
        ansible_cfg_path=str(ansible_cfg),
        ssh_key_path=str(ssh_key),
    )
    report = check_health(settings)
    assert not report.healthy
    git_check = next(c for c in report.checks if c.name == "git_repo")
    assert not git_check.ok
    assert "not a git repository" in git_check.detail


def test_check_health_ansible_cfg_missing_file(tmp_path):
    repo, _ansible_cfg, ssh_key = _healthy_paths(tmp_path)
    settings = IsolatedSettings(
        secrets_repo_path=str(repo),
        ansible_cfg_path=str(tmp_path / "does-not-exist.cfg"),
        ssh_key_path=str(ssh_key),
    )
    report = check_health(settings)
    assert not report.healthy
    ansible_check = next(c for c in report.checks if c.name == "ansible_cfg")
    assert not ansible_check.ok


def test_check_health_ssh_key_missing_file(tmp_path):
    repo, ansible_cfg, _ssh_key = _healthy_paths(tmp_path)
    settings = IsolatedSettings(
        secrets_repo_path=str(repo),
        ansible_cfg_path=str(ansible_cfg),
        ssh_key_path=str(tmp_path / "does-not-exist-key"),
    )
    report = check_health(settings)
    assert not report.healthy
    ssh_check = next(c for c in report.checks if c.name == "ssh_key")
    assert not ssh_check.ok


# ---------------------------------------------------------------------------
# system_health_check MCP tool
# ---------------------------------------------------------------------------


async def test_system_health_check_always_registered(server):
    tools = {tool.name for tool in server._tool_manager.list_tools()}
    assert "system_health_check" in tools


async def test_system_health_check_reports_read_only_when_unconfigured(tmp_path):
    server = build_server(IsolatedSettings(registry_db_path=str(tmp_path / "r.db")))
    result = await server.call_tool("system_health_check", {})
    payload = result[1]
    assert payload["mode"] == "read-only"
    assert payload["healthy"] is False


async def test_system_health_check_reports_read_write_when_healthy(tmp_path):
    repo, ansible_cfg, ssh_key = _healthy_paths(tmp_path)
    server = build_server(
        IsolatedSettings(
            registry_db_path=str(tmp_path / "r.db"),
            secrets_repo_path=str(repo),
            ansible_cfg_path=str(ansible_cfg),
            ssh_key_path=str(ssh_key),
        )
    )
    result = await server.call_tool("system_health_check", {})
    payload = result[1]
    assert payload["mode"] == "read-write"
    assert payload["healthy"] is True


# ---------------------------------------------------------------------------
# Read-only mode gating on the mutating secrets_* tools
# ---------------------------------------------------------------------------


async def test_secrets_encrypt_read_only_when_health_checks_fail(tmp_path):
    server = build_server(IsolatedSettings(registry_db_path=str(tmp_path / "r.db")))
    result = await server.call_tool("secrets_encrypt", {"path": "nodes/host/app/.env"})
    payload = result[1]
    assert "error" in payload
    assert "read-only mode" in payload["error"]


async def test_secrets_add_read_only_when_health_checks_fail(tmp_path):
    server = build_server(IsolatedSettings(registry_db_path=str(tmp_path / "r.db")))
    result = await server.call_tool("secrets_add", {"key": "FOO", "value": "bar", "path": ".env"})
    payload = result[1]
    assert "error" in payload
    assert "read-only mode" in payload["error"]


async def test_secrets_rotate_read_only_when_health_checks_fail(tmp_path):
    server = build_server(IsolatedSettings(registry_db_path=str(tmp_path / "r.db")))
    result = await server.call_tool("secrets_rotate", {"path": ""})
    payload = result[1]
    assert "error" in payload
    assert "read-only mode" in payload["error"]


async def test_secrets_status_not_gated_by_read_only(tmp_path):
    """secrets_status is informational (no repo mutation) and stays usable in
    read-only mode — it fails only on its own pre-existing config guard."""
    server = build_server(IsolatedSettings(registry_db_path=str(tmp_path / "r.db")))
    result = await server.call_tool("secrets_status", {})
    payload = result[1]
    assert "error" in payload
    assert "read-only mode" not in payload["error"]
