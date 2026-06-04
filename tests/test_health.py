"""Phase 1 smoke tests: server builds and the health tool returns OK."""

from registry_mcp import __version__


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
