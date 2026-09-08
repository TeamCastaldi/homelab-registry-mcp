"""documentation-mcp MCP tool: a passthrough relay, no new persistence."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from registry_mcp.config import Settings
from registry_mcp.integrations.docs.client import DocsMcpClient, DocsMcpError

_ERROR_PREFIXES = (
    "CRITICAL:",
    "ERROR: Missing required parameters",
    "ERROR: Official documentation not found locally",
)


def register_docs_tools(mcp: FastMCP, settings: Settings) -> None:
    """Register the `get_service_documentation` passthrough tool."""

    def _client() -> DocsMcpClient | None:
        if not settings.docs_mcp_url or not settings.docs_mcp_token:
            return None
        return DocsMcpClient(
            settings.docs_mcp_url,
            settings.docs_mcp_token,
            timeout=settings.docs_mcp_timeout_seconds,
        )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def get_service_documentation(
        service_name: str, version: str, topic: str | None = None
    ) -> dict[str, Any]:
        """Fetch official, version-specific documentation for a homelab service.

        Relays documentation-mcp's response verbatim. `version` is required and is
        never guessed. Returns `{"content": ...}` on success or `{"error": ...}`
        when documentation-mcp reports a validation failure, no official source
        found, is unreachable, or is not configured.
        """
        client = _client()
        if client is None:
            return {"error": "DOCS_MCP_URL and DOCS_MCP_TOKEN must be configured"}
        try:
            text = await client.get_homelab_docs(service_name, version, topic)
        except DocsMcpError as exc:
            return {"error": str(exc)}

        if text.startswith(_ERROR_PREFIXES):
            return {"error": text}
        return {"content": text}
