"""Cross-source linking and the aggregated full-context tool."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from registry_mcp.config import Settings
from registry_mcp.integrations.authentik.client import AuthentikClient, AuthentikError
from registry_mcp.integrations.traefik.client import TraefikClient, TraefikError
from registry_mcp.registry import RegistryStore


def register_linking_tools(
    mcp: FastMCP,
    store: RegistryStore,
    settings: Settings,
    hardware_store=None,
) -> None:
    """Register manual linking and the aggregated full-context tool."""

    async def _traefik_router(name: str) -> dict[str, Any]:
        if not settings.traefik_api_url:
            return {"error": "TRAEFIK_API_URL is not configured"}
        client = TraefikClient(
            settings.traefik_api_url,
            timeout=settings.traefik_timeout_seconds,
            retries=settings.traefik_retries,
        )
        try:
            return await client.get_router(name)
        except TraefikError as exc:
            return {"error": str(exc)}

    async def _authentik_app(slug: str) -> dict[str, Any]:
        if not (settings.authentik_api_url and settings.authentik_token):
            return {"error": "AUTHENTIK_API_URL and AUTHENTIK_TOKEN must be configured"}
        client = AuthentikClient(
            settings.authentik_api_url,
            settings.authentik_token,
            timeout=settings.authentik_timeout_seconds,
            retries=settings.authentik_retries,
        )
        try:
            return await client.get_application(slug)
        except AuthentikError as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def service_link_authentik(service_id: str, app_slug: str) -> dict[str, Any]:
        """Manually link a service to an Authentik application by slug.

        Overrides discovery; sets the service's `authentik_app_slug`.
        """
        updated = store.update_service(
            service_id,
            {"authentik_app_slug": app_slug},
            actor="manual:service_link_authentik",
        )
        if updated is None:
            return {"error": f"no service found for id {service_id!r}"}
        return updated.model_dump(mode="json")

    @mcp.tool()
    async def service_get_full_context(id: str) -> dict[str, Any]:
        """Resolve a service's full cross-source context in one call.

        Returns the registry record plus its Traefik router, Authentik
        application, and recent change events where those links resolve.
        """
        service = store.get_service(id)
        if service is None:
            return {"error": f"no service found for {id!r}"}

        context: dict[str, Any] = {
            "service": service.model_dump(mode="json"),
            "traefik_router": None,
            "authentik_application": None,
            "hardware_node": None,
            "recent_events": [
                e.model_dump(mode="json")
                for e in store.list_change_events(service_id=service.id, limit=20)
            ],
        }
        if service.traefik_router:
            context["traefik_router"] = await _traefik_router(service.traefik_router)
        if service.authentik_app_slug:
            context["authentik_application"] = await _authentik_app(service.authentik_app_slug)
        if hardware_store and service.hardware_node_id:
            node = hardware_store.get_node(service.hardware_node_id)
            context["hardware_node"] = node.model_dump(mode="json") if node else None
        return context
