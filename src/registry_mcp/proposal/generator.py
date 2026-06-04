"""Patch generation: calls the DSPy reasoning layer and enforces the gates.

There is no rule-based fallback. If the reasoning layer is unavailable, returns
low confidence, or produces a patch that is not valid YAML, the result is a
rejection — never a hand-written patch. This is the safety mechanism that makes
autonomous proposal generation responsible.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import yaml

from registry_mcp.logging import get_logger

if TYPE_CHECKING:
    from registry_mcp.dspy import Reasoner
    from registry_mcp.providers.git import GitProvider

_log = get_logger("proposal.generator")

# The Traefik dynamic middleware config. Fetched so the model can see which
# shared middlewares (e.g. authentik-auth@file) already exist before choosing
# between reusing one and adding a new per-service outpost sidecar.
_MIDDLEWARE_PATH = os.getenv(
    "TRAEFIK_MIDDLEWARE_PATH",
    "nodes/control-node/core/traefik/dynamic/middleware.yml",
)
# Deterministic backstop to the signature's "no real secrets" instruction: a
# value assigned to a secret-named key (TOKEN/KEY/SECRET/PASSWORD/PASS) that is
# long enough to look like a real credential is replaced with a placeholder.
_CREDENTIAL_RE = re.compile(
    r"((?:TOKEN|KEY|SECRET|PASSWORD|PASS)\s*[:=]\s*)([A-Za-z0-9_\-]{20,})",
    re.IGNORECASE,
)


def _scrub_credentials(patch: str) -> tuple[str, bool]:
    """Replace credential-shaped values with a placeholder.

    Returns the (possibly) scrubbed patch and whether any replacement happened.
    """
    scrubbed, count = _CREDENTIAL_RE.subn(r"\1<replace-with-credential>", patch)
    return scrubbed, count > 0


@dataclass
class PatchResult:
    """Outcome of a patch-generation attempt."""

    ok: bool
    confidence: float = 0.0
    rejection_reason: str | None = None
    patch: str = ""
    commit_message: str = ""
    pr_title: str = ""
    pr_body: str = ""
    reasoning: str = field(default="")


class PatchGenerator:
    """Wraps the DSPy ``GenerateRemediationPatch`` module with confidence and
    YAML-validity gates.

    When a Git provider is configured, the generator first fetches the Traefik
    dynamic middleware config so the model can see existing shared middlewares
    before deciding how to remediate. A failed fetch never blocks the proposal —
    it falls back to an empty string.
    """

    def __init__(
        self,
        reasoner: Reasoner,
        *,
        threshold: float = 0.8,
        git: GitProvider | None = None,
        repo: str | None = None,
        base: str = "main",
    ) -> None:
        self._reasoner = reasoner
        self._threshold = threshold
        self._git = git
        self._repo = repo
        self._base = base

    async def _fetch_existing_middlewares(self) -> str:
        """Best-effort read of middleware.yml; empty string on any failure."""
        if self._git is None or not self._repo:
            return ""
        try:
            return await self._git.read_file(self._repo, _MIDDLEWARE_PATH, self._base)
        except Exception as exc:  # never block a proposal on the context fetch
            _log.warning("middleware_fetch_failed", path=_MIDDLEWARE_PATH, error=str(exc))
            return ""

    async def generate(
        self,
        *,
        service: dict,
        finding_type: str,
        current_file: str,
        file_path: str,
        apply_mode: str,
    ) -> PatchResult:
        existing_middlewares = await self._fetch_existing_middlewares()
        raw = self._reasoner.generate_remediation_patch(
            service=service,
            finding_type=finding_type,
            current_file=current_file,
            file_path=file_path,
            apply_mode=apply_mode,
            existing_middlewares=existing_middlewares,
        )
        if raw is None:
            return PatchResult(
                ok=False,
                rejection_reason="reasoning layer unavailable (DSPY_ENABLED=false or call errored)",
            )

        # Deterministic secret scrub, before any gate runs: any credential the
        # model echoed despite the signature's instruction is replaced with a
        # placeholder so it can never reach a commit.
        patch = raw.get("patch", "") or ""
        patch, scrubbed = _scrub_credentials(patch)
        if scrubbed:
            _log.warning("patch_scrubbed_credentials", file_path=file_path)

        confidence = float(raw.get("confidence", 0.0))
        if confidence < self._threshold:
            reason = f"confidence {confidence:.2f} below threshold {self._threshold:.2f}"
            _log.info("patch_rejected", file_path=file_path, reason=reason)
            return PatchResult(ok=False, confidence=confidence, rejection_reason=reason)

        if not patch.strip():
            return PatchResult(
                ok=False, confidence=confidence, rejection_reason="generated patch is empty"
            )

        # Tab characters are illegal in YAML but common in compose file inline
        # comments (e.g. VALUE: ${VAR}\t# note). Normalise before the YAML gate.
        patch = patch.replace("\t", "  ")

        # A syntactically invalid file is never committed.
        try:
            yaml.safe_load(patch)
        except yaml.YAMLError as exc:
            reason = f"generated patch is not valid YAML: {exc}"
            _log.warning("patch_rejected", file_path=file_path, reason=reason)
            return PatchResult(ok=False, confidence=confidence, rejection_reason=reason)

        return PatchResult(
            ok=True,
            confidence=confidence,
            patch=patch,
            commit_message=raw.get("commit_message", "") or f"fix: remediate {finding_type}",
            pr_title=raw.get("pr_title", "") or f"Remediate {finding_type}",
            pr_body=raw.get("pr_body", "") or "",
            reasoning=raw.get("reasoning", "") or "",
        )
