"""DNS-rebinding protection on /mcp (MCP spec: servers must validate Origin).

FastMCP only enables Host/Origin validation on its own for a 127.0.0.1 or
localhost bind; these tests pin that the server enables it for the default
0.0.0.0 bind too, so a browser page on the LAN can't reach the tools through
a rebound hostname.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from conftest import IsolatedSettings
from registry_mcp.server import build_server, build_transport_security

_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
_ACCEPT = {"Accept": "application/json, text/event-stream"}


def _post_initialize(tmp_path, headers: dict[str, str], **settings) -> int:
    server = build_server(IsolatedSettings(registry_db_path=str(tmp_path / "r.db"), **settings))
    with TestClient(server.streamable_http_app()) as client:
        return client.post("/mcp/", json=_INITIALIZE, headers={**_ACCEPT, **headers}).status_code


def test_rebound_host_is_refused_on_default_bind(tmp_path):
    status = _post_initialize(
        tmp_path, {"Host": "attacker.example", "Origin": "http://attacker.example"}
    )
    assert status == 421


def test_loopback_host_is_allowed_by_default(tmp_path):
    assert _post_initialize(tmp_path, {"Host": "localhost:8765"}) == 200


def test_default_port_loopback_host_is_allowed_by_default(tmp_path):
    """Clients omit :80/:443 from Host, which a "name:*" pattern never matches."""
    assert _post_initialize(tmp_path, {"Host": "localhost"}) == 200


def test_configured_host_is_allowed(tmp_path):
    status = _post_initialize(
        tmp_path,
        {"Host": "registry-mcp.example.lan"},
        mcp_allowed_hosts="registry-mcp.example.lan, 10.0.0.5:8765",
    )
    assert status == 200


def test_foreign_origin_on_allowed_host_is_refused(tmp_path):
    status = _post_initialize(
        tmp_path,
        {"Host": "registry-mcp.example.lan", "Origin": "http://attacker.example"},
        mcp_allowed_hosts="registry-mcp.example.lan",
    )
    assert status == 403


def test_configured_origin_is_allowed(tmp_path):
    status = _post_initialize(
        tmp_path,
        {"Host": "registry-mcp.example.lan", "Origin": "https://registry-mcp.example.lan"},
        mcp_allowed_hosts="registry-mcp.example.lan",
        mcp_allowed_origins="https://registry-mcp.example.lan",
    )
    assert status == 200


def test_transport_security_parses_comma_separated_lists():
    security = build_transport_security(
        IsolatedSettings(
            mcp_allowed_hosts=" a.lan , 10.0.0.5:8765,, ", mcp_allowed_origins="https://a.lan"
        )
    )
    assert security.enable_dns_rebinding_protection is True
    assert security.allowed_hosts == ["a.lan", "10.0.0.5:8765"]
    assert security.allowed_origins == ["https://a.lan"]
