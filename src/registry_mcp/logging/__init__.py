"""Structured logging setup."""

from registry_mcp.logging.events import configure_logging, get_logger
from registry_mcp.logging.tool_calls import install_tool_call_logging

__all__ = ["configure_logging", "get_logger", "install_tool_call_logging"]
