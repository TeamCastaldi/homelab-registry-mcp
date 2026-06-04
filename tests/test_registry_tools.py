"""Tests exercising the registry CRUD tools and resources through the server."""


async def call(server, name, args):
    return (await server.call_tool(name, args))[1]


async def test_add_get_list_update_delete_flow(server):
    added = await call(
        server,
        "registry_add_service",
        {"name": "plex", "display_name": "Plex", "category": "media"},
    )
    assert added["name"] == "plex"
    assert added["manual"] is True
    service_id = added["id"]

    got = await call(server, "registry_get_service", {"id_or_name": "plex"})
    assert got["id"] == service_id

    listed = await call(server, "registry_list_services", {})
    assert [s["name"] for s in listed["result"]] == ["plex"]

    updated = await call(
        server,
        "registry_update_service",
        {"id": service_id, "notes": "patched"},
    )
    assert updated["notes"] == "patched"

    deleted = await call(server, "registry_delete_service", {"id": service_id})
    assert deleted["deleted"] is True

    empty = await call(server, "registry_list_services", {})
    assert empty["result"] == []


async def test_add_duplicate_returns_error(server):
    args = {"name": "gitea", "display_name": "Gitea"}
    await call(server, "registry_add_service", args)
    dup = await call(server, "registry_add_service", args)
    assert "error" in dup


async def test_get_missing_returns_error(server):
    got = await call(server, "registry_get_service", {"id_or_name": "ghost"})
    assert "error" in got


async def test_resources_expose_catalog(server):
    await call(
        server,
        "registry_add_service",
        {"name": "vaultwarden", "display_name": "Vaultwarden", "category": "security"},
    )
    index = await server.read_resource("services://all")
    assert "vaultwarden" in index[0].content

    detail = await server.read_resource("service://vaultwarden")
    assert "vaultwarden" in detail[0].content
