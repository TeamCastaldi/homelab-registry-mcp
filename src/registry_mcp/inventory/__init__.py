"""Math-problem confirmation gate for the Ansible inventory-sync tool.

`ansible-inventory-sync-node` splits into a request step (returns an
`x + y = ?` challenge, writes nothing) and a confirm step (validates the
answer, then writes) — see `tools/ansible_inventory.py`. `InventoryGateStore`
is the persistence and validation layer behind both, modeled on
`deletion/store.py`'s `DeletionGateStore` but kept as a sibling rather than a
shared table, since this gates a write, not a delete.
"""

from registry_mcp.inventory.store import InventoryGateError, InventoryGateStore

__all__ = ["InventoryGateError", "InventoryGateStore"]
