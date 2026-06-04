"""Authentik MCP tools, resource, and access-audit prompt."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from registry_mcp.config import Settings
from registry_mcp.integrations.authentik.client import AuthentikClient, AuthentikError

if TYPE_CHECKING:
    from registry_mcp.dspy import Reasoner


def _within_hours(events: list[dict[str, Any]], hours: int) -> list[dict[str, Any]]:
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    recent = []
    for event in events:
        created = event.get("created")
        if not created:
            continue
        try:
            timestamp = datetime.fromisoformat(created)
        except (ValueError, TypeError):
            continue
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        if timestamp >= cutoff:
            recent.append(event)
    return recent


def register_authentik_tools(
    mcp: FastMCP, settings: Settings, reasoner: Reasoner | None = None
) -> None:
    """Register read-only Authentik tools, the application resource, and the audit prompt.

    The DSPy-backed ``authentik_summarize_events`` synthesis tool is always
    registered; when no reasoning layer is supplied or it is disabled, the tool
    returns a structured error directing the operator to set ``DSPY_ENABLED``.
    """

    def _client() -> AuthentikClient | None:
        if not settings.authentik_api_url or not settings.authentik_token:
            return None
        return AuthentikClient(
            settings.authentik_api_url,
            settings.authentik_token,
            timeout=settings.authentik_timeout_seconds,
            retries=settings.authentik_retries,
        )

    async def _call(fn_name: str, *args: Any, **kwargs: Any) -> Any:
        client = _client()
        if client is None:
            return {"error": "AUTHENTIK_API_URL and AUTHENTIK_TOKEN must be configured"}
        try:
            return await getattr(client, fn_name)(*args, **kwargs)
        except AuthentikError as exc:
            return {"error": str(exc)}

    async def _call_list(fn_name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        data = await _call(fn_name, *args, **kwargs)
        if isinstance(data, dict) and "error" in data:
            return data
        return {"items": data}

    @mcp.tool()
    async def authentik_list_applications() -> dict[str, Any]:
        """List Authentik applications, under `items`."""
        return await _call_list("list_applications")

    @mcp.tool()
    async def authentik_get_application(slug: str) -> dict[str, Any]:
        """Fetch a single Authentik application by slug, including its bound provider."""
        return await _call("get_application", slug)

    @mcp.tool()
    async def authentik_list_providers() -> dict[str, Any]:
        """List all Authentik providers (proxy, oauth2, ldap, etc.), under `items`."""
        return await _call_list("list_providers")

    @mcp.tool()
    async def authentik_list_outposts() -> dict[str, Any]:
        """List Authentik outpost instances, under `items`."""
        return await _call_list("list_outposts")

    @mcp.tool()
    async def authentik_get_outpost_status(name: str) -> dict[str, Any]:
        """Find an outpost by name and return it together with its health status."""
        outposts = await _call("list_outposts")
        if isinstance(outposts, dict) and "error" in outposts:
            return outposts
        match = next((o for o in outposts if o.get("name") == name), None)
        if match is None:
            return {"error": f"no outpost named {name!r}"}
        health = await _call("get_outpost_health", match["pk"])
        if isinstance(health, dict) and "error" in health:
            return {"error": f"failed to get health for outpost {name!r}: {health['error']}"}
        return {"outpost": match, "health": health}

    @mcp.tool()
    async def authentik_list_policies() -> dict[str, Any]:
        """List all Authentik policies, under `items`."""
        return await _call_list("list_policies")

    @mcp.tool()
    async def authentik_search_events(
        action: str | None = None,
        search: str | None = None,
        within_hours: int | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Query the Authentik audit log, newest first.

        Optionally filter by `action`, a free-text `search`, and restrict to events
        from the last `within_hours` hours. Results are returned under `items`.
        """
        params = {"action": action, "search": search, "page_size": limit}
        events = await _call("list_events", params)
        if isinstance(events, dict) and "error" in events:
            return events
        if within_hours is not None and isinstance(events, list):
            events = _within_hours(events, within_hours)
        return {"items": events}

    @mcp.tool()
    async def authentik_summarize_events(
        slug: str, within_hours: int = 24, limit: int = 200
    ) -> dict[str, Any]:
        """Summarize recent Authentik access events for an application.

        Fetches the relevant audit events and returns a structured, pre-reasoned
        report (summary, anomalies, unique users, failed-auth count, risk level)
        instead of raw event JSON. Backed by the DSPy reasoning layer; requires
        `DSPY_ENABLED=true`.
        """
        if reasoner is None or not reasoner.enabled:
            return {"error": "reasoning layer disabled; set DSPY_ENABLED=true to enable summaries"}
        events = await _call("list_events", {"search": slug, "page_size": limit})
        if isinstance(events, dict) and "error" in events:
            return events
        events = _within_hours(events, within_hours) if isinstance(events, list) else []
        # The LLM call is slow; offload it so the event loop stays responsive.
        return await asyncio.to_thread(
            reasoner.summarize_access, slug=slug, events=events, hours=within_hours
        )

    @mcp.tool()
    async def authentik_list_users(search: str | None = None) -> dict[str, Any]:
        """List Authentik users, optionally filtered by a search term, under `items`."""
        return await _call_list("list_users", search)

    @mcp.tool()
    async def authentik_list_groups(search: str | None = None) -> dict[str, Any]:
        """List Authentik groups, optionally filtered by a search term, under `items`."""
        return await _call_list("list_groups", search)

    @mcp.resource("authentik://applications/{slug}")
    async def authentik_application_resource(slug: str) -> dict[str, Any]:
        """Full detail for a single Authentik application by slug."""
        return await _call("get_application", slug)

    @mcp.prompt()
    def audit_application_access(slug: str) -> str:
        """Guide an access audit for an Authentik application by chaining its detail,
        provider, policies, and recent events."""
        return (
            f"Audit access to the Authentik application '{slug}'.\n\n"
            "Steps:\n"
            f"1. Call `authentik_get_application(slug='{slug}')` to identify the bound "
            "provider and the application's settings.\n"
            "2. Call `authentik_list_providers` and locate the provider bound to this "
            "application to determine its type (proxy/oauth2/ldap) and configuration.\n"
            "3. Call `authentik_list_policies` to enumerate the policies that gate access, "
            "and note which apply to this application or its provider.\n"
            f"4. Call `authentik_search_events(search='{slug}', within_hours=24)` to review "
            "the last 24 hours of relevant authorization and login events.\n\n"
            "Then summarize: who can access this application, through which provider and "
            "policy chain, and flag any denied attempts or anomalies in the recent events."
        )
