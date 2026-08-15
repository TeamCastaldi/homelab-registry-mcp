"""Komodo MCP tools, resource, and diagnostic prompt."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from registry_mcp.config import Settings
from registry_mcp.integrations.komodo.client import KomodoClient, KomodoError


def register_komodo_tools(mcp: FastMCP, settings: Settings) -> None:
    """Register read-only Komodo tools, the stack resource, and the diagnose prompt."""

    def _client() -> KomodoClient | None:
        if not (settings.komodo_api_url and settings.komodo_api_key and settings.komodo_api_secret):
            return None
        return KomodoClient(
            settings.komodo_api_url,
            settings.komodo_api_key,
            settings.komodo_api_secret,
            timeout=settings.komodo_timeout_seconds,
            retries=settings.komodo_retries,
        )

    async def _call(fn_name: str, *args: Any, **kwargs: Any) -> Any:
        client = _client()
        if client is None:
            return {
                "error": "Komodo is not configured "
                "(missing KOMODO_API_URL, KOMODO_API_KEY, or KOMODO_API_SECRET)"
            }
        try:
            return await getattr(client, fn_name)(*args, **kwargs)
        except KomodoError as exc:
            return {"error": str(exc)}

    async def _call_list(fn_name: str, *args: Any) -> dict[str, Any]:
        data = await _call(fn_name, *args)
        if isinstance(data, dict) and "error" in data:
            return data
        return {"items": data}

    @mcp.tool()
    async def komodo_health() -> dict[str, Any]:
        """Check Komodo Core health and connectivity."""
        return await _call("health_check")

    @mcp.tool()
    async def komodo_list_stacks() -> dict[str, Any]:
        """List all Komodo stacks (deployed applications), under `items`."""
        return await _call_list("list_stacks")

    @mcp.tool()
    async def komodo_get_stack(name: str) -> dict[str, Any]:
        """Get a single Komodo stack by name, including its configuration and status."""
        return await _call("get_stack", name)

    @mcp.tool()
    async def komodo_list_services() -> dict[str, Any]:
        """List all Komodo-managed services (containers), under `items`."""
        return await _call_list("list_services")

    @mcp.tool()
    async def komodo_get_service(service_id: str) -> dict[str, Any]:
        """Get a single service by ID, including its config and runtime state."""
        return await _call("get_service", service_id)

    @mcp.tool()
    async def komodo_list_updates() -> dict[str, Any]:
        """List detected image updates for managed services, under `items`."""
        return await _call_list("list_updates")

    @mcp.tool()
    async def komodo_get_logs(service_id: str, lines: int = 100) -> dict[str, Any]:
        """Fetch recent logs for a service (last N lines)."""
        logs = await _call("get_logs", service_id, lines=lines)
        if isinstance(logs, dict) and "error" in logs:
            return logs
        return {"logs": logs, "service_id": service_id, "lines_requested": lines}

    @mcp.resource("komodo://stacks/{name}")
    async def komodo_stack_resource(name: str) -> dict[str, Any]:
        """Full detail for a single stack by name."""
        return await _call("get_stack", name)

    @mcp.prompt()
    def diagnose_stack(name: str) -> str:
        """Guide a diagnosis of a Komodo stack by fetching config, services, and logs."""
        return (
            f"Diagnose the Komodo stack '{name}'.\n\n"
            "Steps:\n"
            "1. Call `komodo_health` to confirm Komodo is reachable.\n"
            f"2. Call `komodo_get_stack(name='{name}')` to inspect the stack configuration, "
            "status, and deployed services.\n"
            "3. For each service in the stack, call `komodo_get_logs(service_id=<id>)` to "
            "check for recent errors.\n"
            "4. Call `komodo_list_updates` to see if any images have newer versions "
            "available.\n\n"
            "Then summarize: is the stack healthy, what services are running, any error "
            "patterns in the logs, and whether updates are available."
        )
