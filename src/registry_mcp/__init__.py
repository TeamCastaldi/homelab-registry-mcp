"""homelab-registry-mcp: authoritative catalog of homelab services over MCP."""

try:
    from importlib.metadata import version

    __version__ = version("homelab-registry-mcp")
except Exception:
    __version__ = "unknown"
