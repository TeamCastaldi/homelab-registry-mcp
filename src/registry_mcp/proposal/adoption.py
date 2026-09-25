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

# Live env values at least this long are masked in the compose text before it
# reaches the LLM provider. Shorter ones ("80", "true", "info") are rarely
# secrets and too common to replace as substrings without wrecking the file.
_MIN_MASKED_LENGTH = 8
_PLACEHOLDER_PREFIX = "<value-of:"


def _placeholder(key: str) -> str:
    return f"{_PLACEHOLDER_PREFIX}{key}>"


def _mask_live_values(compose: str, env: dict) -> tuple[str, dict[str, str]]:
    """Replace each live env value (of `_MIN_MASKED_LENGTH`+ chars) found in the
    compose text with a `<value-of:KEY>` placeholder, so the provider sees key
    names and structure but never the values. Returns the masked text and
    {placeholder: original value} for `_restore_live_values`. Longest values
    first, so a value that contains another is masked whole."""
    masked = compose
    placeholders: dict[str, str] = {}
    for key, value in sorted(env.items(), key=lambda kv: len(str(kv[1])), reverse=True):
        value = str(value)
        if len(value) < _MIN_MASKED_LENGTH or value not in masked:
            continue
        placeholder = _placeholder(key)
        masked = masked.replace(value, placeholder)
        placeholders[placeholder] = value
    return masked, placeholders


def _restore_live_values(text: str, placeholders: dict[str, str]) -> str:
    """Put back every placeholder the model kept (a value it judged not secret)."""
    for placeholder, value in placeholders.items():
        text = text.replace(placeholder, value)
    return text


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
        # Live secret values never go to the LLM provider: the compose text has
        # them masked as <value-of:KEY> placeholders, and the env is sent as
        # names only. The model decides secrecy from names and context.
        masked_compose, placeholders = _mask_live_values(compose_content, container_env)
        raw = self._reasoner.detect_hardcoded_secrets(
            compose_content=masked_compose,
            container_env={key: _placeholder(key) for key in container_env},
            container_labels=container_labels,
        )
        if raw is None:
            return AdoptionResult(
                ok=False,
                rejection_reason="reasoning layer unavailable (DSPY_ENABLED=false or call errored)",
            )

        # Placeholders the model kept were judged not secret: restore their
        # values. One it altered or invented can't be restored faithfully.
        sanitized = _restore_live_values(raw.get("sanitized_compose", "") or "", placeholders)
        if _PLACEHOLDER_PREFIX in sanitized:
            reason = "sanitized compose contains a value placeholder the model altered or invented"
            _log.warning("adoption_rejected", reason=reason)
            return AdoptionResult(ok=False, rejection_reason=reason)

        # Deterministic secret scrub, before any gate runs — and after the
        # restore, so a real secret the model wrongly kept as a placeholder is
        # still caught: any credential-shaped value is replaced with a
        # placeholder so it can never reach a commit.
        sanitized, scrubbed = _scrub_credentials(sanitized)
        if scrubbed:
            _log.warning("adoption_scrubbed_residual_credentials")

        # The reasoning text is echoed verbatim into the PR body and the tool's
        # response — scrub it too, or a credential the model quotes while
        # explaining its detection would leak out through a channel the
        # sanitized-compose scrub above never touches.
        reasoning = raw.get("reasoning", "") or ""
        reasoning, reasoning_scrubbed = _scrub_credentials(reasoning)
        if reasoning_scrubbed:
            _log.warning("adoption_scrubbed_residual_credentials_in_reasoning")

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
            reasoning=reasoning,
        )
