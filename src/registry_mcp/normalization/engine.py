"""Normalization engine: scans the repo for compose files, applies the
canonical form (``docs/specs/spec-compose-normal-form.md``), and opens one PR
per node with the safe changes it found. Kept out of ``proposal/`` so a
normalization PR can never bundle a security remediation — they are always
separate PRs with separate labels.

Mirrors ``proposal/engine.py``'s discipline: never merges, honors dry-run by
stopping before any Git write, and treats a Git failure as an in-band error
rather than raising past the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from registry_mcp.logging import get_logger
from registry_mcp.models import FindingType, Proposal, ProposalStatus
from registry_mcp.normalization.formatter import normalize as format_file
from registry_mcp.normalization.rules import Finding
from registry_mcp.normalization.scanner import FileReport, scan
from registry_mcp.providers.git import GitError

if TYPE_CHECKING:
    from registry_mcp.config import Settings
    from registry_mcp.normalization.generator import NormalizationGenerator
    from registry_mcp.proposal.store import ProposalStore
    from registry_mcp.providers.git import GitProvider
    from registry_mcp.providers.notification import NotificationProvider

_log = get_logger("normalization.engine")

_SCHEDULE_SECONDS = {"daily": 86400, "weekly": 604800, "monthly": 2592000}


def schedule_seconds(schedule: str) -> int:
    """Map ``NORMALIZATION_SCHEDULE`` to an APScheduler interval in seconds.
    Accepts the named presets or a raw positive-integer-seconds string;
    falls back to weekly for anything else — including zero or negative,
    which APScheduler's interval trigger rejects outright at ``add_job()``."""
    if schedule in _SCHEDULE_SECONDS:
        return _SCHEDULE_SECONDS[schedule]
    try:
        seconds = int(schedule)
    except (TypeError, ValueError):
        seconds = None
    if seconds is None or seconds <= 0:
        _log.warning("normalization_schedule_invalid", schedule=schedule, fallback="weekly")
        return _SCHEDULE_SECONDS["weekly"]
    return seconds


@dataclass
class _FileChange:
    old_path: str
    new_path: str
    content: str
    rename: bool
    confidence: float


class NormalizationEngine:
    def __init__(
        self,
        *,
        settings: Settings,
        proposals: ProposalStore,
        generator: NormalizationGenerator,
        notifier: NotificationProvider,
        git: GitProvider | None,
    ) -> None:
        self._settings = settings
        self._proposals = proposals
        self._generator = generator
        self._notifier = notifier
        self._git = git

    @property
    def configured(self) -> bool:
        """True when the write path can open PRs (git provider + repo set)."""
        return self._git is not None and bool(self._settings.git_repo)

    # -- helpers -------------------------------------------------------
    def _branch_name(self, node: str) -> str:
        today = datetime.now().strftime("%Y-%m-%d")
        return f"normalize/{node}-{today}"

    @staticmethod
    def _assert_feature_branch(branch: str, base: str) -> None:
        """Same invariant check as ``ProposalEngine`` — defense in depth
        against ever committing to the base branch."""
        if not branch or branch == base:
            raise GitError(
                f"refusing to commit to {branch!r}: not a feature branch (base is {base!r})"
            )

    def _process_file(self, report: FileReport) -> _FileChange | None:
        """Deterministic pass first, DSPy escalation only for what it left
        unresolved. Returns ``None`` when nothing needs to change."""
        new_path = report.path
        if report.misnamed and self._settings.normalization_rename_misnamed:
            new_path = report.path.rsplit("/", 1)[0] + "/compose.yaml"

        formatted = format_file(report.content)
        if formatted is None:
            escalated = self._generator.escalate(
                file_path=report.path, current_file=report.content, violations=[]
            )
            if not escalated.ok:
                _log.info(
                    "normalization_file_rejected",
                    path=report.path,
                    reason=escalated.rejection_reason,
                )
                return None
            content, confidence = escalated.content, escalated.confidence
        elif formatted.skipped_rules:
            escalated = self._generator.escalate(
                file_path=report.path,
                current_file=formatted.content,
                violations=formatted.skipped_rules,
            )
            # DSPy unavailable/rejected: the deterministic partial result is
            # already proven safe, just not fully compliant — commit it
            # rather than blocking the file entirely.
            content = escalated.content if escalated.ok else formatted.content
            confidence = escalated.confidence if escalated.ok else 1.0
        else:
            content, confidence = formatted.content, 1.0

        changed = content != report.content
        renamed = new_path != report.path
        if not changed and not renamed:
            return None
        return _FileChange(
            old_path=report.path,
            new_path=new_path,
            content=content,
            rename=renamed,
            confidence=confidence,
        )

    def _pr_body(
        self, node: str, changes: list[_FileChange], findings: list[Finding], truncated: int
    ) -> str:
        lines = [f"Normalization pass for `{node}` — formatting only, no behavior change."]
        renamed = [c for c in changes if c.rename]
        if renamed:
            lines += [
                "",
                "**Note:** this PR renames the following stacks' compose file to "
                "`compose.yaml` — the deploy pipeline could not see them under "
                "their previous filename, so they become deploy-visible for the "
                "first time on merge:",
            ]
            lines += [f"- `{c.old_path}` → `{c.new_path}`" for c in renamed]
        lines += ["", "**Files changed:**"]
        lines += [f"- `{c.new_path}`" for c in changes]
        if truncated:
            lines += [
                "",
                f"_{truncated} additional file(s) on this node were left for a future pass "
                f"(NORMALIZATION_MAX_FILES_PER_PR={self._settings.normalization_max_files_per_pr})._",
            ]
        if findings:
            lines += ["", "**Reported, not auto-fixed** (needs a human decision):"]
            for f in findings:
                svc = f" ({f.service})" if f.service else ""
                lines.append(f"- `{f.path}`{svc}: {f.rule_id} — {f.detail}")
        return "\n".join(lines)

    async def _normalize_node(
        self, node: str, reports: list[FileReport], *, dry_run: bool, actor: str
    ) -> dict[str, Any]:
        finding_type = FindingType.normalization
        proposal_key = f"nodes/{node}"

        existing = self._proposals.find_open_by_path(proposal_key, finding_type)
        if existing is not None:
            return {
                "node": node,
                "skipped": "open normalization proposal already exists for this node",
                "proposal": existing.model_dump(mode="json"),
            }

        findings = [f for report in reports for f in report.findings]
        if not self._settings.normalization_rename_misnamed:
            findings += [
                Finding(
                    rule_id="N-100",
                    path=report.path,
                    service=None,
                    detail="misnamed compose file; not renamed "
                    "(NORMALIZATION_RENAME_MISNAMED=false)",
                )
                for report in reports
                if report.misnamed
            ]

        changes: list[_FileChange] = []
        for report in reports:
            change = self._process_file(report)
            if change is not None:
                changes.append(change)

        cap = self._settings.normalization_max_files_per_pr
        truncated = max(0, len(changes) - cap)
        changes = changes[:cap]

        if not changes:
            return {
                "node": node,
                "skipped": "no changes needed",
                "findings": [f.to_dict() for f in findings],
            }

        body = self._pr_body(node, changes, findings, truncated)
        commit_message = f"style: normalize compose files on {node}"

        if dry_run:
            return {
                "node": node,
                "dry_run": True,
                "files": [
                    {"path": c.new_path, "renamed_from": c.old_path if c.rename else None}
                    for c in changes
                ],
                "pr_body": body,
                "findings": [f.to_dict() for f in findings],
            }

        repo = self._settings.git_repo
        base = self._settings.git_base_branch
        branch = self._branch_name(node)
        try:
            self._assert_feature_branch(branch, base)
            await self._git.create_branch(repo, branch, base)  # type: ignore[union-attr]
            for change in changes:
                await self._git.commit_file(  # type: ignore[union-attr]
                    repo, change.new_path, change.content, branch, commit_message
                )
                if change.rename:
                    # Commit the new path first, delete the old path second —
                    # a failure between the two leaves both copies present
                    # rather than neither.
                    await self._git.delete_file(  # type: ignore[union-attr]
                        repo, change.old_path, branch, commit_message
                    )
            opened = await self._git.open_pr(  # type: ignore[union-attr]
                repo,
                f"Normalize compose files on {node}",
                body,
                branch,
                base,
                self._settings.normalization_label,
            )
        except GitError as exc:
            _log.warning("normalization_git_failed", node=node, error=str(exc))
            return {"node": node, "error": f"git operation failed: {exc}"}

        confidences = [c.confidence for c in changes]
        proposal = self._proposals.create(
            Proposal(
                service_id=None,
                finding_type=finding_type,
                pr_url=opened.url,
                pr_number=opened.number,
                branch=branch,
                file_path=proposal_key,
                diff="\n".join(c.new_path for c in changes),
                status=ProposalStatus.open,
                confidence=min(confidences) if confidences else None,
                actor=actor,
            )
        )
        await self._notify(
            f"[PR] {node}: normalization PR opened",
            f"{len(changes)} file(s) normalized"
            + (f", {truncated} deferred to a future pass" if truncated else ""),
            url=opened.url,
        )
        return {
            "node": node,
            **proposal.model_dump(mode="json"),
            "findings": [f.to_dict() for f in findings],
        }

    async def run_sweep(
        self,
        *,
        node: str | None = None,
        dry_run: bool | None = None,
        actor: str = "scheduler:normalization",
    ) -> dict[str, Any]:
        """Scan the repo and open (or dry-run) one normalization PR per node.

        ``node`` restricts the sweep to a single node; ``dry_run`` overrides
        ``NORMALIZATION_DRY_RUN`` for this call only. Returns
        ``{"items": [...per-node result...], "findings": [...], "scanned": N}``.
        """
        if not self.configured:
            return {"error": "write path not configured (set GIT_BASE_URL, GIT_TOKEN, GIT_REPO)"}

        effective_dry_run = dry_run if dry_run is not None else self._settings.normalization_dry_run
        repo = self._settings.git_repo
        ref = self._settings.git_base_branch
        try:
            grouped = await scan(
                self._git,  # type: ignore[arg-type]
                repo,
                ref,
                path_glob=self._settings.normalization_path_glob,
            )
        except GitError as exc:
            return {"error": f"scan failed: {exc}"}

        if node is not None:
            grouped = {node: grouped[node]} if node in grouped else {}

        items = [
            await self._normalize_node(name, reports, dry_run=effective_dry_run, actor=actor)
            for name, reports in grouped.items()
        ]
        scanned = sum(len(reports) for reports in grouped.values())
        # Each item already carries its own "findings" (including any N-100
        # rename notice) — aggregate from there rather than re-deriving from
        # the raw scan, so the two never drift apart.
        all_findings = [f for item in items for f in item.get("findings", [])]
        return {"items": items, "findings": all_findings, "scanned": scanned}

    async def _notify(self, title: str, body: str, url: str | None = None) -> None:
        try:
            await self._notifier.send(title, body, url)
        except Exception as exc:  # notification must never abort a proposal
            _log.warning("normalization_notify_failed", error=str(exc))
