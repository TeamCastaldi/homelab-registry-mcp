"""Dockhand MCP tools, resource, and diagnostic prompt.

Read-only by design (ADR-013) — no tool ever triggers an update check or
mutates a stack, even though Dockhand's own API exposes endpoints for both.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from registry_mcp.config import Settings
from registry_mcp.integrations.dockhand.client import DockhandClient, DockhandError

_READ_ONLY = ToolAnnotations(readOnlyHint=True)


def register_dockhand_tools(mcp: FastMCP, settings: Settings) -> None:
    """Register read-only Dockhand tools, the stack resource, and the diagnose prompt."""

    def _client() -> DockhandClient | None:
        if not settings.dockhand_api_url or not settings.dockhand_token:
            return None
        return DockhandClient(
            settings.dockhand_api_url,
            settings.dockhand_token,
            timeout=settings.dockhand_timeout_seconds,
            retries=settings.dockhand_retries,
        )

    async def _call(fn_name: str, *args: Any) -> Any:
        client = _client()
        if client is None:
            return {"error": "DOCKHAND_API_URL and DOCKHAND_TOKEN must be configured"}
        try:
            return await getattr(client, fn_name)(*args)
        except DockhandError as exc:
            return {"error": str(exc)}

    async def _call_list(fn_name: str, *args: Any) -> dict[str, Any]:
        data = await _call(fn_name, *args)
        if isinstance(data, dict) and "error" in data:
            return data
        return {"items": data}

    @mcp.tool(annotations=_READ_ONLY)
    async def dockhand_list_environments() -> dict[str, Any]:
        """List Dockhand environments (Docker host connections), under `items`."""
        return await _call_list("list_environments")

    @mcp.tool(annotations=_READ_ONLY)
    async def dockhand_get_environment(environment_id: str) -> dict[str, Any]:
        """Fetch a single Dockhand environment by id."""
        return await _call("get_environment", environment_id)

    @mcp.tool(annotations=_READ_ONLY)
    async def dockhand_list_stacks(environment_id: str | None = None) -> dict[str, Any]:
        """List Dockhand stacks (compose projects), optionally filtered to one
        environment, under `items`."""
        return await _call_list("list_stacks", environment_id)

    @mcp.tool(annotations=_READ_ONLY)
    async def dockhand_get_stack(stack_id: str) -> dict[str, Any]:
        """Fetch a single Dockhand stack by id."""
        return await _call("get_stack", stack_id)

    @mcp.tool(annotations=_READ_ONLY)
    async def dockhand_list_containers(
        environment_id: str | None = None, stack_id: str | None = None
    ) -> dict[str, Any]:
        """List Dockhand-visible containers, optionally filtered by environment
        and/or stack, under `items`."""
        return await _call_list("list_containers", environment_id, stack_id)

    @mcp.tool(annotations=_READ_ONLY)
    async def dockhand_list_pending_updates() -> dict[str, Any]:
        """List containers with an image update available, under `items`.

        Reads Dockhand's last update check only — this never triggers a new
        check against upstream registries.
        """
        return await _call_list("list_pending_updates")

    @mcp.tool(annotations=_READ_ONLY)
    async def dockhand_list_vulnerabilities(container_id: str | None = None) -> dict[str, Any]:
        """List CVE findings from Dockhand's vulnerability scans (Grype/Trivy),
        optionally filtered to one container, under `items`."""
        return await _call_list("list_vulnerabilities", container_id)

    @mcp.resource("dockhand://stacks/{stack_id}")
    async def dockhand_stack_resource(stack_id: str) -> dict[str, Any]:
        """Full detail for a single Dockhand stack by id."""
        return await _call("get_stack", stack_id)

    @mcp.prompt()
    def diagnose_stack(stack_id: str) -> str:
        """Guide a diagnosis of a Dockhand stack by chaining detail, containers,
        pending updates, and vulnerabilities."""
        return (
            f"Diagnose the Dockhand stack '{stack_id}'.\n\n"
            "Steps:\n"
            f"1. Call `dockhand_get_stack(stack_id='{stack_id}')` to inspect its "
            "environment and current state.\n"
            f"2. Call `dockhand_list_containers(stack_id='{stack_id}')` to enumerate its "
            "containers and note each one's running status and image.\n"
            "3. Call `dockhand_list_pending_updates` and cross-reference this stack's "
            "containers to see which have a newer image available.\n"
            "4. Call `dockhand_list_vulnerabilities` for each container id found above "
            "to check for known CVEs.\n\n"
            "Then summarize: is the stack healthy, which containers are out of date, "
            "and what CVE exposure (if any) it carries."
        )
