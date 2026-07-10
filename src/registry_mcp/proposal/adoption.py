"""Adoption patch generation (Phase 7): calls the DSPy reasoning layer to
sanitize a legacy, hand-written compose file for brownfield adoption and
enforces the same confidence/YAML gates as the Phase 8 remediation generator.

There is no rule-based fallback. If the reasoning layer is unavailable,
returns low confidence, or produces a result that is not valid YAML, the
result is a rejection — the operator is told to review manually, never handed
a partially-sanitized file with real secrets still hardcoded in it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import yaml

from registry_mcp.logging import get_logger
from registry_mcp.proposal.generator import _scrub_credentials

if TYPE_CHECKING:
    from registry_mcp.dspy import Reasoner

_log = get_logger("proposal.adoption")


@dataclass
class AdoptionResult:
    """Outcome of a compose-sanitization attempt."""

    ok: bool
    confidence: float = 0.0
    rejection_reason: str | None = None
    sanitized_compose: str = ""
    detected_secret_keys: list[str] = field(default_factory=list)
    reasoning: str = field(default="")


class AdoptionGenerator:
    """Wraps the DSPy ``DetectHardcodedSecrets`` module with confidence and
    YAML-validity gates — the same discipline ``PatchGenerator`` applies to
    remediation patches."""

    def __init__(self, reasoner: Reasoner, *, threshold: float = 0.8) -> None:
        self._reasoner = reasoner
        self._threshold = threshold

    def generate(
        self, *, compose_content: str, container_env: dict, container_labels: dict
    ) -> AdoptionResult:
        raw = self._reasoner.detect_hardcoded_secrets(
            compose_content=compose_content,
            container_env=container_env,
            container_labels=container_labels,
        )
        if raw is None:
            return AdoptionResult(
                ok=False,
                rejection_reason="reasoning layer unavailable (DSPY_ENABLED=false or call errored)",
            )

        # Deterministic secret scrub, before any gate runs: any credential-shaped
        # value the model echoed despite the signature's instruction is replaced
        # with a placeholder so it can never reach a commit.
        sanitized = raw.get("sanitized_compose", "") or ""
        sanitized, scrubbed = _scrub_credentials(sanitized)
        if scrubbed:
            _log.warning("adoption_scrubbed_residual_credentials")

        confidence = float(raw.get("confidence", 0.0))
        if confidence < self._threshold:
            reason = f"confidence {confidence:.2f} below threshold {self._threshold:.2f}"
            _log.info("adoption_rejected", reason=reason)
            return AdoptionResult(ok=False, confidence=confidence, rejection_reason=reason)

        if not sanitized.strip():
            return AdoptionResult(
                ok=False, confidence=confidence, rejection_reason="sanitized compose is empty"
            )

        # Tab characters are illegal in YAML but common in hand-written compose
        # files (inline comments, copy-pasted indentation). Normalise before
        # the YAML gate, same as the remediation generator.
        sanitized = sanitized.replace("\t", "  ")

        try:
            yaml.safe_load(sanitized)
        except yaml.YAMLError as exc:
            reason = f"sanitized compose is not valid YAML: {exc}"
            _log.warning("adoption_rejected", reason=reason)
            return AdoptionResult(ok=False, confidence=confidence, rejection_reason=reason)

        return AdoptionResult(
            ok=True,
            confidence=confidence,
            sanitized_compose=sanitized,
            detected_secret_keys=list(raw.get("detected_secret_keys", []) or []),
            reasoning=raw.get("reasoning", "") or "",
        )
