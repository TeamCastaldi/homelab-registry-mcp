"""Compose file normal form: rule definitions, canonical key ordering, and the
equivalence guarantee that makes Tier 1 auto-fixes behavior-preserving.

Mirrors ``docs/specs/spec-compose-normal-form.md`` rule-for-rule — the rule IDs
here match the IDs in that document exactly. Pure functions only: no I/O, no
LLM calls — same "detection layer stays deterministic" discipline
``registry/reconcile.py`` follows for service discovery.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import yaml

from registry_mcp.proposal.generator import _scrub_credentials

# Tier 2 finding rule IDs, in spec order.
R_LATEST_TAG = "R-001"
R_BUILD_KEY = "R-002"
R_NO_RESTART = "R-003"
R_UNEXPLAINED_PORTS = "R-004"
R_SHARED_NETWORK_NOT_EXTERNAL = "R-005"
R_HARDCODED_SECRET = "R-006"
R_CONTAINER_NAME_MISMATCH = "R-007"
R_NOT_COMPOSE = "R-008"

# Networks that more than one stack joins. Each stack must declare them
# `external: true` so a stray `up` can never create or redefine them.
DEFAULT_SHARED_NETWORKS = ("swarm-net", "proxy-net")


def network_names(setting: str) -> tuple[str, ...]:
    """``NORMALIZATION_SHARED_NETWORKS``, comma-separated, as a tuple."""
    return tuple(name.strip() for name in setting.split(",") if name.strip())


# Tier 1 canonical key orders (N-005, N-006).
TOP_LEVEL_KEY_ORDER = ("services", "volumes", "networks", "configs", "secrets")
SERVICE_KEY_ORDER = (
    "image",
    "container_name",
    "restart",
    "depends_on",
    "env_file",
    "environment",
    "command",
    "entrypoint",
    "ports",
    "volumes",
    "networks",
    "labels",
    "healthcheck",
    "deploy",
)

_PORTS_LINE_RE = re.compile(r"^(?P<indent>[ \t]*)ports:[ \t]*(?P<comment>#.*)?$")
# `${VAR}`, `${VAR:-default}` or `${VAR-default}` as a whole value.
_INTERPOLATION_RE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*(?::?-(?P<default>[^}]*))?\}$")


@dataclass(frozen=True)
class Finding:
    """One Tier 2 issue: reported by the scan, never auto-fixed."""

    rule_id: str
    path: str
    service: str | None
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "path": self.path,
            "service": self.service,
            "detail": self.detail,
        }


def key_sort_key(order: tuple[str, ...]):
    """Sort key placing members of ``order`` first in that sequence; any other
    key follows, alphabetically."""

    def _key(name: str) -> tuple[int, str]:
        try:
            return (order.index(name), "")
        except ValueError:
            return (len(order), str(name))

    return _key


def top_level_sort_key(name: Any) -> tuple[int, int, str]:
    """N-005: ``name`` first, then ``x-`` extension fields in their existing
    order (they usually define the YAML anchors services refer to, and an
    alias must come after its anchor), then ``TOP_LEVEL_KEY_ORDER``, then any
    remaining key alphabetically."""
    name = str(name)
    if name == "name":
        return (0, 0, "")
    if name.startswith("x-"):
        return (1, 0, "")
    if name in TOP_LEVEL_KEY_ORDER:
        return (2, TOP_LEVEL_KEY_ORDER.index(name), "")
    return (3, 0, name)


def compose_string(value: Any) -> str:
    """The string Compose makes of a label or environment value: YAML
    booleans become ``true``/``false`` (not Python's ``True``), null becomes
    empty, anything else its ``str()``."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def _labels_as_mapping(labels: Any) -> dict[str, str]:
    """A compose ``labels:`` value (list *or* mapping form) reduced to the
    ``{key: string}`` shape both forms are equivalent to at the Docker
    level."""
    if labels is None:
        return {}
    if isinstance(labels, dict):
        return {str(k): compose_string(v) for k, v in labels.items()}
    result: dict[str, str] = {}
    for item in labels:
        key, _, value = str(item).partition("=")
        result[key] = value
    return result


def _ports_as_strings(ports: Any) -> list[Any]:
    """A compose ``ports:`` value with each short-syntax entry reduced to its
    string form, whether it was written as a bare number or a quoted string.
    A long-syntax entry (a mapping) is compared as it is."""
    if ports is None:
        return []
    return [p if isinstance(p, dict) else str(p) for p in ports]


def _environment_as_mapping(env: Any) -> dict[str, str | None]:
    """A compose ``environment:`` value (list *or* mapping form) reduced to a
    ``{key: value}`` mapping."""
    if env is None:
        return {}
    if isinstance(env, dict):
        return {str(k): (None if v is None else compose_string(v)) for k, v in env.items()}
    result: dict[str, str | None] = {}
    for item in env:
        key, sep, value = str(item).partition("=")
        result[key] = value if sep else None
    return result


def _canonical_service(service: dict) -> dict:
    canon = dict(service)
    if "labels" in canon:
        canon["labels"] = _labels_as_mapping(canon["labels"])
    if "ports" in canon:
        canon["ports"] = _ports_as_strings(canon["ports"])
    if "environment" in canon:
        canon["environment"] = _environment_as_mapping(canon["environment"])
    return canon


def canonical_projection(doc: Any) -> Any:
    """Project a parsed compose document to the representation-independent
    form the equivalence guarantee compares.

    Two documents that mean the same thing to Docker — regardless of whether
    ``labels``/``ports``/``environment`` were written in list or mapping
    form — project to equal results. Anything that isn't a compose document
    (no top-level ``services:`` mapping) is returned unchanged, so this stays
    safe to call on arbitrary YAML the scanner encounters.
    """
    if not isinstance(doc, dict) or not isinstance(doc.get("services"), dict):
        return doc
    projected = dict(doc)
    # N-004: a top-level `version:` key is obsolete in the Compose Spec and
    # ignored by the Compose CLI — dropping it is behavior-preserving, so it
    # must not register as an equivalence-gate difference.
    projected.pop("version", None)
    projected["services"] = {
        name: (_canonical_service(svc) if isinstance(svc, dict) else svc)
        for name, svc in doc["services"].items()
    }
    return projected


def is_equivalent(before: str, after: str) -> bool:
    """The equivalence guarantee: ``after`` may only differ from ``before`` in
    representation, never in what Docker does with it. Shared by the
    deterministic formatter and the DSPy escalation path — a normalization
    rewrite is committed only when this holds, regardless of which produced
    it."""
    try:
        before_doc = yaml.safe_load(before)
        after_doc = yaml.safe_load(after)
    except yaml.YAMLError:
        return False
    return canonical_projection(before_doc) == canonical_projection(after_doc)


def _has_pinned_tag(image: str) -> bool:
    if ":" not in image:
        return False
    tag = image.rsplit(":", 1)[-1]
    if "/" in tag:
        # A colon before the last "/" is a registry host:port, not a tag —
        # e.g. registry.lan:5000/name has no tag at all.
        return False
    return tag != "latest"


def _ports_without_reason(raw_text: str) -> bool:
    """True when a ``ports:`` block carries no comment at all: none on the
    ``ports:`` line, on the line directly above it, or among its entries. A
    published port should say why it's there (``# temporary`` until Traefik
    routes it, or why it stays). A text-level heuristic, since a parsed YAML
    structure doesn't keep comments; a miss only affects a reported finding,
    never an auto-applied change."""
    lines = raw_text.splitlines()
    for idx, line in enumerate(lines):
        match = _PORTS_LINE_RE.match(line)
        if match is None:
            continue
        indent = len(match.group("indent"))
        explained = match.group("comment") is not None or (
            idx > 0 and lines[idx - 1].lstrip().startswith("#")
        )
        for following in lines[idx + 1 :]:
            if not following.strip():
                continue
            depth = len(following) - len(following.lstrip(" \t"))
            # Deeper lines are entries (or their comments); an entry may also
            # sit at the key's own indent when the list isn't indented.
            if depth < indent or (depth == indent and not following.lstrip().startswith("-")):
                break
            if "#" in following:
                explained = True
        if not explained:
            return True
    return False


def _network_name(key: Any, config: Any) -> str | None:
    """The name Docker gives a top-level network: its ``name:``, else its key.
    An interpolated name resolves to its default, or ``None`` when it has
    none (the name isn't known until deploy time)."""
    name = str(config.get("name", key) if isinstance(config, dict) else key)
    match = _INTERPOLATION_RE.match(name)
    if match:
        return match.group("default")
    return name


def _shared_networks_not_external(doc: dict, shared: Iterable[str]) -> list[str]:
    """Top-level network keys naming a shared network without ``external: true``."""
    networks = doc.get("networks")
    if not isinstance(networks, dict):
        return []
    shared = set(shared)
    return [
        str(key)
        for key, config in networks.items()
        if _network_name(key, config) in shared
        and not (isinstance(config, dict) and config.get("external"))
    ]


def check(
    doc: Any,
    *,
    raw_text: str,
    path: str,
    shared_networks: Iterable[str] = DEFAULT_SHARED_NETWORKS,
) -> list[Finding]:
    """Tier 2 findings for a parsed compose document. Never mutates ``doc``.

    A document with no top-level ``services:`` mapping isn't a compose file;
    it gets the single R-008 finding and nothing else.
    """
    findings: list[Finding] = []
    if not isinstance(doc, dict) or not isinstance(doc.get("services"), dict):
        return [not_compose(path, "no top-level services: mapping")]

    for name, service in doc["services"].items():
        if not isinstance(service, dict):
            continue

        image = service.get("image")
        if image is None or not _has_pinned_tag(str(image)):
            findings.append(
                Finding(R_LATEST_TAG, path, name, f"image {image!r} has no pinned version tag")
            )

        if "build" in service:
            findings.append(Finding(R_BUILD_KEY, path, name, "service defines a build: key"))

        if "restart" not in service:
            findings.append(Finding(R_NO_RESTART, path, name, "service has no restart: policy"))

        container_name = service.get("container_name")
        if container_name is not None and str(container_name) != name:
            findings.append(
                Finding(
                    R_CONTAINER_NAME_MISMATCH,
                    path,
                    name,
                    f"container_name {container_name!r} differs from service key {name!r}",
                )
            )

    if _ports_without_reason(raw_text):
        findings.append(
            Finding(
                R_UNEXPLAINED_PORTS,
                path,
                None,
                "ports: mapping has no comment saying why it's published",
            )
        )

    for network in _shared_networks_not_external(doc, shared_networks):
        findings.append(
            Finding(
                R_SHARED_NETWORK_NOT_EXTERNAL,
                path,
                None,
                f"shared network {network!r} is not declared external: true",
            )
        )

    _, credential_hit = _scrub_credentials(raw_text)
    if credential_hit:
        findings.append(
            Finding(R_HARDCODED_SECRET, path, None, "value looks like a hardcoded credential")
        )

    return findings


def not_compose(path: str, why: str) -> Finding:
    """R-008: the file sits where a compose file belongs but isn't one."""
    return Finding(R_NOT_COMPOSE, path, None, f"not a compose file: {why}")
