"""MCP tool for compose generation (conversational deploy Phase 2, ADR-019).

Read-only and single-call, like `service-intake-repo`: it drafts a compose file
and hands it back, but nothing is persisted, committed, or deployed. Phase 5's
two-call `service-deploy-create`/`-finalize` gate is what will turn a draft
into a PR; until then this tool exists so generation quality can be judged on
real repos before anything downstream depends on it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from registry_mcp.tools.intake import run_intake

if TYPE_CHECKING:
    from registry_mcp.config import Settings
    from registry_mcp.dspy import Reasoner
    from registry_mcp.service_deploy import ComposeGenerator

_READ_ONLY = ToolAnnotations(readOnlyHint=True)

# Doubles as a future `nodes/<node>/<service>/` directory name (Phase 5), so it
# is held to a path-safe subset of what Compose accepts for a service key.
_SERVICE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")


def _resolve_service_name(requested: str, intake: dict[str, Any]) -> str:
    """Caller's name, else the accepted inference's, else the repo's last path
    segment."""
    if requested.strip():
        return requested.strip()
    inferred = (intake.get("inference") or {}).get("service_name") or ""
    if inferred.strip():
        return inferred.strip()
    segment = intake["repo_url"].rstrip("/").rsplit("/", 1)[-1]
    return segment.removesuffix(".git").lower()


def register_service_deploy_tools(
    mcp: FastMCP, settings: Settings, reasoner: Reasoner, generator: ComposeGenerator
) -> None:
    @mcp.tool(name="service-deploy-generate-compose", annotations=_READ_ONLY)
    async def service_deploy_generate_compose(
        repo_url: str, service_name: str = "", target_node: str = ""
    ) -> dict[str, Any]:
        """Draft a homelab-conformant compose.yaml for a source repo.

        Runs the same intake as `service-intake-repo`, then asks the
        reasoning layer for a complete compose file following this homelab's
        canonical rules. `service_name` defaults to the name intake inferred,
        else the repo's name; `target_node` is optional context (placement is
        a later phase).

        No fallback, same as every generator here: a draft below
        `SERVICE_DEPLOY_CONFIDENCE_THRESHOLD`, or one that isn't a valid
        compose file, comes back with `ok: false` and a `rejection_reason`,
        never a guessed file. An accepted draft has been run through the
        canonical formatter; `findings` lists any Tier 2 issues (e.g. an
        unpinned image) for a human to judge, and `skipped_rules` any
        formatting rule a comment kept the formatter from applying.

        Read-only: nothing is written, committed, or deployed.
        """
        if not settings.service_deploy_enabled:
            return {
                "error": "Conversational deploy is disabled. "
                "Set SERVICE_DEPLOY_ENABLED=true to enable."
            }
        # Checked before cloning: without the reasoning layer there is nothing
        # to generate, and no fallback that could stand in for it.
        if not reasoner.enabled:
            return {"error": "Compose generation requires DSPY_ENABLED=true."}

        intake = await run_intake(settings, reasoner, repo_url)
        if "error" in intake:
            return intake

        name = _resolve_service_name(service_name, intake)
        if not _SERVICE_NAME_RE.match(name):
            return {
                "error": f"Service name {name!r} is not a valid lowercase name "
                "(letters, digits, '.', '_', '-'); pass service_name explicitly.",
                "intake": intake,
            }

        draft = await generator.generate(
            intake={
                "repo_url": intake["repo_url"],
                "detected": intake["requirements"],
                "inference": intake["inference"],
            },
            service_name=name,
            target_node=target_node,
        )
        return {
            "service_name": name,
            "target_node": target_node or None,
            "ok": draft.ok,
            "confidence": draft.confidence,
            "rejection_reason": draft.rejection_reason,
            "compose": draft.compose or None,
            "findings": draft.findings,
            "skipped_rules": draft.skipped_rules,
            "reasoning": draft.reasoning,
            "intake": intake,
        }
