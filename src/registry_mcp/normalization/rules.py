"""Compose file normal form: rule definitions, canonical key ordering, and the
equivalence guarantee that makes Tier 1 auto-fixes behavior-preserving.

Mirrors ``docs/specs/spec-compose-normal-form.md`` rule-for-rule — the rule IDs
here match the IDs in that document exactly. Pure functions only: no I/O, no
LLM calls — same "detection layer stays deterministic" discipline
``registry/reconcile.py`` follows for service discovery.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml

from registry_mcp.proposal.generator import _scrub_credentials

# Tier 2 finding rule IDs, in spec order.
R_LATEST_TAG = "R-001"
R_BUILD_KEY = "R-002"
R_NO_RESTART = "R-003"
R_UNFLAGGED_PORTS = "R-004"
R_HARDCODED_NETWORK = "R-005"
R_HARDCODED_SECRET = "R-006"
R_CONTAINER_NAME_MISMATCH = "R-007"

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

_PORTS_BLOCK_RE = re.compile(r"^(?P<indent>[ \t]*)ports:[ \t]*(#.*)?$", re.MULTILINE)


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


def _labels_as_mapping(labels: Any) -> dict[str, str]:
    """A compose ``labels:`` value (list *or* mapping form) reduced to the
    ``{key: str(value)}`` shape both forms are equivalent to at the Docker
    level."""
    if labels is None:
        return {}
    if isinstance(labels, dict):
        return {str(k): str(v) for k, v in labels.items()}
    result: dict[str, str] = {}
    for item in labels:
        key, _, value = str(item).partition("=")
        result[key] = value
    return result


def _ports_as_strings(ports: Any) -> list[str]:
    """A compose ``ports:`` value reduced to its string form regardless of
    whether an entry was written as a bare number or a quoted string."""
    if ports is None:
        return []
    return [str(p) for p in ports]


def _environment_as_mapping(env: Any) -> dict[str, str | None]:
    """A compose ``environment:`` value (list *or* mapping form) reduced to a
    ``{key: value}`` mapping."""
    if env is None:
        return {}
    if isinstance(env, dict):
        return {str(k): (None if v is None else str(v)) for k, v in env.items()}
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


def _ports_missing_temporary_comment(raw_text: str) -> bool:
    """True when a ``ports:`` block exists with no ``# temporary`` comment
    anywhere in it — a text-level heuristic since a parsed YAML structure
    doesn't retain which line a comment was attached to. SOP-001 requires
    this comment on any interim port mapping; false positives/negatives here
    only affect a reported finding, never an auto-applied change."""
    for match in _PORTS_BLOCK_RE.finditer(raw_text):
        indent = len(match.group("indent"))
        block_lines = [match.group(0)]
        for line in raw_text[match.end() :].splitlines():
            if not line.strip():
                continue
            line_indent = len(line) - len(line.lstrip(" \t"))
            if line.strip().startswith("-") and line_indent > indent:
                block_lines.append(line)
                continue
            break
        block_text = "\n".join(block_lines)
        if "temporary" not in block_text.lower():
            return True
    return False


def _proxy_network_is_hardcoded(doc: dict) -> bool:
    networks = doc.get("networks")
    if not isinstance(networks, dict):
        return False
    for name, network_config in networks.items():
        is_external = isinstance(network_config, dict) and network_config.get("external")
        if is_external and "${PROXY_NETWORK" not in str(name):
            return True
    return False


def check(doc: Any, *, raw_text: str, path: str) -> list[Finding]:
    """Tier 2 findings for a parsed compose document. Never mutates ``doc``."""
    findings: list[Finding] = []
    if not isinstance(doc, dict) or not isinstance(doc.get("services"), dict):
        return findings

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

    if _ports_missing_temporary_comment(raw_text):
        findings.append(
            Finding(R_UNFLAGGED_PORTS, path, None, "ports: mapping has no # temporary comment")
        )

    if _proxy_network_is_hardcoded(doc):
        findings.append(
            Finding(
                R_HARDCODED_NETWORK,
                path,
                None,
                "external network name is hardcoded, not ${PROXY_NETWORK:-proxy-net}",
            )
        )

    _, credential_hit = _scrub_credentials(raw_text)
    if credential_hit:
        findings.append(
            Finding(R_HARDCODED_SECRET, path, None, "value looks like a hardcoded credential")
        )

    return findings
