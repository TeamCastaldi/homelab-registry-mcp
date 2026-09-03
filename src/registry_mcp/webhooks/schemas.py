"""Pydantic models for inbound Dockhand alerts, plus the pure parsing helpers
that reduce them to one internal shape.

Dockhand ships two very different bodies depending on how the operator wired the
notification, so both are accepted:

* **Structured** (`DockhandStructuredAlert`) — an explicit
  `{"event", "container", "current_image", "latest_image", "server"}` object.
  This is what a custom payload template (or a future Dockhand build) sends, and
  it is the shape worth designing for.
* **Generic** (`DockhandGenericAlert`) — the flat
  `{"title", "message", "agent"}` body Dockhand's stock *generic webhook*
  notifier documents, e.g. ``{"title": "Container updated: c1", "message":
  "image=sha256:new old_image=sha256:old", "agent": "Dockhand"}``. Everything
  useful is prose, so it has to be parsed out.

Both normalize to `NormalizedAlert`. `AlertKind.ignored` is a first-class,
non-error outcome: a container start/stop event, a below-threshold CVE, or —
most importantly — a generic message whose image references are digests rather
than tags carries nothing a tag bump could be built from. Guessing there would
produce a confidently wrong pull request, so it does not.

Everything here is pure: no I/O, no settings, no DB. `tests/test_webhook_schemas.py`
covers it without touching HTTP.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

# Ordered low→high so a threshold comparison is a simple index lookup.
SEVERITY_ORDER: dict[str, int] = {
    "none": 0,
    "unknown": 0,
    "low": 1,
    "medium": 2,
    "moderate": 2,
    "high": 3,
    "critical": 4,
}

_UPDATE_KEYWORDS = ("update", "updated", "upgrade", "new version", "new image")
_VULN_KEYWORDS = ("vulnerab", "cve", "scan", "security")
# Events that are legitimate Dockhand traffic but say nothing about an image
# version — acknowledged, never proposed on.
_IGNORED_KEYWORDS = (
    "start",
    "stop",
    "restart",
    "exit",
    "die",
    "health",
    "deploy",
    "created",
    "removed",
    "prune",
)

_CVE_RE = re.compile(r"CVE-\d{4}-\d{4,7}", re.IGNORECASE)
# `key=value` tokens Dockhand's generic message uses, e.g. `image=`, `old_image=`.
_KV_RE = re.compile(r"\b(new_image|old_image|image|current_image|latest_image|fixed_image)=(\S+)")
# A bare `repo/name:tag` or `host:port/repo:tag` token, used only as a fallback.
_REF_RE = re.compile(r"\b((?:[A-Za-z0-9._-]+(?::\d+)?/)*[A-Za-z0-9._-]+:[A-Za-z0-9._-]+)\b")


def parse_image_ref(ref: str) -> tuple[str, str]:
    """Split an image reference into ``(repository, tag)``.

    The tag is only the part after the *last* colon, and only when that segment
    contains no ``/`` — otherwise the colon belongs to a registry port
    (``ghcr.io:5000/team/app``), not a tag. A digest reference
    (``repo@sha256:...``, or a bare ``sha256:...``) has no tag at all and yields
    an empty one, which is what keeps a digest-only Dockhand message from being
    mistaken for a version bump.
    """
    ref = (ref or "").strip()
    if not ref:
        return "", ""
    # A digest pins an exact image but names no version we could write into a
    # compose file, so treat the digest as "no tag".
    if "@" in ref:
        return ref.split("@", 1)[0], ""
    if ref.lower().startswith("sha256:"):
        return "", ""
    head, sep, tail = ref.rpartition(":")
    if not sep or "/" in tail:
        return ref, ""
    return head, tail


def _classify(text: str) -> AlertKind:
    """Bucket an event name or notification title into an alert kind."""
    lowered = (text or "").lower()
    # Vulnerability wins over update: "update blocked by vulnerability scan" is
    # a CVE finding, not a version bump.
    if any(k in lowered for k in _VULN_KEYWORDS):
        return AlertKind.vulnerability
    if any(k in lowered for k in _UPDATE_KEYWORDS):
        return AlertKind.image_update
    if any(k in lowered for k in _IGNORED_KEYWORDS):
        return AlertKind.ignored
    return AlertKind.ignored


def severity_meets(severity: str | None, minimum: str) -> bool:
    """True when `severity` is at least `minimum` on the CVE severity ladder.

    An unrecognized severity is treated as meeting the bar: a scanner label this
    map hasn't seen should surface for review rather than be silently dropped.
    """
    if not severity:
        return False
    got = SEVERITY_ORDER.get(severity.strip().lower())
    if got is None:
        return True
    floor = SEVERITY_ORDER.get(minimum.strip().lower(), SEVERITY_ORDER["high"])
    return got >= floor


class AlertKind(StrEnum):
    image_update = "image_update"
    vulnerability = "vulnerability"
    ignored = "ignored"


class NormalizedAlert(BaseModel):
    """The single shape the route acts on, whichever body Dockhand sent."""

    kind: AlertKind
    container: str = ""
    image: str = ""
    current_tag: str = ""
    new_tag: str = ""
    node: str | None = None
    severity: str | None = None
    cve_ids: list[str] = Field(default_factory=list)
    raw_event: str = ""
    # Why an alert was ignored — echoed back to Dockhand so an operator reading
    # the delivery log can tell "not actionable" from "not understood".
    reason: str = ""


class DockhandStructuredAlert(BaseModel):
    """Dockhand alert with explicit fields (custom payload template)."""

    model_config = ConfigDict(extra="ignore")

    event: str
    container: str
    current_image: str | None = None
    latest_image: str | None = None
    server: str | None = None
    severity: str | None = None
    cve_ids: list[str] = Field(default_factory=list)
    fixed_image: str | None = None

    def normalize(self, *, min_severity: str = "high") -> NormalizedAlert:
        kind = _classify(self.event)
        base = {
            "container": self.container,
            "node": self.server,
            "raw_event": self.event,
        }

        if kind is AlertKind.vulnerability:
            if not severity_meets(self.severity, min_severity):
                return NormalizedAlert(
                    kind=AlertKind.ignored,
                    reason=f"severity {self.severity or 'unset'!r} below {min_severity!r}",
                    severity=self.severity,
                    **base,
                )
            repo, current_tag = parse_image_ref(self.current_image or "")
            fixed_repo, fixed_tag = parse_image_ref(self.fixed_image or self.latest_image or "")
            return NormalizedAlert(
                kind=AlertKind.vulnerability,
                image=repo or fixed_repo,
                current_tag=current_tag,
                new_tag=fixed_tag,
                severity=self.severity,
                cve_ids=list(self.cve_ids),
                **base,
            )

        if kind is AlertKind.image_update:
            repo, current_tag = parse_image_ref(self.current_image or "")
            new_repo, new_tag = parse_image_ref(self.latest_image or "")
            if not new_tag:
                return NormalizedAlert(
                    kind=AlertKind.ignored,
                    reason="no target tag in latest_image (digest-only or missing)",
                    **base,
                )
            return NormalizedAlert(
                kind=AlertKind.image_update,
                image=new_repo or repo,
                current_tag=current_tag,
                new_tag=new_tag,
                **base,
            )

        return NormalizedAlert(
            kind=AlertKind.ignored, reason=f"event {self.event!r} is not actionable", **base
        )


class DockhandGenericAlert(BaseModel):
    """Dockhand's stock generic-webhook body: `{title, message, agent}`."""

    model_config = ConfigDict(extra="ignore")

    title: str
    message: str = ""
    agent: str | None = None

    def _container(self) -> str:
        """Pull the container name out of a title like `Container updated: c1`."""
        _, sep, tail = self.title.partition(":")
        if sep and tail.strip():
            return tail.strip().split()[0]
        # Fall back to the last word of the title ("c1 updated").
        words = self.title.split()
        return words[-1] if words else ""

    def normalize(self, *, min_severity: str = "high") -> NormalizedAlert:
        kind = _classify(f"{self.title} {self.message}")
        container = self._container()
        base = {"container": container, "raw_event": self.title}

        pairs = dict(_KV_RE.findall(self.message))
        new_ref = pairs.get("new_image") or pairs.get("image") or pairs.get("latest_image") or ""
        old_ref = pairs.get("old_image") or pairs.get("current_image") or ""
        if not new_ref and not old_ref:
            # No key=value tokens; fall back to any bare `name:tag` refs, oldest
            # first (Dockhand writes the new one first, so reverse on two hits).
            found = _REF_RE.findall(self.message)
            if len(found) >= 2:
                new_ref, old_ref = found[0], found[1]
            elif found:
                new_ref = found[0]

        new_repo, new_tag = parse_image_ref(new_ref)
        old_repo, old_tag = parse_image_ref(old_ref)

        if kind is AlertKind.vulnerability:
            severity = self._severity()
            if not severity_meets(severity, min_severity):
                return NormalizedAlert(
                    kind=AlertKind.ignored,
                    reason=f"severity {severity or 'unset'!r} below {min_severity!r}",
                    severity=severity,
                    **base,
                )
            return NormalizedAlert(
                kind=AlertKind.vulnerability,
                image=old_repo or new_repo,
                current_tag=old_tag,
                new_tag=new_tag,
                severity=severity,
                cve_ids=[c.upper() for c in _CVE_RE.findall(self.message)],
                **base,
            )

        if kind is AlertKind.image_update:
            if not container:
                return NormalizedAlert(
                    kind=AlertKind.ignored, reason="no container name in title", **base
                )
            if not new_tag:
                # The documented Dockhand body is digest-only
                # ("image=sha256:new old_image=sha256:old"). There is no version
                # to write into a compose file, so acknowledge and stop rather
                # than opening a wrong PR.
                return NormalizedAlert(
                    kind=AlertKind.ignored,
                    reason=(
                        "no image tag in message (digest-only payload); configure Dockhand "
                        "to send a structured alert to enable update proposals"
                    ),
                    **base,
                )
            return NormalizedAlert(
                kind=AlertKind.image_update,
                image=new_repo or old_repo,
                current_tag=old_tag,
                new_tag=new_tag,
                **base,
            )

        return NormalizedAlert(
            kind=AlertKind.ignored, reason=f"title {self.title!r} is not actionable", **base
        )

    def _severity(self) -> str | None:
        lowered = self.message.lower()
        # Highest mentioned severity wins — a scan listing both "critical" and
        # "low" findings is a critical alert.
        for name in ("critical", "high", "medium", "moderate", "low"):
            if name in lowered:
                return name
        return None
