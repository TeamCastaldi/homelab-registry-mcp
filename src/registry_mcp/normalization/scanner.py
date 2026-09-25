"""Repo-wide scan for compose files: lists every file in the repo, keeps the
ones the canonical form applies to, and checks each against the Tier 2 rules
in ``docs/specs/spec-compose-normal-form.md``.

Read-only — never writes to Git. Grouping by node is what lets the engine
batch one PR per node instead of one per file or one for the whole sweep.
"""

from __future__ import annotations

import asyncio
import fnmatch
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import yaml

from registry_mcp.logging import get_logger
from registry_mcp.normalization.rules import DEFAULT_SHARED_NETWORKS, Finding, check, not_compose

if TYPE_CHECKING:
    from registry_mcp.providers.git import GitProvider

_log = get_logger("normalization.scanner")

# N-100 candidates: any of these sitting where a compose.yaml should be.
MISNAMED_FILENAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml")

# Files read from the Git provider at once: one API call each.
_READ_CONCURRENCY = 8


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
    git: GitProvider,
    repo: str,
    ref: str,
    *,
    path_glob: str,
    node: str | None = None,
    shared_networks: Iterable[str] = DEFAULT_SHARED_NETWORKS,
) -> dict[str, list[FileReport]]:
    """Scan ``repo`` at ``ref`` for compose files, returning a
    ``{node: [FileReport, ...]}`` mapping.

    A file matches when its path fits ``path_glob`` (the canonical
    ``nodes/*/*/compose.yaml`` shape) or its filename is one of the N-100
    misnamed variants sitting where a ``compose.yaml`` would go. ``node``
    limits the scan to that node's files, before any is read. Files are read
    a few at a time in parallel. One that can't be read is logged and
    skipped rather than failing the whole scan; one that isn't a compose
    file (bad YAML, or no ``services:``) is reported as R-008.
    """
    all_paths = await git.list_files(repo, ref)
    matches: list[tuple[str, str, str, bool]] = []
    for path in all_paths:
        located = _node_and_stack(path)
        if located is None or (node is not None and located[0] != node):
            continue
        filename = path.rsplit("/", 1)[-1]
        is_misnamed = filename in MISNAMED_FILENAMES
        if fnmatch.fnmatch(path, path_glob) or is_misnamed:
            matches.append((path, *located, is_misnamed))

    limit = asyncio.Semaphore(_READ_CONCURRENCY)

    async def read(path: str) -> str | None:
        async with limit:
            try:
                return await git.read_file(repo, path, ref)
            except Exception as exc:  # never let one unreadable file break the sweep
                _log.warning("scan_read_failed", path=path, error=str(exc))
                return None

    contents = await asyncio.gather(*(read(path) for path, *_ in matches))

    reports: dict[str, list[FileReport]] = {}
    for (path, file_node, stack, is_misnamed), content in zip(matches, contents, strict=True):
        if content is None:
            continue
        findings: list[Finding]
        try:
            doc = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            where = f" (line {mark.line + 1})" if mark is not None else ""
            _log.warning("scan_parse_failed", path=path, error=str(exc))
            findings = [not_compose(path, f"doesn't parse as YAML{where}")]
        else:
            findings = check(doc, raw_text=content, path=path, shared_networks=shared_networks)

        reports.setdefault(file_node, []).append(
            FileReport(
                path=path,
                node=file_node,
                stack=stack,
                content=content,
                findings=findings,
                misnamed=is_misnamed,
            )
        )
    return reports
