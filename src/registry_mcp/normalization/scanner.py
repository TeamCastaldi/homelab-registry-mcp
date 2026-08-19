"""Repo-wide scan for compose files: lists every file in the repo, keeps the
ones the canonical form applies to, and checks each against the Tier 2 rules
in ``docs/specs/spec-compose-normal-form.md``.

Read-only — never writes to Git. Grouping by node is what lets the engine
batch one PR per node instead of one per file or one for the whole sweep.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import yaml

from registry_mcp.logging import get_logger
from registry_mcp.normalization.rules import Finding, check

if TYPE_CHECKING:
    from registry_mcp.providers.git import GitProvider

_log = get_logger("normalization.scanner")

# N-100 candidates: any of these sitting where a compose.yaml should be.
MISNAMED_FILENAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml")


@dataclass
class FileReport:
    """One scanned file, grouped under its node by ``scan()``."""

    path: str
    node: str
    stack: str
    content: str
    findings: list[Finding] = field(default_factory=list)
    misnamed: bool = False  # N-100: should be renamed to compose.yaml


def _node_and_stack(path: str) -> tuple[str, str] | None:
    """``nodes/{node}/{stack}/<file>`` -> ``(node, stack)``. ``None`` for
    anything else — including deeper paths (e.g. a Traefik dynamic config
    under ``nodes/{node}/{stack}/dynamic/...``), which the spec places out
    of scope for v1 (see docs/specs/spec-compose-normal-form.md)."""
    parts = path.split("/")
    if len(parts) != 4 or parts[0] != "nodes":
        return None
    return parts[1], parts[2]


async def scan(
    git: GitProvider, repo: str, ref: str, *, path_glob: str
) -> dict[str, list[FileReport]]:
    """Scan ``repo`` at ``ref`` for compose files, returning a
    ``{node: [FileReport, ...]}`` mapping.

    A file matches when its path fits ``path_glob`` (the canonical
    ``nodes/*/*/compose.yaml`` shape) or its filename is one of the N-100
    misnamed variants sitting where a ``compose.yaml`` would go. Any file
    this pass can't read or parse is logged and skipped rather than failing
    the whole scan.
    """
    all_paths = await git.list_files(repo, ref)
    reports: dict[str, list[FileReport]] = {}

    for path in all_paths:
        located = _node_and_stack(path)
        if located is None:
            continue
        node, stack = located

        filename = path.rsplit("/", 1)[-1]
        is_canonical_name = fnmatch.fnmatch(path, path_glob)
        is_misnamed = filename in MISNAMED_FILENAMES
        if not is_canonical_name and not is_misnamed:
            continue

        try:
            content = await git.read_file(repo, path, ref)
        except Exception as exc:  # never let one unreadable file break the sweep
            _log.warning("scan_read_failed", path=path, error=str(exc))
            continue

        findings: list[Finding] = []
        try:
            doc = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            _log.warning("scan_parse_failed", path=path, error=str(exc))
        else:
            findings = check(doc, raw_text=content, path=path)

        report = FileReport(
            path=path,
            node=node,
            stack=stack,
            content=content,
            findings=findings,
            misnamed=is_misnamed,
        )
        reports.setdefault(node, []).append(report)

    return reports
