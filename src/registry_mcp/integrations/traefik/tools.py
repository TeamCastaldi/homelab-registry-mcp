"""Traefik MCP tools, resource, and diagnostic prompt."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from registry_mcp.config import Settings
from registry_mcp.integrations.traefik.client import Protocol, TraefikClient, TraefikError


def register_traefik_tools(mcp: FastMCP, settings: Settings) -> None:
    """Register read-only Traefik tools, the router resource, and the diagnose prompt."""

    def _client() -> TraefikClient | None:
        if not settings.traefik_api_url:
            return None
        return TraefikClient(
            settings.traefik_api_url,
            timeout=settings.traefik_timeout_seconds,
            retries=settings.traefik_retries,
        )

    async def _call(fn_name: str, *args: Any) -> Any:
        client = _client()
        if client is None:
            return {"error": "TRAEFIK_API_URL is not configured"}
        try:
            return await getattr(client, fn_name)(*args)
        except TraefikError as exc:
            return {"error": str(exc)}

    async def _call_list(fn_name: str, *args: Any) -> dict[str, Any]:
        data = await _call(fn_name, *args)
        if isinstance(data, dict) and "error" in data:
            return data
        return {"items": data}

    @mcp.tool()
    async def traefik_get_overview() -> dict[str, Any]:
        """Traefik's own summary of routers, services, middlewares, and features."""
        return await _call("overview")

    @mcp.tool()
    async def traefik_get_entrypoints() -> dict[str, Any]:
        """List configured Traefik entrypoints (e.g. web, websecure), under `items`."""
        return await _call_list("entrypoints")

    @mcp.tool()
    async def traefik_list_routers(protocol: Protocol = "http") -> dict[str, Any]:
        """List Traefik routers for the given protocol (http, tcp, or udp), under `items`."""
        return await _call_list("list_routers", protocol)

    @mcp.tool()
    async def traefik_get_router(name: str, protocol: Protocol = "http") -> dict[str, Any]:
        """Fetch a single Traefik router by name, including its rule and middlewares."""
        return await _call("get_router", name, protocol)

    @mcp.tool()
    async def traefik_list_services(protocol: Protocol = "http") -> dict[str, Any]:
        """List Traefik backend services for the given protocol, under `items`."""
        return await _call_list("list_services", protocol)

    @mcp.tool()
    async def traefik_list_middlewares(protocol: Protocol = "http") -> dict[str, Any]:
        """List Traefik middlewares for the given protocol, under `items`."""
        return await _call_list("list_middlewares", protocol)

    @mcp.tool()
    async def traefik_list_tls_certificates() -> dict[str, Any]:
        """List TLS configuration from Traefik.

        Traefik has no dedicated certificates endpoint, so this returns the `tls`
        section of `/api/rawdata` (certificates, stores, options) when present.
        """
        data = await _call("rawdata")
        if isinstance(data, dict) and "error" in data:
            return data
        return {"tls": data.get("tls", {}) if isinstance(data, dict) else {}}

    @mcp.resource("traefik://routers/{name}")
    async def traefik_router_resource(name: str) -> dict[str, Any]:
        """Full detail for a single HTTP router by name."""
        return await _call("get_router", name, "http")

    @mcp.prompt()
    def diagnose_router(name: str) -> str:
        """Guide a diagnosis of a Traefik router by chaining overview + detail + middlewares."""
        return (
            f"Diagnose the Traefik router '{name}'.\n\n"
            "Steps:\n"
            "1. Call `traefik_get_overview` to check overall Traefik health and counts.\n"
            f"2. Call `traefik_get_router(name='{name}')` to inspect its rule, entrypoints, "
            "service, TLS, and attached middlewares, and note its status.\n"
            "3. Call `traefik_list_middlewares` and cross-reference the middlewares the "
            "router uses to confirm they exist and are healthy.\n\n"
            "Then summarize: is the router enabled and healthy, what does it route, which "
            "middlewares apply, and flag any errors or missing references."
        )
