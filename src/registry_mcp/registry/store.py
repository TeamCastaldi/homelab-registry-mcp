"""SQLite-backed CRUD for the service registry, with change-event logging."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, col, create_engine, delete, select

from registry_mcp.logging import get_logger
from registry_mcp.models import (
    FIELD_CREATED,
    FIELD_DELETED,
    AuthMode,
    Category,
    ChangeEvent,
    DiscoveryEvent,
    DiscoveryStatus,
    Service,
    ServiceSource,
    SourceType,
)
from registry_mcp.models.service import utcnow
from registry_mcp.registry.reconcile import match_service, provenance_updates

if TYPE_CHECKING:
    from registry_mcp.discovery.base import DiscoveredService

# Reference the table models so every table is registered before create_all.
_ = (ServiceSource, DiscoveryEvent)

_log = get_logger("registry.events")

_MUTABLE_FIELDS = {
    "display_name",
    "category",
    "host",
    "urls",
    "traefik_router",
    "authentik_app_slug",
    "auth_mode",
    "traefik_auth_mode",
    "authentik_auth_mode",
    "auth_mode_conflict",
    "tags",
    "notes",
}


class DuplicateServiceError(ValueError):
    """Raised when creating a service whose name already exists."""


def _as_str(value: Any) -> str | None:
    return None if value is None else str(value)


class RegistryStore:
    """Thin persistence layer over SQLModel/SQLite."""

    def __init__(self, db_path: str) -> None:
        connect_args = {"check_same_thread": False}
        if db_path == ":memory:":
            self.engine = create_engine(
                "sqlite://",
                connect_args=connect_args,
                poolclass=StaticPool,
            )
        else:
            Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
            self.engine = create_engine(f"sqlite:///{db_path}", connect_args=connect_args)
        SQLModel.metadata.create_all(self.engine)
        self._migrate(self.engine)

    @staticmethod
    def _migrate(engine) -> None:
        """Apply any missing columns to existing tables (forward-only, additive only)."""
        with engine.connect() as conn:
            existing = {row[1] for row in conn.execute(text("PRAGMA table_info(service)"))}
            if "auth_mode_conflict" not in existing:
                conn.execute(
                    text(
                        "ALTER TABLE service ADD COLUMN "
                        "auth_mode_conflict BOOLEAN NOT NULL DEFAULT 0"
                    )
                )
                conn.commit()
            for col in ("traefik_auth_mode", "authentik_auth_mode"):
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE service ADD COLUMN {col} VARCHAR DEFAULT NULL"))
            for col_name, col_def in (
                ("hardware_node_id", "VARCHAR DEFAULT NULL"),
                ("manual_link", "BOOLEAN NOT NULL DEFAULT 0"),
            ):
                if col_name not in existing:
                    conn.execute(text(f"ALTER TABLE service ADD COLUMN {col_name} {col_def}"))
            conn.commit()

    def _record_change(
        self,
        session: Session,
        *,
        service_id: str | None,
        field: str,
        old: Any,
        new: Any,
        actor: str,
    ) -> None:
        session.add(
            ChangeEvent(
                service_id=service_id,
                field=field,
                old=_as_str(old),
                new=_as_str(new),
                actor=actor,
            )
        )
        _log.info(
            "service_changed",
            service_id=service_id,
            field=field,
            old=_as_str(old),
            new=_as_str(new),
            actor=actor,
        )

    def create_service(self, service: Service, actor: str = "manual") -> Service:
        with Session(self.engine) as session:
            if session.exec(select(Service).where(Service.name == service.name)).first():
                raise DuplicateServiceError(f"service named {service.name!r} already exists")
            session.add(service)
            session.flush()
            session.add(
                ServiceSource(
                    service_id=service.id,
                    source=SourceType.manual,
                    external_id=service.name,
                )
            )
            self._record_change(
                session,
                service_id=service.id,
                field=FIELD_CREATED,
                old=None,
                new=service.name,
                actor=actor,
            )
            session.commit()
            session.refresh(service)
            return service

    def get_service(self, id_or_name: str) -> Service | None:
        with Session(self.engine) as session:
            service = session.get(Service, id_or_name)
            if service is not None:
                return service
            return session.exec(select(Service).where(Service.name == id_or_name)).first()

    def list_services(
        self,
        *,
        category: str | None = None,
        host: str | None = None,
        tag: str | None = None,
    ) -> list[Service]:
        with Session(self.engine) as session:
            statement = select(Service)
            if category is not None:
                statement = statement.where(Service.category == category)
            if host is not None:
                statement = statement.where(Service.host == host)
            services = list(session.exec(statement).all())
        if tag is not None:
            services = [s for s in services if tag in s.tags]
        return services

    def update_service(
        self,
        service_id: str,
        updates: dict[str, Any],
        actor: str = "manual",
    ) -> Service | None:
        with Session(self.engine) as session:
            service = session.get(Service, service_id)
            if service is None:
                return None
            for field, value in updates.items():
                if field not in _MUTABLE_FIELDS or value is None:
                    continue
                old = getattr(service, field)
                if old == value:
                    continue
                setattr(service, field, value)
                self._record_change(
                    session,
                    service_id=service_id,
                    field=field,
                    old=old,
                    new=value,
                    actor=actor,
                )
            service.updated_at = utcnow()
            session.add(service)
            session.commit()
            session.refresh(service)
            return service

    def delete_service(self, service_id: str, actor: str = "manual") -> bool:
        with Session(self.engine) as session:
            service = session.get(Service, service_id)
            if service is None:
                return False
            for source in session.exec(
                select(ServiceSource).where(ServiceSource.service_id == service_id)
            ).all():
                session.delete(source)
            self._record_change(
                session,
                service_id=service_id,
                field=FIELD_DELETED,
                old=service.name,
                new=None,
                actor=actor,
            )
            session.delete(service)
            session.commit()
            return True

    def list_change_events(
        self,
        *,
        service_id: str | None = None,
        limit: int = 100,
    ) -> list[ChangeEvent]:
        with Session(self.engine) as session:
            statement = select(ChangeEvent)
            if service_id is not None:
                statement = statement.where(ChangeEvent.service_id == service_id)
            statement = statement.order_by(col(ChangeEvent.created_at).desc()).limit(limit)
            return list(session.exec(statement).all())

    def list_discovery_events(
        self,
        *,
        source: str | None = None,
        limit: int = 100,
    ) -> list[DiscoveryEvent]:
        with Session(self.engine) as session:
            statement = select(DiscoveryEvent)
            if source is not None:
                statement = statement.where(DiscoveryEvent.source == source)
            statement = statement.order_by(col(DiscoveryEvent.started_at).desc()).limit(limit)
            return list(session.exec(statement).all())

    def list_stale_services(self) -> list[Service]:
        with Session(self.engine) as session:
            return list(session.exec(select(Service).where(Service.stale)).all())

    def record_discovery_event(
        self,
        source: SourceType,
        *,
        started_at: datetime,
        finished_at: datetime,
        status: DiscoveryStatus,
        counts: dict[str, int] | None = None,
        error: str | None = None,
    ) -> DiscoveryEvent:
        counts = counts or {}
        with Session(self.engine) as session:
            event = DiscoveryEvent(
                source=source,
                started_at=started_at,
                finished_at=finished_at,
                status=status,
                items_seen=counts.get("items_seen", 0),
                items_new=counts.get("items_new", 0),
                items_changed=counts.get("items_changed", 0),
                items_missing=counts.get("items_missing", 0),
                error=error,
            )
            session.add(event)
            session.commit()
            session.refresh(event)
            return event

    def reconcile(
        self,
        source: SourceType,
        discovered: list[DiscoveredService],
        *,
        stale_threshold: int,
        identity_resolver: Callable[[DiscoveredService, list[Service]], Service | None]
        | None = None,
        metadata_enricher: Callable[[DiscoveredService], dict[str, Any] | None] | None = None,
    ) -> dict[str, int]:
        """Merge a source's discovered services into the registry; track staleness.

        ``identity_resolver`` and ``metadata_enricher`` are optional callables
        supplied by the reasoning layer (Phase 7). When deterministic matching
        fails, ``identity_resolver`` may still resolve the candidate to an
        existing service; when a brand-new service is created,
        ``metadata_enricher`` may fill curated fields. Both default to ``None``,
        which preserves the deterministic-only behavior. This module never calls
        an LLM directly — the reasoning lives behind these injected callables.
        """
        now = utcnow()
        actor = f"discovery:{source}"
        items_new = 0
        items_changed = 0
        seen_ids: set[str] = set()

        with Session(self.engine) as session:
            services = list(session.exec(select(Service)).all())
            for item in discovered:
                match = match_service(services, item)
                if match is None and identity_resolver is not None:
                    match = identity_resolver(item, services)
                if match is None:
                    enriched = {}
                    if metadata_enricher is not None:
                        enriched = metadata_enricher(item) or {}
                    match = Service(
                        name=item.name,
                        display_name=enriched.get("display_name") or item.display_name or item.name,
                        category=enriched.get("category") or item.category or Category.other,
                        host=item.host,
                        urls=item.urls,
                        traefik_router=item.traefik_router,
                        authentik_app_slug=item.authentik_app_slug,
                        auth_mode=enriched.get("auth_mode") or item.auth_mode or AuthMode.unknown,
                        tags=item.tags,
                        notes=enriched.get("notes", ""),
                        manual=False,
                        last_seen_at=now,
                        traefik_auth_mode=(
                            item.auth_mode
                            if item.source == SourceType.traefik
                            and item.auth_mode not in (AuthMode.unknown, None)
                            else None
                        ),
                        authentik_auth_mode=(
                            item.auth_mode
                            if item.source == SourceType.authentik
                            and item.auth_mode not in (AuthMode.unknown, None)
                            else None
                        ),
                    )
                    session.add(match)
                    session.flush()
                    services.append(match)
                    self._record_change(
                        session,
                        service_id=match.id,
                        field=FIELD_CREATED,
                        old=None,
                        new=match.name,
                        actor=actor,
                    )
                    items_new += 1
                else:
                    updates = provenance_updates(match, item)
                    for field, value in updates.items():
                        old = getattr(match, field)
                        setattr(match, field, value)
                        self._record_change(
                            session,
                            service_id=match.id,
                            field=field,
                            old=old,
                            new=value,
                            actor=actor,
                        )
                    if updates:
                        items_changed += 1
                    if match.stale:
                        match.stale = False
                        self._record_change(
                            session,
                            service_id=match.id,
                            field="stale",
                            old="True",
                            new="False",
                            actor=actor,
                        )
                    match.last_seen_at = now
                    match.updated_at = now
                    session.add(match)

                seen_ids.add(match.id)
                self._upsert_source(session, match.id, source, item, now)

            items_missing = self._mark_stale(session, source, seen_ids, stale_threshold, actor)
            session.commit()

        return {
            "items_seen": len(discovered),
            "items_new": items_new,
            "items_changed": items_changed,
            "items_missing": items_missing,
        }

    @staticmethod
    def _upsert_source(
        session: Session,
        service_id: str,
        source: SourceType,
        item: DiscoveredService,
        now: datetime,
    ) -> None:
        row = session.exec(
            select(ServiceSource).where(
                ServiceSource.service_id == service_id,
                ServiceSource.source == source,
            )
        ).first()
        if row is None:
            session.add(
                ServiceSource(
                    service_id=service_id,
                    source=source,
                    external_id=item.external_id,
                    raw=item.raw,
                    last_seen_at=now,
                    missed_passes=0,
                )
            )
        else:
            row.external_id = item.external_id
            row.raw = item.raw
            row.last_seen_at = now
            row.missed_passes = 0
            session.add(row)

    def _mark_stale(
        self,
        session: Session,
        source: SourceType,
        seen_ids: set[str],
        stale_threshold: int,
        actor: str,
    ) -> int:
        items_missing = 0
        rows = session.exec(select(ServiceSource).where(ServiceSource.source == source)).all()
        for row in rows:
            if row.service_id in seen_ids:
                continue
            items_missing += 1
            row.missed_passes += 1
            session.add(row)
            if row.missed_passes >= stale_threshold:
                service = session.get(Service, row.service_id)
                if service is not None and not service.stale:
                    service.stale = True
                    self._record_change(
                        session,
                        service_id=service.id,
                        field="stale",
                        old="False",
                        new="True",
                        actor=actor,
                    )
                    session.add(service)
        return items_missing

    def purge_old_events(self, retention_days: int) -> dict[str, int]:
        cutoff = utcnow() - timedelta(days=retention_days)
        with Session(self.engine) as session:
            changes = session.exec(
                delete(ChangeEvent).where(col(ChangeEvent.created_at) < cutoff)
            ).rowcount
            discoveries = session.exec(
                delete(DiscoveryEvent).where(col(DiscoveryEvent.started_at) < cutoff)
            ).rowcount
            session.commit()
        return {"change_events": changes or 0, "discovery_events": discoveries or 0}
