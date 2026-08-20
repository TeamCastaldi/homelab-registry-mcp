"""Manual registry CRUD tools and resources."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from registry_mcp.config import Settings
from registry_mcp.deletion import DeletionGateError, DeletionGateStore
from registry_mcp.models import AuthMode, Category, DeletionEntityType, Service
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


def register_registry_tools(
    mcp: FastMCP, store: RegistryStore, deletion_gate: DeletionGateStore, settings: Settings
) -> None:
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
        """Request deletion of a service by id. Deletes nothing yet — returns an
        arithmetic challenge that must be solved and passed to
        registry_delete_service_confirm before the row is removed."""
        service = store.get_service(id)
        if service is None:
            return {"error": f"no service found for id {id!r}"}
        challenge = deletion_gate.request(
            entity_type=DeletionEntityType.service,
            entity_id=service.id,
            entity_label=service.name,
            actor="manual:registry_delete_service",
            ttl_minutes=settings.delete_challenge_ttl_minutes,
        )
        return {
            "request_id": challenge.id,
            "challenge": f"{challenge.x} + {challenge.y} = ?",
            "service": service.name,
            "expires_at": challenge.expires_at.isoformat(),
            "next_step": (
                f"Ask the user to solve {challenge.x} + {challenge.y}, then call "
                f"registry_delete_service_confirm(request_id={challenge.id!r}, "
                f"answer=<their answer>) to permanently delete {service.name!r}."
            ),
        }

    @mcp.tool()
    def registry_delete_service_confirm(request_id: str, answer: int) -> dict[str, Any]:
        """Complete a service deletion by answering the math challenge from
        registry_delete_service. A wrong or expired answer invalidates the
        challenge — call registry_delete_service again for a new one."""
        try:
            challenge = deletion_gate.confirm(request_id, DeletionEntityType.service, answer)
        except DeletionGateError as exc:
            return {"error": str(exc)}
        deleted = store.delete_service(
            challenge.entity_id, actor="manual:registry_delete_service_confirm"
        )
        if not deleted:
            return {
                "error": f"no service found for id {challenge.entity_id!r} (may already be deleted)"
            }
        return {"deleted": True, "id": challenge.entity_id, "name": challenge.entity_label}

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
