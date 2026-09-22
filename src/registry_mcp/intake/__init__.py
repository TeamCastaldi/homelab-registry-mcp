"""Repo intake (conversational deploy, Phase 1 — ADR-018).

Reads a *foreign* source repository — one this homelab does not own and has
never deployed — and works out what it would take to run it. The closest
existing analog is `adoption/ssh.py`, which inspects a live container; this
inspects a repo instead, so nothing here touches the registry, the homelab
repo, or any running service.

Two halves, deliberately split the same way `normalization/` splits its
deterministic formatter from its DSPy escalation:

- `fetch.py` — validates the URL, shallow-clones into a temp dir, and reads
  back the handful of interesting files. All I/O, no interpretation.
- `parse.py` — deterministic extraction from those files (ports, env vars,
  volumes, base image). No LLM, same discipline that keeps `reconcile.py`
  detection-only.

What deterministic parsing genuinely cannot answer (does this need a
database? which env vars are required?) is left to the confidence-gated
`InferServiceRequirements` reasoning module, which fills nothing it isn't
confident about.
"""

from registry_mcp.intake.fetch import (
    IntakeError,
    RepoSnapshot,
    check_repo_url,
    collect_from_dir,
    fetch_repo,
)
from registry_mcp.intake.parse import (
    ServiceRequirements,
    parse_compose,
    parse_dockerfile,
    parse_snapshot,
)

__all__ = [
    "IntakeError",
    "RepoSnapshot",
    "ServiceRequirements",
    "check_repo_url",
    "collect_from_dir",
    "fetch_repo",
    "parse_compose",
    "parse_dockerfile",
    "parse_snapshot",
]
