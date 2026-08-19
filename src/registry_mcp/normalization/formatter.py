"""Deterministic Tier 1 normalization: a ``ruamel.yaml`` round-trip that
applies the formatting rules in ``docs/specs/spec-compose-normal-form.md``
without an LLM call, then proves the result is behavior-preserving before
returning it.

This is the fast, free, fully-repeatable path — it handles the common case
(no comments in the way of a reorder) entirely deterministically. A comment
that would be relocated by a key reorder or a list-to-mapping conversion
makes that specific sub-rule unsafe to auto-apply; the rule is recorded in
``NormalizedFile.skipped_rules`` instead of risking data loss, and the caller
(``normalization/generator.py``) decides whether to escalate those leftover
rules to the DSPy ``NormalizeConfigFile`` module. ``normalize()`` returns
``None`` only when it cannot produce *any* safe result at all — the source
isn't a compose-shaped document, or parsing/emitting fails outright.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.compat import StringIO
from yamllint import linter
from yamllint.config import YamlLintConfig

from registry_mcp.logging import get_logger
from registry_mcp.normalization.rules import (
    SERVICE_KEY_ORDER,
    TOP_LEVEL_KEY_ORDER,
    is_equivalent,
    key_sort_key,
)

_log = get_logger("normalization.formatter")

_yaml = YAML()
_yaml.indent(mapping=2, sequence=4, offset=2)
_yaml.preserve_quotes = True
_yaml.width = 4096  # never line-wrap a long value (e.g. a Traefik rule label)

# document-start disabled to match N-013 (no `---` marker); line-length and
# truthy left permissive since neither is a rule this spec defines.
_YAMLLINT_CONFIG = YamlLintConfig(
    """
extends: default
rules:
  document-start: disable
  line-length: disable
  truthy: disable
  comments-indentation: disable
"""
)


@dataclass
class NormalizedFile:
    """Outcome of a successful deterministic normalization pass."""

    content: str
    changed: bool
    skipped_rules: list[str] = field(default_factory=list)


def _has_comments(ca_items: dict, ca_comment) -> bool:
    if ca_comment:
        return True
    return any(part is not None for entry in ca_items.values() for part in entry)


def _mapping_has_comments(mapping: CommentedMap) -> bool:
    return _has_comments(mapping.ca.items, mapping.ca.comment)


def _seq_has_comments(seq: CommentedSeq) -> bool:
    return _has_comments(seq.ca.items, seq.ca.comment)


def _reorder(
    mapping: CommentedMap, order: tuple[str, ...], rule_id: str, skipped: list[str]
) -> None:
    """Reorder ``mapping``'s keys to match ``order``, unless any key carries
    an attached comment — reordering would relocate it to the wrong key
    (ruamel anchors a "comment before key X" to the *previous* key, not to
    X, so moving keys can silently misplace it)."""
    if _mapping_has_comments(mapping):
        skipped.append(rule_id)
        return
    for key in sorted(mapping.keys(), key=key_sort_key(order)):
        mapping.move_to_end(key)


def _remove_version(doc: CommentedMap, skipped: list[str]) -> None:
    if "version" not in doc:
        return
    entry = doc.ca.items.get("version")
    if entry and any(part is not None for part in entry):
        skipped.append("N-004")
        return
    del doc["version"]


def _apply_labels(service: CommentedMap, skipped: list[str]) -> None:
    labels = service.get("labels")
    if labels is None:
        return
    if isinstance(labels, CommentedSeq):
        if _seq_has_comments(labels):
            skipped.append("N-007")
            return
        converted = CommentedMap()
        for item in labels:
            key, _, value = str(item).partition("=")
            converted[key] = str(value)
        service["labels"] = converted
        labels = converted
    if not isinstance(labels, CommentedMap):
        return
    for key in list(labels.keys()):  # N-008: quote every value as a string
        labels[key] = str(labels[key])
    _reorder(labels, (), "N-009", skipped)  # empty order => pure alphabetical


def _apply_ports(service: CommentedMap) -> None:
    """N-010. Always an in-place, same-index reassignment, so it never
    disturbs a per-item comment (e.g. SOP-001's `# temporary`)."""
    ports = service.get("ports")
    if not isinstance(ports, CommentedSeq):
        return
    for i, value in enumerate(ports):
        ports[i] = str(value)


def _apply_environment(service: CommentedMap, skipped: list[str]) -> None:
    env = service.get("environment")
    if not isinstance(env, CommentedSeq):
        return
    if _seq_has_comments(env):
        skipped.append("N-011")
        return
    converted = CommentedMap()
    for item in env:
        key, sep, value = str(item).partition("=")
        converted[key] = value if sep else None
    service["environment"] = converted


def _apply_tier1(doc: CommentedMap) -> list[str]:
    skipped: list[str] = []
    _remove_version(doc, skipped)  # N-004
    _reorder(doc, TOP_LEVEL_KEY_ORDER, "N-005", skipped)

    services = doc.get("services")
    if not isinstance(services, CommentedMap):
        return skipped

    for service in services.values():
        if not isinstance(service, CommentedMap):
            continue
        _apply_labels(service, skipped)
        _apply_ports(service)
        _apply_environment(service, skipped)
        _reorder(service, SERVICE_KEY_ORDER, "N-006", skipped)

    return skipped


def _dump(doc: CommentedMap) -> str:
    stream = StringIO()
    _yaml.dump(doc, stream)
    # N-003: exactly one trailing newline, no leading blank lines. (N-002,
    # tabs, can't survive parsing in the first place; N-013, no `---`
    # marker, is simply never emitted by this dumper.)
    return stream.getvalue().strip("\n") + "\n"


def normalize(text: str) -> NormalizedFile | None:
    """Apply every safely-applicable Tier 1 rule to ``text``.

    Returns ``None`` when no safe result can be produced at all (not a
    compose-shaped document, or the round-trip fails) — the caller should
    escalate to DSPy in that case. A non-``None`` result has already passed
    the equivalence guarantee and a yamllint check of its own output; any
    Tier 1 rule that was unsafe to apply (a comment in the way) is listed in
    ``skipped_rules`` rather than silently dropped.
    """
    try:
        doc = _yaml.load(text)
    except Exception as exc:  # ruamel raises its own error hierarchy
        _log.warning("formatter_parse_failed", error=str(exc))
        return None

    if not isinstance(doc, CommentedMap) or not isinstance(doc.get("services"), CommentedMap):
        return None

    try:
        skipped = _apply_tier1(doc)
        normalized_text = _dump(doc)
    except Exception as exc:  # never let a formatting bug break the sweep
        _log.warning("formatter_apply_failed", error=str(exc))
        return None

    if not is_equivalent(text, normalized_text):
        _log.warning("formatter_equivalence_failed")
        return None

    errors = [
        p for p in linter.run(io.StringIO(normalized_text), _YAMLLINT_CONFIG) if p.level == "error"
    ]
    if errors:
        _log.warning("formatter_yamllint_failed", problems=[str(p) for p in errors])
        return None

    return NormalizedFile(
        content=normalized_text, changed=normalized_text != text, skipped_rules=skipped
    )
