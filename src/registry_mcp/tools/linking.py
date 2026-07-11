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

    @mcp.prompt()
    def pre_update_compatibility_check(name: str) -> str:
        """Guide a compatibility risk check before manually bumping a service's
        pinned version, by cross-referencing its current naming/routing state."""
        return (
            f"Assess compatibility risk before bumping the pinned version of '{name}'.\n\n"
            "Steps:\n"
            f"1. Call `registry_get_service(id_or_name='{name}')` to resolve the service "
            "and review its curated fields (host, traefik_router, authentik_app_slug, "
            "notes, tags).\n"
            "2. Call `service_get_full_context(id=<resolved id>)` to pull its linked "
            "Traefik router, Authentik application, hardware node, and recent change "
            "events in one call.\n"
            "3. If a Traefik router is linked, inspect its rule (e.g. `Host(...)`) and "
            "flag any hostname that would fail strict DNS-1123 label validation "
            "(the stricter, Kubernetes-style rule many systems enforce, distinct from "
            "plain RFC-1123 which is case-insensitive) — underscores, uppercase "
            "letters, or leading/trailing hyphens are all invalid under it, and are "
            "exactly the kind of thing newer upstream versions may start enforcing "
            "strictly where older versions did not.\n"
            "4. If an Authentik application is linked, call `authentik_list_outposts` "
            "and `authentik_get_outpost_status` for its outpost, and check the "
            "application/provider's external host against the same hostname "
            "validation rule.\n"
            "5. Review the `recent_events` already returned by `service_get_full_context` "
            "for prior manual updates or naming-field changes that hint at past "
            "compatibility issues with this service.\n"
            "6. Ask for (or scan) the upstream changelog/release notes covering the "
            "version jump, explicitly looking for: renamed environment variables, "
            "renamed container/compose service keys, changed default ports, and newly "
            "enforced hostname or routing validation rules.\n\n"
            "Then summarize a go/caution/no-go recommendation, calling out any naming "
            "field on this service that would violate a stricter validation rule. If "
            "the update proceeds and something is learned, suggest recording it with "
            "`registry_update_service(id=<id>, notes=...)` so the lesson persists on "
            "the service record."
        )
