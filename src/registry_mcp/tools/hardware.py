"""MCP tools for the hardware node registry."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

from registry_mcp.config import Settings
from registry_mcp.hardware import ansible_facts
from registry_mcp.hardware.store import DuplicateNodeError, HardwareStore
from registry_mcp.logging import get_logger
from registry_mcp.models.hardware import HardwareNode, NodeRole
from registry_mcp.registry import RegistryStore

_log = get_logger("tools.hardware")


def summarize_discovery_status(nodes: list[HardwareNode]) -> dict[str, Any]:
    """Aggregate registry state for ``hardware-discovery-status``: node counts by
    status and the most recent confirmation/sighting. Pure function over a node
    list so it can be unit-tested without the MCP server."""
    by_status: dict[str, int] = {}
    last_confirmed: datetime | None = None
    last_seen: datetime | None = None
    for node in nodes:
        # str() keeps the key JSON-safe even if status is a StrEnum.
        key = str(node.status)
        by_status[key] = by_status.get(key, 0) + 1
        # Compare datetimes directly — comparing ISO strings is brittle across
        # differing tz offsets — and serialize once at the end.
        if node.last_confirmed_at is not None and (
            last_confirmed is None or node.last_confirmed_at > last_confirmed
        ):
            last_confirmed = node.last_confirmed_at
        if node.last_seen_at is not None and (last_seen is None or node.last_seen_at > last_seen):
            last_seen = node.last_seen_at
    return {
        "total_nodes": len(nodes),
        "by_status": by_status,
        "last_confirmed_at": last_confirmed.isoformat() if last_confirmed else None,
        "last_seen_at": last_seen.isoformat() if last_seen else None,
        "push_discovery": "implemented (hardware-discover-now)",
    }


def _discover_now_unavailable(settings: Settings) -> str | None:
    """Return an error message if live Ansible fact-gather can't run."""
    if not settings.ansible_cfg_path:
        return "ANSIBLE_CFG_PATH is not configured (needed to reach the operator's inventory)."
    if not settings.ssh_key_path:
        return "SSH_KEY_PATH is not configured (needed to SSH into inventory hosts)."
    return None


async def discover_now(
    hardware_store: HardwareStore,
    settings: Settings,
    host: str | None = None,
) -> dict[str, Any]:
    """Run one live Ansible fact-gather pass and upsert results into the
    hardware registry. Pulled out of the tool closure so it's unit-testable
    without a running FastMCP server."""
    pattern = host or "all"
    # Callers gate on _discover_now_unavailable() first, so both are set.
    assert settings.ansible_cfg_path is not None
    assert settings.ssh_key_path is not None
    try:
        facts_by_host, failures = await ansible_facts.gather_facts(
            pattern=pattern,
            ansible_cfg_path=settings.ansible_cfg_path,
            ssh_key_path=settings.ssh_key_path,
            ssh_user=settings.ssh_default_user,
        )
    except ansible_facts.AnsibleFactsError as exc:
        return {"status": "error", "error": str(exc)}

    created: list[str] = []
    updated: list[str] = []
    for inventory_host, facts in facts_by_host.items():
        hostname = ansible_facts.hostname_from_facts(facts) or inventory_host
        fields = ansible_facts.node_fields_from_facts(facts)
        was_new = hardware_store.get_node(hostname) is None
        node = hardware_store.upsert_from_discovery(
            hostname=hostname,
            ansible_host=inventory_host,
            ansible_groups=[],
            fields=fields,
        )
        (created if was_new else updated).append(node.hostname)

    if failures:
        _log.warning("hardware_discover_now_failures", pattern=pattern, failures=failures)

    return {
        "status": "ok",
        "pattern": pattern,
        "nodes_created": created,
        "nodes_updated": updated,
        "failures": failures,
    }


def register_hardware_tools(
    mcp: FastMCP,
    store: RegistryStore,
    hardware_store: HardwareStore,
    settings: Settings,
    read_only: bool = False,
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
    async def hardware_discover_now(host: str | None = None) -> dict[str, Any]:
        """Run a live Ansible fact-gather pass (`ansible <host|all> -m setup`)
        against the operator's own inventory (`ANSIBLE_CFG_PATH`) and upsert
        the results into the hardware registry. Only provenance fields are
        written — curated fields (display_name, role, tags, notes, ...) are
        untouched. Pass `host` to target one inventory host/group; omit it to
        probe the whole inventory."""
        if read_only:
            return {
                "error": "Server is in read-only mode (startup health check failed). "
                "Run system_health_check for details."
            }
        if err := _discover_now_unavailable(settings):
            return {"error": err}
        return await discover_now(hardware_store, settings, host)

    @mcp.tool(name="hardware-discovery-status")
    def hardware_discovery_status() -> dict[str, Any]:
        """Summarize hardware registry state: node counts by status and the most
        recent confirmation/sighting."""
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
