"""Strict parsers for the text Dozzle's MCP tools return.

Dozzle documents no output contract: the log tools answer in prose plus NDJSON.
These parsers raise `DozzleError` on a shape they don't recognize rather than
guess, so a format change after a Dozzle upgrade shows up as an error and not as
silently empty or wrong logs.
"""

from __future__ import annotations

import json
import re
from typing import Any

from registry_mcp.integrations.dozzle.client import DozzleError

NO_LOGS = "(no logs in the specified time range)"
# Dozzle stops a log response at 1 MB; at or near this size the oldest entries were
# kept and the rest dropped without a word (only the search tool adds a note).
NEAR_UPSTREAM_LIMIT = 900_000

_SEARCH_HEADER = re.compile(r'^Found (\d+) matches for ".*" \(scanned (\d+) entries\):$')


def parse_json(text: str, what: str) -> Any:
    try:
        return json.loads(text)
    except ValueError as exc:
        raise DozzleError(f"Dozzle {what} was not JSON: {text[:120]!r}") from exc


def parse_log_text(text: str) -> dict[str, Any]:
    """Split a log response into entries, the search header's counts, and notes."""
    stripped = text.strip()
    if stripped == NO_LOGS:
        return {"entries": [], "matches": None, "scanned": 0, "notes": []}

    entries: list[dict[str, Any]] = []
    notes: list[str] = []
    matches: int | None = None
    scanned: int | None = None
    for line in stripped.splitlines():
        line = line.strip()
        if not line:
            continue
        header = _SEARCH_HEADER.match(line)
        if header:
            matches, scanned = int(header.group(1)), int(header.group(2))
        elif line.startswith("{"):
            try:
                entry = json.loads(line)
            except ValueError as exc:
                raise DozzleError(f"Dozzle log line was not JSON: {line[:120]!r}") from exc
            if not isinstance(entry, dict):
                raise DozzleError(f"Dozzle log line was not an object: {line[:120]!r}")
            entries.append(entry)
        else:
            notes.append(line)

    if not entries and matches is None:
        raise DozzleError(f"Dozzle log output was not recognized: {stripped[:120]!r}")
    if matches == 0 and not entries:
        return {"entries": [], "matches": 0, "scanned": scanned or 0, "notes": notes}
    return {
        "entries": entries,
        "matches": matches,
        "scanned": scanned if scanned is not None else len(entries),
        "notes": notes,
    }
