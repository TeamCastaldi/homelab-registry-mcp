"""MCP tool for repo intake (conversational deploy Phase 1, ADR-018).

Read-only and single-call, unlike the two-call human-decision gates elsewhere
in this codebase (adoption, proposal finalize) — there is nothing to decide
yet at this phase. `service-intake-repo` only turns a repo URL into
structured requirements; nothing is drafted, committed, or persisted. Later
phases (placement, secrets block, PR assembly) are what turn this into a
write.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from registry_mcp.intake import IntakeError, fetch_repo, parse_snapshot
from registry_mcp.logging import get_logger

if TYPE_CHECKING:
    from registry_mcp.config import Settings
    from registry_mcp.dspy import Reasoner

_log = get_logger("tools.intake")

_READ_ONLY = ToolAnnotations(readOnlyHint=True)


def register_intake_tools(mcp: FastMCP, settings: Settings, reasoner: Reasoner) -> None:
    @mcp.tool(name="service-intake-repo", annotations=_READ_ONLY)
    async def service_intake_repo(repo_url: str) -> dict[str, Any]:
        """Shallow-fetch a source repo and work out what it needs to run.

        Reads the repo's Dockerfile/compose file/README (nothing else, and
        nothing is kept — the clone is discarded before this returns) and
        extracts base image, ports, env vars, volumes, and dependencies.
        Deterministic facts are always returned; an `inference` block fills
        gaps the reasoning layer is confident about (backing services the
        README implies, which env vars need an operator-supplied value) when
        `DSPY_ENABLED=true` and its confidence clears
        `SERVICE_DEPLOY_CONFIDENCE_THRESHOLD` — otherwise `inference` is null
        and `inference_rejection_reason` says why, same discipline the
        proposal layer's patch generation uses: a low-confidence guess is
        discarded, never returned as if it were a fact.

        Read-only: this is intake only. Nothing is drafted, written, or
        committed — later phases turn this into a deploy.
        """
        if not settings.service_deploy_enabled:
            return {
                "error": "Conversational deploy is disabled. "
                "Set SERVICE_DEPLOY_ENABLED=true to enable."
            }

        try:
            snapshot = await fetch_repo(
                repo_url,
                timeout_seconds=settings.service_deploy_clone_timeout_seconds,
                max_repo_mb=settings.service_deploy_max_repo_mb,
            )
        except IntakeError as exc:
            return {"error": str(exc)}

        requirements = parse_snapshot(snapshot)

        result: dict[str, Any] = {
            "repo_url": snapshot.repo_url,
            "dockerfile_found": snapshot.dockerfile is not None,
            "readme_found": snapshot.readme is not None,
            "compose_path": snapshot.compose_path,
            "requirements": requirements.as_dict(),
            "skipped_files": snapshot.skipped,
            "inference": None,
        }

        if not reasoner.enabled:
            result["inference_rejection_reason"] = (
                "reasoning layer disabled; set DSPY_ENABLED=true for dependency/"
                "operator-env-var inference"
            )
            return result

        try:
            inferred = reasoner.infer_service_requirements(
                repo_url=snapshot.repo_url,
                readme=snapshot.readme or "",
                detected=requirements.as_dict(),
            )
        except Exception as exc:  # reasoning must never break intake
            _log.warning("intake_inference_failed", repo_url=snapshot.repo_url, error=str(exc))
            result["inference_rejection_reason"] = f"reasoning call failed: {exc}"
            return result

        threshold = settings.service_deploy_confidence_threshold
        if inferred is None:
            result["inference_rejection_reason"] = "reasoning call returned no result"
            return result
        confidence = inferred.get("confidence", 0.0)
        if confidence < threshold:
            result["inference_rejection_reason"] = (
                f"confidence {confidence:.2f} below threshold {threshold:.2f}"
            )
            return result

        result["inference"] = inferred
        return result
