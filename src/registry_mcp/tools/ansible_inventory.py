"""MCP tools for syncing a registered hardware node into the operator's real
Ansible inventory file (ADR-015).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from registry_mcp.config import Settings
from registry_mcp.hardware.store import HardwareStore
from registry_mcp.inventory import InventoryGateError, InventoryGateStore
from registry_mcp.inventory.writer import upsert_host


def register_ansible_inventory_tools(
    mcp: FastMCP,
    hardware_store: HardwareStore,
    inventory_gate: InventoryGateStore,
    settings: Settings,
    *,
    read_only: bool,
) -> None:
    """Register the `ansible-inventory-sync-node` request/confirm tool pair."""

    @mcp.tool(
        name="ansible-inventory-sync-node",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
    )
    def ansible_inventory_sync_node(id_or_hostname: str) -> dict[str, Any]:
        """Request syncing a registered hardware node's `ansible_host` and
        `ansible_groups` into the real Ansible inventory file
        (`ANSIBLE_INVENTORY_PATH`). Writes nothing yet — returns an
        arithmetic challenge that must be solved and passed to
        ansible-inventory-sync-node-confirm before the file is written. The
        node must already exist in the hardware registry (via
        hardware-add-node or hardware-discover-now); this tool never
        registers a new node."""
        if read_only:
            return {
                "status": "error",
                "error": "Server is in read-only mode (startup health check failed). "
                "Run system_health_check for details.",
            }
        if not settings.ansible_inventory_path:
            return {"error": "ANSIBLE_INVENTORY_PATH is not configured"}
        node = hardware_store.get_node(id_or_hostname)
        if node is None:
            return {"error": f"no hardware node found for {id_or_hostname!r}"}
        challenge = inventory_gate.request(
            node_id=node.id,
            node_hostname=node.hostname,
            actor="manual:ansible_inventory_sync_node",
            ttl_minutes=settings.ansible_inventory_write_challenge_ttl_minutes,
        )
        return {
            "request_id": challenge.id,
            "challenge": f"{challenge.x} + {challenge.y} = ?",
            "node": node.hostname,
            "expires_at": challenge.expires_at.isoformat(),
            "next_step": (
                f"Ask the user to solve {challenge.x} + {challenge.y}, then call "
                f"ansible-inventory-sync-node-confirm(request_id={challenge.id!r}, "
                f"answer=<their answer>) to write {node.hostname!r} into the inventory."
            ),
        }

    @mcp.tool(
        name="ansible-inventory-sync-node-confirm",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
    )
    def ansible_inventory_sync_node_confirm(request_id: str, answer: int) -> dict[str, Any]:
        """Complete an inventory sync by answering the math challenge from
        ansible-inventory-sync-node. A wrong or expired answer invalidates
        the challenge — call ansible-inventory-sync-node again for a new
        one."""
        if read_only:
            return {
                "status": "error",
                "error": "Server is in read-only mode (startup health check failed). "
                "Run system_health_check for details.",
            }
        try:
            challenge = inventory_gate.confirm(request_id, answer)
        except InventoryGateError as exc:
            return {"error": str(exc)}
        node = hardware_store.get_node(challenge.node_id)
        if node is None:
            return {
                "error": f"no hardware node found for id {challenge.node_id!r} "
                "(may have been deleted since the request)"
            }
        if not settings.ansible_inventory_path:
            return {"error": "ANSIBLE_INVENTORY_PATH is not configured"}
        ansible_host = node.ansible_host or node.ip_address
        upsert_host(
            Path(settings.ansible_inventory_path),
            node.hostname,
            ansible_host,
            node.ansible_groups,
        )
        return {
            "synced": True,
            "hostname": node.hostname,
            "ansible_host": ansible_host,
            "groups": node.ansible_groups,
            "inventory_path": settings.ansible_inventory_path,
        }
