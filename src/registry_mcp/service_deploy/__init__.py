"""Conversational service deployment (docs/plans/conversational-deploy.md).

Phase 2 (ADR-019) lives here: turning repo-intake requirements into a
brand-new compose file. Shaped like `proposal/` — a generator that calls the
reasoning layer and owns every gate — but kept a separate package, the same
way `normalization/` is, so a deploy draft never shares a code path (or a PR
label, once Phase 5 opens PRs) with a security remediation.

Intake itself (Phase 1) stays in `intake/`; later phases (placement, secrets
block, PR assembly) land here alongside the generator.
"""

from registry_mcp.service_deploy.generator import ComposeDraft, ComposeGenerator

__all__ = ["ComposeDraft", "ComposeGenerator"]
