"""SQLite-backed CRUD for the hardware node registry."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, SQLModel, col, select

from registry_mcp.hardware.ansible_facts import DISCOVERY_FIELDS
from registry_mcp.logging import get_logger
from registry_mcp.models.hardware import (
    HardwareChangeEvent,
    HardwareNode,
    NodeStatus,
    _utcnow,
)
from registry_mcp.models.service import Service

_log = get_logger("hardware.store")

_HARDWARE_CREATED = "__created__"
_HARDWARE_DELETED = "__deleted__"

_MUTABLE_FIELDS = {
    "display_name",
    "role",
    "status",
    "ip_address",
    "mac_address",
    "os",
    "cpu_model",
    "cpu_cores",
    "ram_gb",
    "gpu_model",
    "storage",
    "storage_pools",
    "tags",
    "notes",
    "location",
    "ups_backed",
    "obtained_at",
    "cluster_member",
    "ansible_host",
    "ansible_groups",
    "last_confirmed_at",
    "last_seen_at",
}


class DuplicateNodeError(ValueError):
    """Raised when creating a node whose hostname already exists."""


class HardwareStore:
    """Persistence layer for hardware nodes; shares the registry SQLite engine."""

    def __init__(self, engine) -> None:
        self.engine = engine
        SQLModel.metadata.create_all(engine)

    def _record(
        self,
        session: Session,
        *,
        node_id: str | None,
        field: str,
        old: Any,
        new: Any,
        actor: str,
    ) -> None:
        def _s(v: Any) -> str | None:
            return None if v is None else str(v)

        session.add(
            HardwareChangeEvent(
                node_id=node_id,
                field=field,
                old=_s(old),
                new=_s(new),
                actor=actor,
            )
        )
        _log.info(
            "hardware_changed",
            node_id=node_id,
            field=field,
            old=_s(old),
            new=_s(new),
            actor=actor,
        )

    def create_node(self, node: HardwareNode, actor: str = "manual") -> HardwareNode:
        with Session(self.engine) as session:
            existing = session.exec(
                select(HardwareNode).where(HardwareNode.hostname == node.hostname)
            ).first()
            if existing:
                raise DuplicateNodeError(f"node with hostname {node.hostname!r} already exists")
            session.add(node)
            session.flush()
            self._record(
                session,
                node_id=node.id,
                field=_HARDWARE_CREATED,
                old=None,
                new=node.hostname,
                actor=actor,
            )
            session.commit()
            session.refresh(node)
            return node

    def upsert_from_discovery(
        self,
        *,
        hostname: str,
        ansible_host: str,
        ansible_groups: list[str] | None,
        fields: dict[str, Any],
        actor: str = "ansible",
    ) -> HardwareNode:
        """Create or refresh a node from a live Ansible fact-gather pass
        (Phase 9b). Only provenance fields (`DISCOVERY_FIELDS`, plus
        `ansible_host`/`ansible_groups`/`status`/`last_confirmed_at`/
        `last_seen_at`) are written — curated fields (`display_name`, `role`,
        `tags`, `notes`, `location`, ...) set via `hardware-add-node`/
        `hardware-update-node` are never touched, mirroring the Service
        curated-field convention (`registry/reconcile.py`). `ansible_groups`
        is `None` when the caller has no group membership to report (the
        ad-hoc `ansible ... -m setup` pass doesn't expose it) — that leaves
        an existing node's groups untouched rather than clobbering them with
        an empty list on every pass; a new node still gets `[]`."""
        now = _utcnow()
        discovered = {k: v for k, v in fields.items() if k in DISCOVERY_FIELDS}
        existing = self.get_node(hostname)
        if existing is None:
            node = HardwareNode(
                hostname=hostname,
                display_name=hostname,
                ansible_host=ansible_host,
                ansible_groups=ansible_groups or [],
                status=NodeStatus.confirmed,
                last_confirmed_at=now,
                last_seen_at=now,
                manual=False,
                **discovered,
            )
            return self.create_node(node, actor=actor)

        updates: dict[str, Any] = {
            **discovered,
            "ansible_host": ansible_host,
            "ansible_groups": ansible_groups,
            "status": NodeStatus.confirmed,
            "last_confirmed_at": now,
            "last_seen_at": now,
        }
        updated = self.update_node(existing.id, updates, actor=actor)
        assert updated is not None  # existing was just fetched above
        return updated

    def get_node(self, id_or_hostname: str) -> HardwareNode | None:
        with Session(self.engine) as session:
            node = session.get(HardwareNode, id_or_hostname)
            if node is not None:
                return node
            return session.exec(
                select(HardwareNode).where(HardwareNode.hostname == id_or_hostname)
            ).first()

    def list_nodes(
        self,
        *,
        role: str | None = None,
        status: str | None = None,
        tag: str | None = None,
    ) -> list[HardwareNode]:
        with Session(self.engine) as session:
            stmt = select(HardwareNode)
            if role is not None:
                stmt = stmt.where(HardwareNode.role == role)
            if status is not None:
                stmt = stmt.where(HardwareNode.status == status)
            nodes = list(session.exec(stmt).all())
        if tag is not None:
            nodes = [n for n in nodes if tag in n.tags]
        return nodes

    def update_node(
        self, node_id: str, updates: dict[str, Any], actor: str = "manual"
    ) -> HardwareNode | None:
        with Session(self.engine) as session:
            node = session.get(HardwareNode, node_id)
            if node is None:
                return None
            for field, value in updates.items():
                if field not in _MUTABLE_FIELDS or value is None:
                    continue
                old = getattr(node, field)
                if old == value:
                    continue
                setattr(node, field, value)
                self._record(session, node_id=node_id, field=field, old=old, new=value, actor=actor)
            node.updated_at = _utcnow()
            session.add(node)
            session.commit()
            session.refresh(node)
            return node

    def delete_node(self, node_id: str, actor: str = "manual") -> bool:
        with Session(self.engine) as session:
            node = session.get(HardwareNode, node_id)
            if node is None:
                return False
            self._record(
                session,
                node_id=node_id,
                field=_HARDWARE_DELETED,
                old=node.hostname,
                new=None,
                actor=actor,
            )
            session.delete(node)
            session.commit()
            return True

    def list_stale_nodes(self) -> list[HardwareNode]:
        with Session(self.engine) as session:
            return list(
                session.exec(
                    select(HardwareNode).where(HardwareNode.status == NodeStatus.stale)
                ).all()
            )

    def list_unconfirmed_nodes(self) -> list[HardwareNode]:
        with Session(self.engine) as session:
            return list(
                session.exec(
                    select(HardwareNode).where(HardwareNode.status == NodeStatus.unconfirmed)
                ).all()
            )

    def link_service(self, service_id: str, node_id: str, *, manual: bool = True) -> bool:
        """Write hardware_node_id onto a Service record. Returns False if not found."""
        with Session(self.engine) as session:
            service = session.get(Service, service_id)
            node = session.get(HardwareNode, node_id)
            if service is None or node is None:
                return False
            service.hardware_node_id = node_id
            service.manual_link = manual
            session.add(service)
            session.commit()
            _log.info(
                "service_linked_to_node",
                service_id=service_id,
                node_id=node_id,
                manual=manual,
            )
            return True

    def get_node_services(self, node_id: str) -> list[Service]:
        with Session(self.engine) as session:
            return list(
                session.exec(select(Service).where(Service.hardware_node_id == node_id)).all()
            )

    def capacity_summary(self) -> dict[str, Any]:
        """Aggregate storage pool capacity across all confirmed nodes."""
        nodes = self.list_nodes(status=NodeStatus.confirmed)
        total_gb = 0.0
        used_gb = 0.0
        free_gb = 0.0
        pools: list[dict[str, Any]] = []
        for node in nodes:
            for pool in node.storage_pools:
                pool_dict = pool if isinstance(pool, dict) else pool.model_dump()
                total_gb += pool_dict.get("total_gb", 0.0)
                used_gb += pool_dict.get("used_gb", 0.0)
                free_gb += pool_dict.get("free_gb", 0.0)
                pools.append({"node": node.hostname, **pool_dict})
        return {
            "total_gb": round(total_gb, 2),
            "used_gb": round(used_gb, 2),
            "free_gb": round(free_gb, 2),
            "confirmed_nodes": len(nodes),
            "pools": pools,
        }

    def list_change_events(
        self, *, node_id: str | None = None, limit: int = 100
    ) -> list[HardwareChangeEvent]:
        with Session(self.engine) as session:
            stmt = select(HardwareChangeEvent)
            if node_id is not None:
                stmt = stmt.where(HardwareChangeEvent.node_id == node_id)
            stmt = stmt.order_by(col(HardwareChangeEvent.created_at).desc()).limit(limit)
            return list(session.exec(stmt).all())
