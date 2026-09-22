"""Compose generation: calls the DSPy reasoning layer and enforces the gates.

Same discipline as ``proposal/generator.py`` — there is no rule-based
fallback. If the reasoning layer is unavailable, returns low confidence, or
produces something that isn't a valid compose file, the result is a
rejection, never a hand-written draft.

A generated file then goes through the deterministic canonical formatter
(``normalization/formatter.py``) before anyone sees it. There is no "before"
file here, so the formatter's equivalence guarantee only proves its own
reshaping changed nothing; the Tier 2 checks (``normalization/rules.check``)
run on the result and are *reported*, never auto-fixed or blocking — same as
a normalization sweep treats them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import yaml

from registry_mcp.logging import get_logger
from registry_mcp.normalization import formatter, rules
from registry_mcp.normalization.generator import CANONICAL_FORM_SUMMARY
from registry_mcp.proposal.generator import _scrub_credentials

if TYPE_CHECKING:
    from registry_mcp.dspy import Reasoner
    from registry_mcp.providers.git import GitProvider

_log = get_logger("service_deploy.generator")

# normalization/rules.check()'s Tier 2 findings, restated as requirements a
# fresh draft should meet up front instead of findings reported afterward.
REQUIRED_RULES_SUMMARY = (
    "every service uses a published image with a pinned version tag (never "
    ":latest, never a build: key), sets a restart: policy, and has a "
    "container_name equal to its service key; an external reverse-proxy "
    "network uses ${PROXY_NETWORK:-proxy-net} as its key under the top-level "
    "networks: mapping, never a hardcoded name; any host "
    "ports: mapping carries a # temporary comment; no hardcoded credentials — "
    "secrets are ${VAR} interpolations."
)

# Label for Tier 2 findings; the draft has no repo path until Phase 5 commits it.
_DRAFT_PATH = "compose.yaml"


@dataclass
class ComposeDraft:
    """Outcome of a compose-generation attempt."""

    ok: bool
    confidence: float = 0.0
    rejection_reason: str | None = None
    compose: str = ""
    skipped_rules: list[str] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)
    reasoning: str = ""


class ComposeGenerator:
    """Wraps the DSPy ``GenerateServiceCompose`` module with confidence,
    YAML-validity, and compose-shape gates, then canonicalizes the result.

    Conventions handed to the model always include this repo's own canonical
    rules. When a Git provider is configured, the homelab repo's compose spec
    is appended as supplementary context — best-effort, like
    ``PatchGenerator``'s middleware fetch, so a failed read never blocks
    generation. The homelab copy has drifted before, so the prompt tells the
    model this repo's rules win on conflict.
    """

    def __init__(
        self,
        reasoner: Reasoner,
        *,
        threshold: float = 0.8,
        git: GitProvider | None = None,
        repo: str | None = None,
        base: str = "main",
        conventions_path: str = "docs/spec/compose.yaml",
    ) -> None:
        self._reasoner = reasoner
        self._threshold = threshold
        self._git = git
        self._repo = repo
        self._base = base
        self._conventions_path = conventions_path

    async def _conventions(self) -> str:
        baseline = (
            f"Canonical shape: {CANONICAL_FORM_SUMMARY}\n\nRequired: {REQUIRED_RULES_SUMMARY}"
        )
        if self._git is None or not self._repo:
            return baseline
        try:
            spec = await self._git.read_file(self._repo, self._conventions_path, self._base)
        except Exception as exc:  # never block generation on the context fetch
            _log.warning("conventions_fetch_failed", path=self._conventions_path, error=str(exc))
            return baseline
        return (
            f"{baseline}\n\nHomelab compose spec ({self._conventions_path}) — supplementary; "
            f"where it conflicts with the rules above, the rules above win:\n{spec}"
        )

    def _reject(self, reason: str, *, confidence: float = 0.0) -> ComposeDraft:
        _log.info("compose_draft_rejected", reason=reason)
        return ComposeDraft(ok=False, confidence=confidence, rejection_reason=reason)

    async def generate(
        self, *, intake: dict, service_name: str, target_node: str = ""
    ) -> ComposeDraft:
        conventions = await self._conventions()
        raw = self._reasoner.generate_service_compose(
            intake=intake,
            homelab_conventions=conventions,
            service_name=service_name,
            target_node=target_node,
        )
        if raw is None:
            return self._reject("reasoning layer unavailable (DSPY_ENABLED=false or call errored)")

        # Deterministic secret scrub before any gate runs, on both the draft
        # and the reasoning text returned alongside it.
        compose, scrubbed = _scrub_credentials(raw.get("compose_yaml", "") or "")
        if scrubbed:
            _log.warning("compose_draft_scrubbed_credentials", service_name=service_name)
        reasoning, _ = _scrub_credentials(raw.get("reasoning", "") or "")

        confidence = float(raw.get("confidence", 0.0))
        if confidence < self._threshold:
            return self._reject(
                f"confidence {confidence:.2f} below threshold {self._threshold:.2f}",
                confidence=confidence,
            )

        if not compose.strip():
            return self._reject("generated compose file is empty", confidence=confidence)

        # Tabs are illegal in YAML but common in model-emitted inline comments.
        compose = compose.replace("\t", "  ")

        try:
            doc = yaml.safe_load(compose)
        except yaml.YAMLError as exc:
            return self._reject(
                f"generated compose file is not valid YAML: {exc}", confidence=confidence
            )

        services = doc.get("services") if isinstance(doc, dict) else None
        if not isinstance(services, dict) or not services:
            return self._reject("generated file has no services: mapping", confidence=confidence)
        if service_name not in services:
            return self._reject(
                f"generated file has no service named {service_name!r}", confidence=confidence
            )

        normalized = formatter.normalize(compose)
        if normalized is None:
            return self._reject(
                "canonical formatter could not process the generated file",
                confidence=confidence,
            )

        findings = rules.check(
            yaml.safe_load(normalized.content), raw_text=normalized.content, path=_DRAFT_PATH
        )
        return ComposeDraft(
            ok=True,
            confidence=confidence,
            compose=normalized.content,
            skipped_rules=normalized.skipped_rules,
            findings=[f.to_dict() for f in findings],
            reasoning=reasoning,
        )
