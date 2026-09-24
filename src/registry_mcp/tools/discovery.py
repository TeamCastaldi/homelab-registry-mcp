"""Discovery control and status tools."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from registry_mcp.discovery.engine import DiscoveryEngine
from registry_mcp.integrations.authentik.client import AuthentikClient, AuthentikError
from registry_mcp.integrations.traefik.client import TraefikClient, TraefikError
from registry_mcp.models import SourceType

_RESTART_HINT = (
    "Add these to .env on the control-plane node, then `docker compose up -d` "
    "to restart the server and enable this source -- Settings and the "
    "discovery scheduler are only read at startup, so nothing here takes "
    "effect until the restart."
)


def _invalid_connection_params(timeout_seconds: float, retries: int) -> str | None:
    """Reject inputs the client would silently reinterpret or choke on.

    TraefikClient/AuthentikClient clamp retries to max(1, retries), so a
    non-positive retries here would make the returned .env line lie about
    what the client actually does. A non-positive timeout isn't clamped at
    all and fails inside httpx, which would surface as a misleading
    "unreachable" error instead of a clear input problem.
    """
    if timeout_seconds <= 0:
        return f"timeout_seconds must be positive, got {timeout_seconds}"
    if retries < 1:
        return f"retries must be at least 1, got {retries}"
    return None


def _invalid_base_url(url: str) -> str | None:
    """Reject anything but a plain http(s) base URL.

    The clients append their own API path, so a `?` or `#` in a caller's URL
    would swallow it (`http://10.0.0.9:2375/containers/json?` turns
    `/api/overview` into a query string), letting these tools fetch an
    arbitrary internal endpoint from this node's network position.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return f"url must be an http(s) URL with a host, got {url!r}"
    if "?" in url or "#" in url:
        return "url must be a base URL, without a query string or fragment"
    return None


_OVERVIEW_SECTIONS = ("http", "tcp", "udp")
_OVERVIEW_KINDS = ("routers", "services", "middlewares")


def _overview_summary(overview: Any) -> dict[str, Any] | None:
    """Traefik's /api/overview trimmed to its router/service/middleware counts,
    or None when the body isn't Traefik-shaped. A tool that echoed the whole
    body would hand back any JSON a caller-supplied URL returned."""
    if not isinstance(overview, dict) or not isinstance(overview.get("http"), dict):
        return None
    summary: dict[str, Any] = {}
    for section in _OVERVIEW_SECTIONS:
        block = overview.get(section)
        if not isinstance(block, dict):
            continue
        kept = {
            kind: {k: v for k, v in block[kind].items() if isinstance(v, int)}
            for kind in _OVERVIEW_KINDS
            if isinstance(block.get(kind), dict)
        }
        if kept:
            summary[section] = kept
    return summary


def register_discovery_tools(mcp: FastMCP, engine: DiscoveryEngine) -> None:
    """Register tools to trigger discovery, inspect its results, and connect new sources."""

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
    async def discovery_run_now(source: str | None = None) -> dict[str, Any]:
        """Run a discovery pass now. Without `source`, runs every enabled source.

        `source` must be one of the enabled sources (traefik, docker, authentik).
        """
        if source is None:
            events = await engine.run_all()
            return {"items": [e.model_dump(mode="json") for e in events]}
        try:
            source_type = SourceType(source)
        except ValueError:
            return {"error": f"unknown source {source!r}"}
        if source_type not in engine.sources:
            enabled = [s.value for s in engine.sources]
            return {"error": f"source {source!r} is not enabled; enabled: {enabled}"}
        event = await engine.run_source(source_type)
        return event.model_dump(mode="json")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def discovery_status() -> dict[str, Any]:
        """Return the most recent discovery pass summary for each enabled source."""
        return {"sources": engine.status()}

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def discovery_list_stale() -> dict[str, Any]:
        """List services that have gone stale (not seen for the configured threshold)."""
        return {"items": [s.model_dump(mode="json") for s in engine.list_stale()]}

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def discovery_connect_traefik(
        url: str, timeout_seconds: float = 10.0, retries: int = 3
    ) -> dict[str, Any]:
        """Validate a Traefik API URL for discovery and return the .env lines to enable it.

        Greenfield setups don't have Traefik yet, so this is deliberately not
        asked at install time -- call this once Traefik actually exists. Only
        live-tests the URL (fetches Traefik's overview); never writes a file,
        since the container has no filesystem access to the host's .env.
        """
        if error := _invalid_base_url(url) or _invalid_connection_params(timeout_seconds, retries):
            return {"ok": False, "error": error}
        client = TraefikClient(url, timeout=timeout_seconds, retries=retries)
        try:
            overview = await client.overview()
        except TraefikError as exc:
            return {"ok": False, "error": str(exc)}
        summary = _overview_summary(overview)
        if summary is None:
            return {
                "ok": False,
                "error": f"{url} answered, but not like the Traefik API "
                "(its /api/overview has no `http` section)",
            }
        return {
            "ok": True,
            "overview": summary,
            "env_lines": [
                f"TRAEFIK_API_URL={url}",
                f"TRAEFIK_TIMEOUT_SECONDS={timeout_seconds}",
                f"TRAEFIK_RETRIES={retries}",
            ],
            "next_step": _RESTART_HINT,
        }

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def discovery_connect_authentik(
        url: str, token: str, timeout_seconds: float = 10.0, retries: int = 3
    ) -> dict[str, Any]:
        """Validate an Authentik API URL/token for discovery and return the .env lines to enable it.

        Same rationale as discovery_connect_traefik: brownfield-only, call
        once Authentik exists. Only live-tests the credentials (lists
        applications); never writes a file, and never echoes the token back.
        """
        if error := _invalid_base_url(url) or _invalid_connection_params(timeout_seconds, retries):
            return {"ok": False, "error": error}
        client = AuthentikClient(url, token, timeout=timeout_seconds, retries=retries)
        try:
            applications = await client.list_applications()
        except AuthentikError as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "application_count": len(applications),
            "env_lines": [
                f"AUTHENTIK_API_URL={url}",
                "AUTHENTIK_TOKEN=<the token you just validated with>",
                f"AUTHENTIK_TIMEOUT_SECONDS={timeout_seconds}",
                f"AUTHENTIK_RETRIES={retries}",
            ],
            "next_step": _RESTART_HINT,
        }
