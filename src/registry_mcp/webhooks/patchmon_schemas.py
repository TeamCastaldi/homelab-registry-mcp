"""Patchmon webhook payloads (ADR-020): two accepted shapes, one normalized alert.

* `PatchmonWebhookSchema` — the flat patch-alert shape: one host, one service,
  one version bump. A `patchmon_callback_url` from an older sender is ignored:
  nothing calls back to PatchMon (ADR-020's 2026-09-27 amendment).
* `PatchmonNativeAlert` — the generic body PatchMon's own webhook destination
  sends (`event_type`, `severity`, `title`, `message`, `reference`, `metadata`),
  read from its `server-source-code/internal/queue/notification_worker.go`. Its
  threshold alerts (`host_security_updates_exceeded`,
  `host_pending_updates_exceeded`) name the host in `metadata.host_name` and
  PatchMon's own id for it in `metadata.host_id`. That name is PatchMon's
  display name: the friendly name when one is set, which can hold spaces. Such
  an alert is resolvable by its id instead (`IgnoredAlert.lookup_host_id`), and
  `normalize()` takes the hostname the webhook looked up.

Every value that can reach the Ansible playbook (host, service, versions, event)
is held to a character allowlist here, at the edge: no spaces, no pattern
characters (`,:!&*`), no leading `-`, and no `{`/`}`, so none of it can widen a
`--limit` or carry a Jinja template into the playbook's extra-vars.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, StringConstraints, field_validator

_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,252}$"
_VERSION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._+:~-]{0,127}$"
_EVENT_PATTERN = r"^[a-z0-9][a-z0-9_.-]{0,63}$"
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
_NAME_RE = re.compile(_NAME_PATTERN)
_MAX_TEXT_CHARS = 1000

HostName = Annotated[str, StringConstraints(strip_whitespace=True, pattern=_NAME_PATTERN)]
Version = Annotated[str, StringConstraints(strip_whitespace=True, pattern=_VERSION_PATTERN)]
EventName = Annotated[
    str, StringConstraints(strip_whitespace=True, to_lower=True, pattern=_EVENT_PATTERN)
]


def _clip(value: object) -> str | None:
    """Free text from the sender is display-only: clipped, never rejected for length."""
    return None if value is None else str(value).strip()[:_MAX_TEXT_CHARS]


ShortText = Annotated[str | None, BeforeValidator(_clip)]


@dataclass(frozen=True)
class PatchAlert:
    """What either shape normalizes to: everything an approval row needs."""

    event: str
    target_host: str
    service: str | None = None
    current_version: str | None = None
    target_version: str | None = None
    severity: str | None = None
    patchmon_host_id: str | None = None
    summary: str | None = None


@dataclass(frozen=True)
class IgnoredAlert:
    """A well-formed alert that can't become an approval, and why.

    `lookup_host_id` is set when the only problem is the host's name and the
    alert carries PatchMon's id for it: looking the id up could still name the
    host exactly."""

    event: str
    reason: str
    lookup_host_id: str | None = None


def _host_id(value: object) -> str | None:
    """PatchMon addresses a host by UUID and nothing else; anything else is dropped."""
    text = str(value).strip() if value is not None else ""
    return text if _UUID_RE.match(text) else None


class PatchmonWebhookSchema(BaseModel):
    """The flat patch-alert payload."""

    model_config = ConfigDict(extra="ignore")

    event: EventName
    target_host: HostName
    service: HostName | None = None
    current_version: Version | None = None
    target_version: Version | None = None
    severity: ShortText = None
    patchmon_host_id: str | None = None

    @field_validator("patchmon_host_id")
    @classmethod
    def _check_host_id(cls, value: str | None) -> str | None:
        return _host_id(value)

    def normalize(self) -> PatchAlert:
        summary = f"{self.service or self.target_host}"
        if self.target_version:
            summary += f" {self.current_version or '?'} -> {self.target_version}"
        return PatchAlert(
            event=self.event,
            target_host=self.target_host,
            service=self.service,
            current_version=self.current_version,
            target_version=self.target_version,
            severity=self.severity,
            patchmon_host_id=self.patchmon_host_id,
            summary=summary,
        )


class PatchmonReference(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: ShortText = None
    id: ShortText = None


class PatchmonNativeAlert(BaseModel):
    """PatchMon's generic webhook body. `text` (a Slack-style rendering) and
    `app_link` are accepted and unused."""

    model_config = ConfigDict(extra="ignore")

    event_type: EventName
    severity: ShortText = None
    title: ShortText = None
    message: ShortText = None
    reference: PatchmonReference | None = None
    metadata: dict[str, Any] = {}

    @property
    def host_id(self) -> str | None:
        """PatchMon's UUID for the host: `metadata.host_id`, else a host
        `reference.id`."""
        host_id = _host_id(self.metadata.get("host_id"))
        if host_id is None and self.reference is not None and self.reference.type == "host":
            host_id = _host_id(self.reference.id)
        return host_id

    def normalize(self, resolved_hostname: str | None = None) -> PatchAlert | IgnoredAlert:
        """The alert as an approval, or why it can't be one.

        `resolved_hostname` is the `hostname` PatchMon's API holds for this
        alert's host id. It is used only when `metadata.host_name` isn't a plain
        inventory name, so an alert that works by its own name never changes
        target, and it is held to the same allowlist.
        """
        host_name = str(self.metadata.get("host_name") or "").strip()
        host_id = self.host_id
        if not host_name and not host_id:
            return IgnoredAlert(self.event_type, "alert names no host (metadata.host_name)")
        if not _NAME_RE.match(host_name):
            # PatchMon sends a host's friendly name when it has one, and that
            # can hold spaces. It is never guessed into an inventory name; only
            # PatchMon's own record for the host id can stand in for it.
            reason = (
                f"host name {host_name!r} is not a plain inventory host name"
                if host_name
                else "alert names no host (metadata.host_name)"
            )
            resolved = (resolved_hostname or "").strip()
            if not resolved:
                return IgnoredAlert(self.event_type, reason, lookup_host_id=host_id)
            if not _NAME_RE.match(resolved):
                return IgnoredAlert(
                    self.event_type,
                    f"{reason}, and PatchMon's hostname for host {host_id} ({resolved!r}) "
                    "is not one either",
                )
            host_name = resolved
        return PatchAlert(
            event=self.event_type,
            target_host=host_name,
            severity=self.severity,
            patchmon_host_id=host_id,
            summary=self.title or self.message,
        )
