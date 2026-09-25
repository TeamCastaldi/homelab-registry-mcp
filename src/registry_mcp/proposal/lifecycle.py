"""Retire proposals whose pull request has finished.

A proposal stays ``open`` until something moves it on, and ``find_open`` /
``find_open_by_path`` deduplicate against every open row. Without this, a
merged or closed PR would block every later proposal for the same service or
node forever. Shared by the proposal and normalization engines, which both
dedupe that way; it only records PR state and never opens, edits, or merges.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import TYPE_CHECKING

from registry_mcp.logging import get_logger
from registry_mcp.models import FindingType, Proposal, ProposalStatus
from registry_mcp.providers.git import GitError

if TYPE_CHECKING:
    from registry_mcp.proposal.store import ProposalStore
    from registry_mcp.providers.git import GitProvider

_log = get_logger("proposal.lifecycle")


def branch_suffix() -> str:
    """Today's date plus a short random tag, to end a proposal branch name.

    The date keeps branches readable; the tag keeps them unique. Once a PR is
    retired, the next proposal for the same finding or node can follow the
    same day, and a date-only name collided with the finished PR's branch,
    which Gitea (409) and GitHub (422) refuse to create again.
    """
    return f"{datetime.now().strftime('%Y-%m-%d')}-{secrets.token_hex(3)}"


async def retire_if_finished(
    proposal: Proposal,
    *,
    proposals: ProposalStore,
    git: GitProvider | None,
    repo: str | None,
) -> bool:
    """Move an open proposal to ``merged``/``cancelled`` once its PR is done.

    Returns True when the proposal was retired. A merged ``auth_mode_conflict``
    PR is deliberately left open: that finding is only resolved once discovery
    sees the conflict clear, which the verification sweep records as
    ``verified``. A PR whose state can't be read is left as-is.
    """
    if not proposal.pr_number or git is None or not repo:
        return False
    try:
        state = await git.get_pr_state(repo, proposal.pr_number)
    except GitError as exc:
        _log.warning("pr_state_read_failed", proposal_id=proposal.id, error=str(exc))
        return False
    if state == "closed":
        status = ProposalStatus.cancelled
    elif state == "merged" and proposal.finding_type != FindingType.auth_mode_conflict:
        status = ProposalStatus.merged
    else:
        return False
    proposals.set_status(proposal.id, status, resolved=True)
    _log.info(
        "proposal_retired",
        proposal_id=proposal.id,
        finding_type=proposal.finding_type.value,
        pr_number=proposal.pr_number,
        status=status.value,
    )
    return True
