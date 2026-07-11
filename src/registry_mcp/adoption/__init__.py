"""Brownfield adoption (Phase 7): reverse-engineer a live, pre-existing Docker
service and bring it under GitOps management without leaking hardcoded
secrets.

Two-call flow, split across an inspection/drafting half and a
human-decision-gated finalize half — see `tools/adoption.py`:

1. `proposal_adopt_service` — SSH-inspects the live container, asks the DSPy
   reasoning layer (`proposal.adoption.AdoptionGenerator`) to sanitize the
   legacy compose file, and persists an `AdoptionDraft`. Nothing is written to
   Git yet.
2. `proposal_adopt_service_finalize` — given the operator's choice to keep or
   rotate each detected secret, writes and git-crypt-encrypts the `.env` in the
   local homelab clone, commits the sanitized compose via the Git provider,
   and opens the PR.
"""

from registry_mcp.adoption.store import AdoptionDraftStore

__all__ = ["AdoptionDraftStore"]
