"""Math-problem confirmation gate shared by every hard-delete MCP tool.

`registry_delete_service` and `hardware-delete-node` each split into a
request step (returns an `x + y = ?` challenge, deletes nothing) and a
confirm step (validates the answer, then deletes) — see `tools/registry.py`
and `tools/hardware.py`. `DeletionGateStore` is the shared persistence and
validation layer behind both.
"""

from registry_mcp.deletion.store import DeletionGateError, DeletionGateStore

__all__ = ["DeletionGateError", "DeletionGateStore"]
