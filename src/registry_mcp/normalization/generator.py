"""DSPy escalation: finishes the Tier 1 rules the deterministic formatter
(``formatter.py``) couldn't safely apply on its own.

Called in two situations, both via ``escalate()``:

- The formatter returned ``None`` (not compose-shaped, or the round-trip
  itself failed) — ``current_file`` is the true original text.
- The formatter returned a result with non-empty ``skipped_rules`` — a
  comment stood in the way of a safe reorder or list-to-mapping conversion —
  ``current_file`` is the already-partially-normalized text and
  ``violations`` names exactly the rules left unapplied.

Either way this is finishing specific, named formatting, never rewriting a
whole clean file from scratch. Same discipline as ``proposal/generator.py``:
credential scrub, confidence gate, non-empty check, YAML validity — plus the
equivalence guarantee, checked against whatever text was handed in here. That
comparison is sound by transitivity: when escalation runs after a partial
deterministic pass, that pass already proved *its* output equivalent to the
true original, so proving DSPy's output equivalent to it is enough. There is
no rule-based fallback — a failed gate is a rejection, never a hand-applied
fix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import yaml

from registry_mcp.logging import get_logger
from registry_mcp.normalization.rules import is_equivalent
from registry_mcp.proposal.generator import _scrub_credentials

if TYPE_CHECKING:
    from registry_mcp.dspy import Reasoner

_log = get_logger("normalization.generator")

# Handed to NormalizeConfigFile as `canonical_form` — a compact restatement of
# docs/specs/spec-compose-normal-form.md's Tier 1 rules, not the whole spec.
CANONICAL_FORM_SUMMARY = (
    "2-space indent; no top-level version: key; top-level keys ordered "
    "services, volumes, networks, configs, secrets, then alphabetical; "
    "per-service keys ordered image, container_name, restart, depends_on, "
    "env_file, environment, command, entrypoint, ports, volumes, networks, "
    "labels, healthcheck, deploy, then alphabetical; labels as a sorted "
    "mapping (never a list) with quoted string values; ports as quoted "
    "strings; environment as a mapping; every existing comment preserved "
    "verbatim and attached to the same line it annotated; no --- document "
    "marker; exactly one trailing newline."
)


@dataclass
class NormalizationResult:
    """Outcome of a DSPy escalation attempt."""

    ok: bool
    confidence: float = 0.0
    rejection_reason: str | None = None
    content: str = ""
    commit_message: str = ""
    reasoning: str = field(default="")


class NormalizationGenerator:
    """Wraps the DSPy ``NormalizeConfigFile`` module with the same
    confidence/YAML-validity discipline as ``PatchGenerator``, plus the
    normalization-specific equivalence guarantee."""

    def __init__(self, reasoner: Reasoner, *, threshold: float = 0.8) -> None:
        self._reasoner = reasoner
        self._threshold = threshold

    def escalate(
        self, *, file_path: str, current_file: str, violations: list[str]
    ) -> NormalizationResult:
        raw = self._reasoner.normalize_config(
            current_file=current_file,
            file_path=file_path,
            violations=", ".join(violations) or "file could not be parsed as compose YAML",
            canonical_form=CANONICAL_FORM_SUMMARY,
        )
        if raw is None:
            return NormalizationResult(
                ok=False,
                rejection_reason="reasoning layer unavailable (DSPY_ENABLED=false or call errored)",
            )

        # Deterministic secret scrub, before any gate runs — same discipline
        # as PatchGenerator.generate().
        content = raw.get("normalized_file", "") or ""
        content, scrubbed = _scrub_credentials(content)
        if scrubbed:
            _log.warning("normalization_scrubbed_credentials", file_path=file_path)

        # The reasoning text is echoed into the PR body; scrub it too.
        reasoning = raw.get("reasoning", "") or ""
        reasoning, reasoning_scrubbed = _scrub_credentials(reasoning)
        if reasoning_scrubbed:
            _log.warning("normalization_scrubbed_credentials_in_reasoning", file_path=file_path)

        confidence = float(raw.get("confidence", 0.0))
        if confidence < self._threshold:
            reason = f"confidence {confidence:.2f} below threshold {self._threshold:.2f}"
            _log.info("normalization_rejected", file_path=file_path, reason=reason)
            return NormalizationResult(ok=False, confidence=confidence, rejection_reason=reason)

        if not content.strip():
            return NormalizationResult(
                ok=False, confidence=confidence, rejection_reason="normalized file is empty"
            )

        content = content.replace("\t", "  ")

        try:
            yaml.safe_load(content)
        except yaml.YAMLError as exc:
            reason = f"normalized file is not valid YAML: {exc}"
            _log.warning("normalization_rejected", file_path=file_path, reason=reason)
            return NormalizationResult(ok=False, confidence=confidence, rejection_reason=reason)

        if not is_equivalent(current_file, content):
            reason = "normalized file is not behavior-equivalent to the input"
            _log.warning("normalization_rejected", file_path=file_path, reason=reason)
            return NormalizationResult(ok=False, confidence=confidence, rejection_reason=reason)

        return NormalizationResult(
            ok=True,
            confidence=confidence,
            content=content,
            commit_message=raw.get("commit_message") or f"style: normalize {file_path}",
            reasoning=reasoning,
        )
