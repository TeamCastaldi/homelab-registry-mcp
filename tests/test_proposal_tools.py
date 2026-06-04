"""Tests for the proposal MCP tool surface (graceful when write path is off)."""

from conftest import IsolatedSettings
from registry_mcp.server import build_server


async def call(server, name, args):
    return (await server.call_tool(name, args))[1]


def _server(tmp_path):
    return build_server(IsolatedSettings(registry_db_path=str(tmp_path / "r.db")))


async def test_proposal_create_disabled_returns_error(tmp_path):
    server = _server(tmp_path)
    added = await call(server, "registry_add_service", {"name": "plex", "display_name": "Plex"})
    result = await call(server, "proposal_create", {"service_id": added["id"]})
    assert "error" in result
    assert "write path not configured" in result["error"]


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
