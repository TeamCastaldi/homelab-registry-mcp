"""Manual registry CRUD tools and resources."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from registry_mcp.models import AuthMode, Category, Service
from registry_mcp.registry import DuplicateServiceError, RegistryStore


def _dump(service: Service) -> dict[str, Any]:
    return service.model_dump(mode="json")


def _summary(service: Service) -> dict[str, Any]:
    return {
        "id": service.id,
        "name": service.name,
        "display_name": service.display_name,
        "category": service.category.value,
        "host": service.host,
    }


def register_registry_tools(mcp: FastMCP, store: RegistryStore) -> None:
    """Register the manual registry CRUD tools and resources on the server."""

    @mcp.tool()
    def registry_add_service(
        name: str,
        display_name: str,
        category: Category = Category.other,
        host: str | None = None,
        urls: list[str] | None = None,
        traefik_router: str | None = None,
        authentik_app_slug: str | None = None,
        auth_mode: AuthMode = AuthMode.unknown,
        tags: list[str] | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        """Manually add a service to the registry. Fails if the name already exists."""
        service = Service(
            name=name,
            display_name=display_name,
            category=category,
            host=host,
            urls=urls or [],
            traefik_router=traefik_router,
            authentik_app_slug=authentik_app_slug,
            auth_mode=auth_mode,
            tags=tags or [],
            notes=notes,
            manual=True,
        )
        try:
            created = store.create_service(service, actor="manual:registry_add_service")
        except DuplicateServiceError as exc:
            return {"error": str(exc)}
        return _dump(created)

    @mcp.tool()
    def registry_get_service(id_or_name: str) -> dict[str, Any]:
        """Fetch a single service by its id or canonical name."""
        service = store.get_service(id_or_name)
        if service is None:
            return {"error": f"no service found for {id_or_name!r}"}
        return _dump(service)

    @mcp.tool()
    def registry_list_services(
        category: Category | None = None,
        host: str | None = None,
        tag: str | None = None,
    ) -> list[dict[str, Any]]:
        """List services, optionally filtered by category, host, or tag."""
        services = store.list_services(
            category=category.value if category else None,
            host=host,
            tag=tag,
        )
        return [_dump(s) for s in services]

    @mcp.tool()
    def registry_update_service(
        id: str,
        display_name: str | None = None,
        category: Category | None = None,
        host: str | None = None,
        urls: list[str] | None = None,
        traefik_router: str | None = None,
        authentik_app_slug: str | None = None,
        auth_mode: AuthMode | None = None,
        tags: list[str] | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Patch mutable fields on an existing service. Only provided fields change."""
        updates: dict[str, Any] = {
            "display_name": display_name,
            "category": category,
            "host": host,
            "urls": urls,
            "traefik_router": traefik_router,
            "authentik_app_slug": authentik_app_slug,
            "auth_mode": auth_mode,
            "tags": tags,
            "notes": notes,
        }
        updated = store.update_service(id, updates, actor="manual:registry_update_service")
        if updated is None:
            return {"error": f"no service found for id {id!r}"}
        return _dump(updated)

    @mcp.tool()
    def registry_delete_service(id: str) -> dict[str, Any]:
        """Hard-delete a service by id. Change logs are preserved."""
        deleted = store.delete_service(id, actor="manual:registry_delete_service")
        if not deleted:
            return {"error": f"no service found for id {id!r}"}
        return {"deleted": True, "id": id}

    @mcp.resource("service://{service_id}")
    def service_detail(service_id: str) -> dict[str, Any]:
        """Full detail for a single service by id or name."""
        service = store.get_service(service_id)
        if service is None:
            return {"error": f"no service found for {service_id!r}"}
        return _dump(service)

    @mcp.resource("services://all")
    def services_index() -> list[dict[str, Any]]:
        """Catalog index: a summary row per registered service."""
        return [_summary(s) for s in store.list_services()]
