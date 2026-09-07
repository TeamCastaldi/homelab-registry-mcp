"""documentation-mcp integration: MCP client and passthrough MCP tool."""

from registry_mcp.integrations.docs.client import DocsMcpClient, DocsMcpError
from registry_mcp.integrations.docs.tools import register_docs_tools

__all__ = ["DocsMcpClient", "DocsMcpError", "register_docs_tools"]
