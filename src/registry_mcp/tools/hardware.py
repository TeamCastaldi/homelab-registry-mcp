"""MCP tools for the hardware node registry."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from registry_mcp.hardware.store import DuplicateNodeError, HardwareStore
from registry_mcp.models.hardware import HardwareNode, NodeRole
from registry_mcp.registry import RegistryStore


def summarize_discovery_status(nodes: list[HardwareNode]) -> dict[str, Any]:
    """Aggregate registry state for ``hardware-discovery-status``: node counts by
    status and the most recent confirmation/sighting. Pure function over a node
    list so it can be unit-tested without the MCP server. Live push-mode
    fact-gather remains Phase 9b."""
    by_status: dict[str, int] = {}
    last_confirmed_at: str | None = None
    last_seen_at: str | None = None
    for node in nodes:
        by_status[node.status] = by_status.get(node.status, 0) + 1
        if node.last_confirmed_at is not None:
            stamp = node.last_confirmed_at.isoformat()
            if last_confirmed_at is None or stamp > last_confirmed_at:
                last_confirmed_at = stamp
        if node.last_seen_at is not None:
            stamp = node.last_seen_at.isoformat()
            if last_seen_at is None or stamp > last_seen_at:
                last_seen_at = stamp
    return {
        "total_nodes": len(nodes),
        "by_status": by_status,
        "last_confirmed_at": last_confirmed_at,
        "last_seen_at": last_seen_at,
        "push_discovery": "not_implemented (Phase 9b)",
    }


def register_hardware_tools(
    mcp: FastMCP, store: RegistryStore, hardware_store: HardwareStore
) -> None:
    """Register hardware node CRUD and linking tools."""

    @mcp.tool(name="hardware-add-node")
    def hardware_add_node(
        hostname: str,
        display_name: str,
        role: str = "other",
        ip_address: str | None = None,
        notes: str = "",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Manually register a hardware node. Status starts as unconfirmed until live-probed."""
        try:
            node = HardwareNode(
                hostname=hostname,
                display_name=display_name,
                role=NodeRole(role),
                ip_address=ip_address,
                notes=notes,
                tags=tags or [],
            )
            created = hardware_store.create_node(node)
            return created.model_dump(mode="json")
        except DuplicateNodeError as exc:
            return {"error": str(exc)}
        except ValueError as exc:
            return {"error": f"invalid role {role!r}: {exc}"}

    @mcp.tool(name="hardware-get-node")
    def hardware_get_node(id: str) -> dict[str, Any]:
        """Fetch a hardware node by id or hostname."""
        node = hardware_store.get_node(id)
        if node is None:
            return {"error": f"no node found for {id!r}"}
        return node.model_dump(mode="json")

    @mcp.tool(name="hardware-list-nodes")
    def hardware_list_nodes(
        role: str | None = None,
        status: str | None = None,
        tag: str | None = None,
    ) -> list[dict[str, Any]]:
        """List hardware nodes, optionally filtered by role, status, or tag."""
        return [
            n.model_dump(mode="json")
            for n in hardware_store.list_nodes(role=role, status=status, tag=tag)
        ]

    @mcp.tool(name="hardware-update-node")
    def hardware_update_node(id: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Patch mutable fields on a hardware node."""
        updated = hardware_store.update_node(id, updates)
        if updated is None:
            return {"error": f"no node found for {id!r}"}
        return updated.model_dump(mode="json")

    @mcp.tool(name="hardware-delete-node")
    def hardware_delete_node(id: str) -> dict[str, Any]:
        """Hard-delete a hardware node. Change events are preserved."""
        deleted = hardware_store.delete_node(id)
        if not deleted:
            return {"error": f"no node found for {id!r}"}
        return {"deleted": True, "id": id}

    @mcp.tool(name="hardware-link-service")
    def hardware_link_service(service_id: str, node_id: str) -> dict[str, Any]:
        """Manually link a service to a hardware node. Sets manual_link=True to prevent
        auto-override."""
        ok = hardware_store.link_service(service_id, node_id, manual=True)
        if not ok:
            return {"error": f"service {service_id!r} or node {node_id!r} not found"}
        return {"linked": True, "service_id": service_id, "node_id": node_id}

    @mcp.tool(name="hardware-node-services")
    def hardware_node_services(node_id: str) -> list[dict[str, Any]]:
        """List all services linked to a hardware node."""
        return [s.model_dump(mode="json") for s in hardware_store.get_node_services(node_id)]

    @mcp.tool(name="hardware-list-unconfirmed")
    def hardware_list_unconfirmed() -> list[dict[str, Any]]:
        """List nodes created from inventory or pull-mode that have not yet been live-probed."""
        return [n.model_dump(mode="json") for n in hardware_store.list_unconfirmed_nodes()]

    @mcp.tool(name="hardware-list-stale")
    def hardware_list_stale() -> list[dict[str, Any]]:
        """List nodes marked stale (not seen for the configured threshold of passes)."""
        return [n.model_dump(mode="json") for n in hardware_store.list_stale_nodes()]

    @mcp.tool(name="hardware-capacity-summary")
    def hardware_capacity_summary() -> dict[str, Any]:
        """Aggregate storage pool capacity across all confirmed nodes."""
        return hardware_store.capacity_summary()

    @mcp.tool(name="hardware-discover-now")
    def hardware_discover_now(host: str | None = None) -> dict[str, Any]:
        """Trigger a live Ansible fact-gather for one or all nodes. (Phase 9b — not implemented.)"""
        return {
            "status": "not_implemented",
            "message": "Push-mode discovery is Phase 9b. Use hardware-add-node to register nodes.",
        }

    @mcp.tool(name="hardware-discovery-status")
    def hardware_discovery_status() -> dict[str, Any]:
        """Summarize hardware registry state: node counts by status and the most
        recent confirmation/sighting. Live push-mode fact-gather is Phase 9b."""
        return summarize_discovery_status(hardware_store.list_nodes())

    @mcp.resource("hardware://all")
    def hardware_all_resource() -> list[dict[str, Any]]:
        """Summary index of all hardware nodes."""
        return [n.model_dump(mode="json") for n in hardware_store.list_nodes()]

    @mcp.resource("hardware://{node_id}")
    def hardware_node_resource(node_id: str) -> dict[str, Any]:
        """Full detail for a hardware node."""
        node = hardware_store.get_node(node_id)
        if node is None:
            return {"error": f"no node found for {node_id!r}"}
        return node.model_dump(mode="json")
