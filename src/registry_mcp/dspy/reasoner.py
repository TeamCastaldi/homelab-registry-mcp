"""The reasoning layer: DSPy-backed enrichment with explicit confidence gates.

This is the home of the Phase 7 enrichment modules. It reasons and returns typed
results; it never writes to the registry or to infrastructure. The detection
layer (``registry/reconcile.py``) and the discovery engine consume the callables
exposed here — they never import dspy directly, which keeps LLM calls out of the
deterministic layers (per ``docs/agentic-design-intent.md``).

DSPy and litellm are imported lazily, only when the layer is enabled, so a
server with ``DSPY_ENABLED=false`` starts fast and emits no litellm warnings.

Confidence gates are explicit Python threshold checks. ``dspy.Assert`` /
``dspy.Suggest`` were removed in DSPy 3.x; the checks here are the idiomatic
replacement. A result that falls below the threshold is discarded and the
deterministic fallback applies — the system degrades gracefully rather than
writing a low-confidence guess.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from registry_mcp.logging import get_logger
from registry_mcp.models import AuthMode, Category

if TYPE_CHECKING:
    from registry_mcp.config import Settings

_log = get_logger("reasoning")


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _coerce_enum(value: Any, enum_cls: type) -> Any | None:
    if value is None:
        return None
    try:
        return enum_cls(str(value).strip().lower())
    except ValueError:
        return None


def _last_completion(lm: Any, *, limit: int = 4000) -> str:
    """Best-effort: the raw text of the most recent LM completion, truncated.

    Used to diagnose parse failures (e.g. a response truncated by the token
    budget), where the partial output is otherwise lost inside the exception.
    """
    try:
        entry = lm.history[-1]
    except (AttributeError, IndexError, TypeError):
        return "<no LM history available>"
    raw = entry.get("outputs") or entry.get("response") or entry.get("messages")
    if isinstance(raw, list) and raw:
        raw = raw[0]
    text = str(raw)
    return text[:limit] + ("…[truncated]" if len(text) > limit else "")


class Reasoner:
    """DSPy enrichment modules with confidence gates and graceful degradation.

    Constructing a ``Reasoner`` is cheap — dspy is imported and the language
    model configured lazily on first use. When ``enabled`` is false every
    operation short-circuits to ``None`` (or a structured error for the
    client-facing summary), leaving the deterministic path untouched.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.enabled = bool(settings.dspy_enabled)
        self._configured = False
        self._resolve: Any = None
        self._infer: Any = None
        self._summarize: Any = None
        self._patch: Any = None
        self._patch_lm: Any = None
        self._revise: Any = None
        self._detect_secrets: Any = None

    # -- lazy setup --------------------------------------------------------
    def _ensure(self) -> None:
        if self._configured:
            return
        import dspy

        from registry_mcp.dspy.signatures import (
            ApplyReviewFeedback,
            DetectHardcodedSecrets,
            GenerateRemediationPatch,
            InferServiceMetadata,
            ResolveServiceIdentity,
            SummarizeAccessAudit,
        )

        lm = dspy.LM(
            self._settings.dspy_model,
            api_key=self._settings.dspy_api_key,
            max_tokens=self._settings.dspy_max_tokens,
        )
        dspy.configure(lm=lm)
        self._resolve = dspy.ChainOfThought(ResolveServiceIdentity)
        self._infer = dspy.ChainOfThought(InferServiceMetadata)
        self._summarize = dspy.ChainOfThought(SummarizeAccessAudit)
        self._patch = dspy.ChainOfThought(GenerateRemediationPatch)
        # Patch generation emits a whole file plus several fields, so it gets its
        # own LM with a larger token budget; the others keep the default budget.
        self._patch_lm = dspy.LM(
            self._settings.dspy_model,
            api_key=self._settings.dspy_api_key,
            max_tokens=self._settings.dspy_patch_max_tokens,
        )
        self._patch.set_lm(self._patch_lm)
        self._revise = dspy.ChainOfThought(ApplyReviewFeedback)
        # Also emits a whole file; reuse the patch LM's larger token budget.
        self._revise.set_lm(self._patch_lm)
        self._detect_secrets = dspy.ChainOfThought(DetectHardcodedSecrets)
        # Also emits a whole file; reuse the patch LM's larger token budget.
        self._detect_secrets.set_lm(self._patch_lm)
        self._load_compiled()
        self._configured = True
        _log.info("reasoning_configured", model=self._settings.dspy_model)

    def _load_compiled(self) -> None:
        """Best-effort load of optimized modules saved by a Phase 9 pass."""
        path = self._settings.dspy_compiled_path
        if not path:
            return
        import os

        for module, fname in (
            (self._resolve, "resolve_identity.json"),
            (self._infer, "infer_metadata.json"),
            (self._summarize, "summarize_access.json"),
            (self._patch, "remediation_patch.json"),
            (self._revise, "apply_review_feedback.json"),
            (self._detect_secrets, "detect_hardcoded_secrets.json"),
        ):
            full = os.path.join(path, fname)
            if not os.path.exists(full):
                continue
            try:
                module.load(full)
                _log.info("reasoning_loaded_compiled", file=full)
            except Exception as exc:  # fall back to the uncompiled module
                _log.warning("reasoning_compiled_load_failed", file=full, error=str(exc))

    @property
    def threshold(self) -> float:
        return self._settings.dspy_confidence_threshold

    # -- operations --------------------------------------------------------
    def resolve_identity(self, candidate: dict, existing: list[dict]) -> str | None:
        """Return the `name` of the existing service the candidate matches, or
        None when there is no confident match (and a new service should be
        created)."""
        if not self.enabled or not existing:
            return None
        self._ensure()
        try:
            pred = self._resolve(candidate=candidate, existing_services=existing)
        except Exception as exc:
            _log.warning("reasoning_failed", op="resolve_identity", error=str(exc))
            return None
        name = (getattr(pred, "matched_name", "") or "").strip()
        conf = _as_float(getattr(pred, "confidence", 0.0))
        valid = {e.get("name") for e in existing}
        if not name or name not in valid or conf < self.threshold:
            _log.info(
                "reasoning_no_match",
                op="resolve_identity",
                matched_name=name,
                confidence=conf,
                threshold=self.threshold,
            )
            return None
        _log.info("reasoning_match", op="resolve_identity", matched_name=name, confidence=conf)
        return name

    def infer_metadata(
        self, *, router_rule: str, middlewares: list[str], service_name: str
    ) -> dict | None:
        """Infer curated fields (display_name/category/auth_mode/notes) for a
        new service, or None when confidence is below threshold."""
        if not self.enabled:
            return None
        self._ensure()
        try:
            pred = self._infer(
                router_rule=router_rule,
                middlewares=list(middlewares or []),
                service_name=service_name,
            )
        except Exception as exc:
            _log.warning("reasoning_failed", op="infer_metadata", error=str(exc))
            return None
        conf = _as_float(getattr(pred, "confidence", 0.0))
        if conf < self.threshold:
            _log.info(
                "reasoning_low_confidence",
                op="infer_metadata",
                service_name=service_name,
                confidence=conf,
                threshold=self.threshold,
            )
            return None
        out: dict[str, Any] = {}
        category = _coerce_enum(getattr(pred, "category", None), Category)
        if category is not None:
            out["category"] = category
        auth_mode = _coerce_enum(getattr(pred, "auth_mode", None), AuthMode)
        if auth_mode is not None:
            out["auth_mode"] = auth_mode
        display = (getattr(pred, "display_name", "") or "").strip()
        if display:
            out["display_name"] = display
        notes = (getattr(pred, "notes", "") or "").strip()
        if notes:
            out["notes"] = notes
        return out or None

    def summarize_access(self, *, slug: str, events: list[dict], hours: int) -> dict:
        """Summarize Authentik events for an application. Returns a structured
        report, or a structured error when the layer is disabled or fails."""
        if not self.enabled:
            return {"error": "reasoning layer disabled; set DSPY_ENABLED=true to enable summaries"}
        self._ensure()
        events = list(events or [])
        try:
            pred = self._summarize(application_slug=slug, events=events, time_window_hours=hours)
        except Exception as exc:
            _log.warning("reasoning_failed", op="summarize_access", error=str(exc))
            return {"error": f"reasoning failed: {exc}"}
        return {
            "application_slug": slug,
            "time_window_hours": hours,
            "event_count": len(events),
            "summary": getattr(pred, "summary", ""),
            "anomalies": list(getattr(pred, "anomalies", []) or []),
            "unique_users": _as_int(getattr(pred, "unique_users", 0)),
            "failed_auth_count": _as_int(getattr(pred, "failed_auth_count", 0)),
            "risk_level": getattr(pred, "risk_level", "unknown"),
        }

    def generate_remediation_patch(
        self,
        *,
        service: dict,
        finding_type: str,
        current_file: str,
        file_path: str,
        apply_mode: str,
        existing_middlewares: str = "",
    ) -> dict | None:
        """Generate a complete corrected file for a security finding.

        Returns the raw module outputs (including ``confidence``) so the
        proposal layer can enforce its own gate and record a rejection reason;
        returns None only when the reasoning layer is disabled or the call
        errors. Per the design intent, there is no rule-based fallback — when
        this returns None the proposal layer must reject, never hand-write a
        patch.
        """
        if not self.enabled:
            return None
        self._ensure()
        try:
            pred = self._patch(
                service=service,
                finding_type=finding_type,
                current_file=current_file,
                file_path=file_path,
                apply_mode=apply_mode,
                existing_middlewares=existing_middlewares,
            )
        except Exception as exc:
            # A truncated response fails field parsing here; capture the partial
            # completion so the truncation is diagnosable from the logs.
            _log.warning(
                "reasoning_failed",
                op="generate_remediation_patch",
                error=str(exc),
                partial_response=_last_completion(self._patch_lm),
            )
            return None
        return {
            "patch": getattr(pred, "patch", "") or "",
            "commit_message": getattr(pred, "commit_message", "") or "",
            "pr_title": getattr(pred, "pr_title", "") or "",
            "pr_body": getattr(pred, "pr_body", "") or "",
            "confidence": _as_float(getattr(pred, "confidence", 0.0)),
            "reasoning": getattr(pred, "reasoning", "") or "",
        }

    def apply_review_feedback(
        self, *, file_path: str, current_file: str, feedback: str
    ) -> dict | None:
        """Revise a file on an open proposal PR per a human reviewer's comment.

        Returns the raw module outputs (including ``confidence``) so the
        proposal layer can enforce its own gate; returns None only when the
        reasoning layer is disabled or the call errors. Same no-fallback rule
        as ``generate_remediation_patch``: a None or low-confidence result
        must be rejected, never hand-applied.
        """
        if not self.enabled:
            return None
        self._ensure()
        try:
            pred = self._revise(file_path=file_path, current_file=current_file, feedback=feedback)
        except Exception as exc:
            _log.warning(
                "reasoning_failed",
                op="apply_review_feedback",
                error=str(exc),
                partial_response=_last_completion(self._patch_lm),
            )
            return None
        return {
            "revised_file": getattr(pred, "revised_file", "") or "",
            "commit_message": getattr(pred, "commit_message", "") or "",
            "confidence": _as_float(getattr(pred, "confidence", 0.0)),
            "reasoning": getattr(pred, "reasoning", "") or "",
        }

    def detect_hardcoded_secrets(
        self, *, compose_content: str, container_env: dict, container_labels: dict
    ) -> dict | None:
        """Sanitize a legacy compose file for brownfield adoption (Phase 7).

        Returns the raw module outputs (including ``confidence``) so
        ``AdoptionGenerator`` can enforce its own gate and record a rejection
        reason; returns None only when the reasoning layer is disabled or the
        call errors. Same no-fallback rule as ``generate_remediation_patch``:
        a None or low-confidence result must be rejected, never hand-sanitized.
        """
        if not self.enabled:
            return None
        self._ensure()
        try:
            pred = self._detect_secrets(
                compose_content=compose_content,
                container_env=container_env,
                container_labels=container_labels,
            )
        except Exception as exc:
            _log.warning(
                "reasoning_failed",
                op="detect_hardcoded_secrets",
                error=str(exc),
                partial_response=_last_completion(self._patch_lm),
            )
            return None
        return {
            "sanitized_compose": getattr(pred, "sanitized_compose", "") or "",
            "detected_secret_keys": list(getattr(pred, "detected_secret_keys", []) or []),
            "confidence": _as_float(getattr(pred, "confidence", 0.0)),
            "reasoning": getattr(pred, "reasoning", "") or "",
        }


def build_reasoner(settings: Settings) -> Reasoner:
    """Construct the reasoning layer. Cheap; dspy is imported lazily on first use."""
    return Reasoner(settings)
