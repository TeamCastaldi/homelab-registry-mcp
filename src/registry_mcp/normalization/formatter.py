"""Deterministic Tier 1 normalization: applies the formatting rules in
``docs/specs/spec-compose-normal-form.md`` without an LLM call, then proves
the result is behavior-preserving before returning it.

Two stages:

1. A ``ruamel.yaml`` round-trip reshapes values: drops ``version:`` (N-004),
   turns a labels or environment list into a mapping (N-007, N-011), and
   quotes label values and short-syntax ports (N-008, N-010). A comment on a
   list entry moves to the mapping entry that replaces it.
2. Keys are then reordered as text (N-005, N-006, N-009). ruamel anchors a
   "comment above key X" to the *previous* key, so reordering the parsed
   mapping would move comments onto the wrong line. Working on the emitted
   text instead, each key moves as one block together with the comment lines
   directly above it, while blank lines stay where they were as separators.

Every reorder is checked on its own: the text must still mean the same thing
to Docker and keep every comment. One that fails is left undone and its rule
recorded in ``NormalizedFile.skipped_rules``; the caller
(``normalization/engine.py``) decides whether to escalate those to the DSPy
``NormalizeConfigFile`` module. ``normalize()`` returns ``None`` only when it
cannot produce *any* safe result: the source isn't a compose-shaped document,
or the value stage fails its own checks.
"""

from __future__ import annotations

import io
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.compat import StringIO
from ruamel.yaml.scalarstring import DoubleQuotedScalarString
from yamllint import linter
from yamllint.config import YamlLintConfig

from registry_mcp.logging import get_logger
from registry_mcp.normalization.rules import (
    SERVICE_KEY_ORDER,
    compose_string,
    is_equivalent,
    key_sort_key,
    top_level_sort_key,
)

_log = get_logger("normalization.formatter")

_thread_state = threading.local()


def _yaml() -> YAML:
    """This thread's round-trip ``YAML`` instance.

    ruamel keeps its parser and emitter state on the ``YAML`` object itself, so
    one instance shared across threads corrupts both: sweeps running at once
    (each file is formatted in a worker thread) failed with errors like
    "expected NodeEvent, but got DocumentStartEvent" and escalated every file
    to DSPy. Each thread gets its own.
    """
    yaml_ = getattr(_thread_state, "yaml", None)
    if yaml_ is None:
        yaml_ = YAML()
        yaml_.indent(mapping=2, sequence=4, offset=2)
        yaml_.preserve_quotes = True
        yaml_.width = 4096  # never line-wrap a long value (e.g. a Traefik rule label)
        _thread_state.yaml = yaml_
    return yaml_


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


# -- comments -------------------------------------------------------------


def _comments(text: str) -> Counter[str]:
    """Every comment in ``text``, as a multiset of stripped ``# ...`` strings.

    Used to prove a rewrite kept every comment (N-012): the equivalence
    check only compares values, and a comment isn't one. A ``#`` starts a
    comment at the start of a line or after whitespace, outside a quoted
    scalar. A quote only opens one where a scalar can start (``it's`` in a
    plain value is just an apostrophe).
    """
    found: Counter[str] = Counter()
    for line in text.splitlines():
        quote = None
        escaped = False
        prev = None  # last non-space character outside a quoted scalar
        for idx, char in enumerate(line):
            spaced = idx > 0 and line[idx - 1] in " \t"
            if quote == '"':
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quote = None
            elif quote == "'":
                if char == "'":
                    quote = None
            elif char in "\"'" and (prev is None or prev in "[{," or (prev in ":-?" and spaced)):
                quote = char
            elif char == "#" and (idx == 0 or spaced):
                found[line[idx:].strip()] += 1
                break
            if not char.isspace() and quote is None:
                prev = char
    return found


def _same_meaning(before: str, after: str) -> bool:
    """``after`` means the same to Docker as ``before`` and keeps every comment."""
    return is_equivalent(before, after) and _comments(before) == _comments(after)


# -- stage 1: values (ruamel round-trip) ------------------------------------


def _remove_version(doc: CommentedMap, skipped: list[str]) -> None:
    if "version" not in doc:
        return
    entry = doc.ca.items.get("version")
    if entry and any(part is not None for part in entry):
        skipped.append("N-004")
        return
    del doc["version"]


def _seq_to_map(seq: CommentedSeq, pairs: list[tuple[str, Any]]) -> CommentedMap | None:
    """A mapping of ``pairs`` that carries each list entry's comment over to
    the entry that replaces it. ``None`` when that can't be done faithfully:
    a repeated key, or a comment somewhere a mapping entry has no slot for."""
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        return None
    converted = CommentedMap(pairs)
    for index, key in enumerate(keys):
        entry = seq.ca.items.get(index)
        if entry is None:
            continue
        if any(part is not None for part in entry[1:]):
            return None
        if entry[0] is not None:
            # Same slot semantics: the comment after this entry, through the
            # comment lines before the next one.
            converted.ca.items[key] = [None, None, entry[0], None]
    return converted


def _apply_labels(service: CommentedMap, skipped: list[str]) -> None:
    labels = service.get("labels")
    if isinstance(labels, CommentedSeq):  # N-007
        pairs = [(key, value) for key, _, value in (str(item).partition("=") for item in labels)]
        converted = _seq_to_map(labels, pairs)
        if converted is None:
            skipped.append("N-007")
            return
        service["labels"] = labels = converted
    if not isinstance(labels, CommentedMap):
        return
    for key in list(labels.keys()):  # N-008: the string Compose sees, double-quoted
        labels[key] = DoubleQuotedScalarString(compose_string(labels[key]))


def _apply_ports(service: CommentedMap) -> None:
    """N-010. Always an in-place, same-index reassignment, so it never
    disturbs a per-item comment (e.g. SOP-001's `# temporary`). A long-syntax
    entry (a mapping) is left as it is."""
    ports = service.get("ports")
    if not isinstance(ports, CommentedSeq):
        return
    for i, value in enumerate(ports):
        if isinstance(value, (int, str)) and not isinstance(value, bool):
            ports[i] = DoubleQuotedScalarString(str(value))


def _env_value(value: str) -> Any:
    """An environment value taken from ``KEY=value`` list form, quoted when a
    YAML parser could read it as anything but that string (``yes``, ``1000``,
    ``null``, an empty value, ...)."""
    try:
        parsed = yaml.safe_load(value)
    except yaml.YAMLError:
        parsed = None
    return value if parsed == value else DoubleQuotedScalarString(value)


def _apply_environment(service: CommentedMap, skipped: list[str]) -> None:
    env = service.get("environment")
    if not isinstance(env, CommentedSeq):
        return
    pairs = []
    for item in env:
        key, sep, value = str(item).partition("=")
        pairs.append((key, _env_value(value) if sep else None))
    converted = _seq_to_map(env, pairs)
    if converted is None:
        skipped.append("N-011")
        return
    service["environment"] = converted


def _apply_values(doc: CommentedMap) -> list[str]:
    skipped: list[str] = []
    _remove_version(doc, skipped)  # N-004
    for service in doc["services"].values():
        if not isinstance(service, CommentedMap):
            continue
        _apply_labels(service, skipped)
        _apply_ports(service)
        _apply_environment(service, skipped)
    return skipped


def _dump(doc: CommentedMap) -> str:
    stream = StringIO()
    _yaml().dump(doc, stream)
    # N-003: exactly one trailing newline, no leading blank lines. (N-002,
    # tabs, can't survive parsing in the first place; N-013, no `---`
    # marker, is simply never emitted by this dumper.)
    return stream.getvalue().strip("\n") + "\n"


# -- stage 2: key order (text) ----------------------------------------------


def _is_blank(line: str) -> bool:
    return not line.strip()


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _mapping_at(doc: Any, path: tuple[Any, ...]) -> CommentedMap | None:
    node = doc
    for key in path:
        if not isinstance(node, CommentedMap) or key not in node:
            return None
        node = node[key]
    return node if isinstance(node, CommentedMap) else None


def reorder_mapping(text: str, path: tuple[Any, ...], sort_key: Callable[[Any], Any]) -> str | None:
    """Reorder the keys of the block mapping at ``path`` (``()`` for the
    document root) in ``text``, each key moving together with the comment
    lines directly above it.

    Blank lines above a key stay where they are as separators, so spacing
    doesn't travel with a key; comments do. At the document root, lines
    above the first key are a file header and stay put. Comments after the
    last key's value stay at the end. Returns ``text`` unchanged when the
    keys are already in order or the mapping uses a ``<<:`` merge key (its
    merged keys aren't written here, so their order isn't this mapping's to
    set), and ``None`` when the mapping can't be laid out as blocks (flow
    style, or keys that don't each start their own line).
    """
    mapping = _mapping_at(_yaml().load(text), path)
    if mapping is None or len(mapping) < 2 or getattr(mapping, "merge", None):
        return text
    keys = list(mapping.keys())
    order = sorted(range(len(keys)), key=lambda i: sort_key(keys[i]))
    if order == list(range(len(keys))):
        return text

    positions = [mapping.lc.key(key) for key in keys]
    rows = [row for row, _ in positions]
    columns = {column for _, column in positions}
    if len(columns) != 1 or any(
        later <= earlier for earlier, later in zip(rows, rows[1:], strict=False)
    ):
        return None
    indent = columns.pop()
    lines = text.splitlines(keepends=True)

    def loose(line: str) -> bool:
        # Between two keys: a blank line, or a comment no deeper than the keys.
        return _is_blank(line) or (_is_comment(line) and _indent(line) <= indent)

    end = len(lines)
    if path:  # a nested mapping ends at the first content line shallower than its keys
        for i in range(rows[-1] + 1, len(lines)):
            line = lines[i]
            if not _is_blank(line) and not _is_comment(line) and _indent(line) < indent:
                end = i
                break
    tail = end
    while tail - 1 > rows[-1] and loose(lines[tail - 1]):
        tail -= 1

    starts = []
    for i, row in enumerate(rows):
        start = row
        if i == 0:
            if path:
                while start > 0 and _is_comment(lines[start - 1]):
                    start -= 1
        else:
            while start - 1 > rows[i - 1] and loose(lines[start - 1]):
                start -= 1
        starts.append(start)

    blocks = []
    for i, row in enumerate(rows):
        lead = lines[starts[i] : row]
        split = 0
        while i > 0 and split < len(lead) and _is_blank(lead[split]):
            split += 1
        body_end = starts[i + 1] if i + 1 < len(rows) else tail
        blocks.append((lead[:split], lead[split:], lines[row:body_end]))

    out = lines[: starts[0]]
    for slot, index in enumerate(order):
        separator = blocks[slot][0]
        _, comment, body = blocks[index]
        out += separator + comment + body
    out += lines[tail:]
    if len(out) != len(lines):
        return None
    return "".join(out)


def _reorder_steps(text: str) -> list[tuple[tuple[Any, ...], Callable[[Any], Any], str]]:
    doc = _yaml().load(text)
    steps: list[tuple[tuple[Any, ...], Callable[[Any], Any], str]] = [
        ((), top_level_sort_key, "N-005")
    ]
    for name, service in doc["services"].items():
        if not isinstance(service, CommentedMap):
            continue
        steps.append((("services", name), key_sort_key(SERVICE_KEY_ORDER), "N-006"))
        if isinstance(service.get("labels"), CommentedMap):
            steps.append((("services", name, "labels"), key_sort_key(()), "N-009"))
    return steps


def _apply_order(text: str) -> tuple[str, list[str]]:
    """N-005, N-006, N-009, each reorder checked on its own."""
    skipped: list[str] = []
    for path, sort_key, rule_id in _reorder_steps(text):
        try:
            reordered = reorder_mapping(text, path, sort_key)
        except Exception as exc:  # ruamel raises its own error hierarchy
            _log.warning("formatter_reorder_failed", rule=rule_id, error=str(exc))
            reordered = None
        if reordered is None or not _same_meaning(text, reordered):
            skipped.append(rule_id)
            continue
        text = reordered
    return text, skipped


def normalize(text: str) -> NormalizedFile | None:
    """Apply every safely-applicable Tier 1 rule to ``text``.

    Returns ``None`` when no safe result can be produced at all (not a
    compose-shaped document, or the value stage fails its checks) — the
    caller should escalate to DSPy in that case. A non-``None`` result has
    already passed the equivalence guarantee, kept every comment, and passed
    a yamllint check of its own output; any Tier 1 rule that couldn't be
    applied safely is listed in ``skipped_rules`` rather than silently
    dropped.
    """
    try:
        doc = _yaml().load(text)
    except Exception as exc:  # ruamel raises its own error hierarchy
        _log.warning("formatter_parse_failed", error=str(exc))
        return None

    if not isinstance(doc, CommentedMap) or not isinstance(doc.get("services"), CommentedMap):
        return None

    try:
        skipped = _apply_values(doc)
        reshaped = _dump(doc)
    except Exception as exc:  # never let a formatting bug break the sweep
        _log.warning("formatter_apply_failed", error=str(exc))
        return None

    if not _same_meaning(text, reshaped):
        _log.warning("formatter_equivalence_failed")
        return None

    try:
        normalized_text, order_skipped = _apply_order(reshaped)
    except Exception as exc:  # never let a formatting bug break the sweep
        _log.warning("formatter_apply_failed", error=str(exc))
        return None
    skipped += order_skipped

    errors = [
        p for p in linter.run(io.StringIO(normalized_text), _YAMLLINT_CONFIG) if p.level == "error"
    ]
    if errors:
        _log.warning("formatter_yamllint_failed", problems=[str(p) for p in errors])
        return None

    return NormalizedFile(
        content=normalized_text, changed=normalized_text != text, skipped_rules=skipped
    )
