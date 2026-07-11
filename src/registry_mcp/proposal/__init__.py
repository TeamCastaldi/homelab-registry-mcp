"""Proposal layer (Phase 8): DSPy-backed patch generation, Git operations,
notifications, and the proposal lifecycle.

This layer opens pull requests. It never merges them and never writes to the
filesystem Traefik/Docker watch — the PR is the safety gate. All patch
generation goes through DSPy (see ``generator.py``); there is no rule-based
fallback.
"""

from registry_mcp.proposal.adoption import AdoptionGenerator, AdoptionResult
from registry_mcp.proposal.engine import ProposalEngine
from registry_mcp.proposal.generator import PatchGenerator, PatchResult
from registry_mcp.proposal.store import ProposalStore

__all__ = [
    "AdoptionGenerator",
    "AdoptionResult",
    "ProposalEngine",
    "PatchGenerator",
    "PatchResult",
    "ProposalStore",
]
