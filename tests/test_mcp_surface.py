"""Tool annotations and resource semantics as MCP clients see them."""

from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp.exceptions import ResourceError

from conftest import IsolatedSettings
from registry_mcp.server import _CLOSED_WORLD_TOOLS, build_server


@pytest.fixture
def full_server(tmp_path):
    """Every optional integration registered, so every tool is listed."""
    return build_server(
        IsolatedSettings(
            registry_db_path=str(tmp_path / "r.db"),
            traefik_api_url="http://traefik.test",
            authentik_api_url="http://authentik.test",
            authentik_token="t",
            dockhand_api_url="http://dockhand.test",
            dockhand_token="t",
            infisical_enabled=True,
            service_deploy_enabled=True,
            adoption_enabled=True,
            docs_mcp_url="http://docs.test",
        )
    )


async def test_every_tool_declares_open_world(full_server):
    """The MCP default for a missing openWorldHint is true, so leaving it unset
    made local-SQLite tools claim they reach external systems."""
    tools = {t.name: t for t in await full_server.list_tools()}
    assert all(t.annotations.openWorldHint is not None for t in tools.values())
    assert set(tools) >= _CLOSED_WORLD_TOOLS, "a closed-world entry names no tool"
    assert tools["registry_get_service"].annotations.openWorldHint is False
    assert tools["secrets_decrypt"].annotations.openWorldHint is False
    assert tools["traefik_list_routers"].annotations.openWorldHint is True
    assert tools["service-intake-repo"].annotations.openWorldHint is True
    assert tools["proposal_create"].annotations.openWorldHint is True


async def test_other_hints_survive(full_server):
    tools = {t.name: t for t in await full_server.list_tools()}
    assert tools["registry_get_service"].annotations.readOnlyHint is True
    assert tools["registry_delete_service"].annotations.destructiveHint is True


async def test_json_resources_declare_their_mime_type(full_server):
    listed = await full_server.list_resources()
    templates = await full_server.list_resource_templates()
    assert {r.mimeType for r in listed} == {"application/json"}
    assert {t.mimeType for t in templates} == {"application/json"}


async def test_a_found_resource_reads_as_json(full_server):
    added = (
        await full_server.call_tool(
            "registry_add_service", {"name": "plex", "display_name": "Plex"}
        )
    )[1]
    [content] = await full_server.read_resource(f"service://{added['id']}")
    assert content.mime_type == "application/json"
    assert json.loads(content.content)["name"] == "plex"


@pytest.mark.parametrize(
    ("uri", "message"),
    [
        ("service://nope", "no service found"),
        ("hardware://nope", "no node found"),
        # An integration that can't answer is a failed read, not its content.
        ("traefik://routers/app@docker", "TRAEFIK_API_URL is not configured"),
    ],
)
async def test_a_failed_read_is_an_error_not_content(server, uri, message):
    """A resource read has no isError flag: returning {"error": ...} made a
    failed read indistinguishable from a successful one. (For a templated URI
    the SDK re-wraps the error as ValueError while resolving the template.)"""
    with pytest.raises((ResourceError, ValueError), match=message):
        await server.read_resource(uri)


def test_a_client_gets_a_json_rpc_error_for_a_failed_read(server):
    from starlette.testclient import TestClient

    headers = {"Accept": "application/json, text/event-stream", "Host": "localhost"}
    with TestClient(server.streamable_http_app()) as client:
        init = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            },
            headers=headers,
        )
        response = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "resources/read",
                "params": {"uri": "service://nope"},
            },
            headers={**headers, "mcp-session-id": init.headers["mcp-session-id"]},
        )

    data = next(
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    )
    assert "result" not in data
    assert "no service found" in data["error"]["message"]
