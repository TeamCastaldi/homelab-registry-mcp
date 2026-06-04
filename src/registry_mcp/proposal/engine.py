"""Proposal engine: create remediation PRs, sweep for verification.

The server proposes; the engineer decides; infrastructure enforces. This engine
opens one PR per finding and records a :class:`Proposal`. It never merges PRs and
never writes to the filesystem. The verification sweep watches for the conflict
to clear on a later discovery pass and marks the proposal verified.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from registry_mcp.logging import get_logger
from registry_mcp.models import FindingType, Proposal, ProposalStatus
from registry_mcp.models.service import utcnow
from registry_mcp.providers.git import GitError

if TYPE_CHECKING:
    from registry_mcp.config import Settings
    from registry_mcp.models import Service
    from registry_mcp.proposal.generator import PatchGenerator
    from registry_mcp.proposal.store import ProposalStore
    from registry_mcp.providers.git import GitProvider
    from registry_mcp.providers.notification import NotificationProvider
    from registry_mcp.registry import RegistryStore

_log = get_logger("proposal.engine")

_APPLY_FOOTER = {
    "ansible": (
        "\n\n---\n_Merge this PR to apply the change. The GitHub Actions workflow "
        "will trigger Ansible to deploy the update to the affected node automatically._"
    ),
    "webhook": (
        "\n\n---\n_Merge this PR to apply the change. The configured webhook "
        "will trigger deployment to the affected node automatically._"
    ),
    "manual": (
        "\n\n---\n_After merging, deploy the change to the affected node "
        "(pull the repo and reload the service) to apply it._"
    ),
}

class ProposalEngine:
    def __init__(
        self,
        *,
        settings: Settings,
        store: RegistryStore,
        proposals: ProposalStore,
        generator: PatchGenerator,
        notifier: NotificationProvider,
        git: GitProvider | None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._proposals = proposals
        self._generator = generator
        self._notifier = notifier
        self._git = git

    @property
    def configured(self) -> bool:
        """True when the write path can open PRs (git provider + repo set)."""
        return self._git is not None and bool(self._settings.git_repo)

    # -- helpers -----------------------------------------------------------
    def _finding_type(self, service: Service) -> FindingType | None:
        if service.auth_mode_conflict:
            return FindingType.auth_mode_conflict
        return None

    def _resolve_target(self, service: Service) -> str | None:
        """Map a service to the repo file that should be edited."""
        if not service.host:
            return None
        return self._settings.proposal_compose_path_template.format(
            node=service.host, service=service.name
        )

    def _branch_name(self, finding: FindingType, service: Service) -> str:
        today = datetime.now().strftime("%Y-%m-%d")
        return f"patch/{finding.value}-{service.name}-{today}"

    def _apply_footer(self) -> str:
        return _APPLY_FOOTER.get(self._settings.apply_mode, _APPLY_FOOTER["manual"])

    # -- creation ----------------------------------------------------------
    async def create_for_service(
        self, service_id: str, *, actor: str = "manual:proposal_create"
    ) -> dict[str, Any]:
        if not self.configured:
            return {"error": "write path not configured (set GIT_BASE_URL, GIT_TOKEN, GIT_REPO)"}

        service = self._store.get_service(service_id)
        if service is None:
            return {"error": f"no service found for {service_id!r}"}

        finding = self._finding_type(service)
        if finding is None:
            return {"error": f"service {service.name!r} has no open finding to remediate"}

        existing = self._proposals.find_open(service.id, finding)
        if existing is not None:
            return {
                "skipped": "open proposal already exists",
                "proposal": existing.model_dump(mode="json"),
            }

        file_path = self._resolve_target(service)
        if file_path is None:
            return {
                "error": f"cannot resolve a target file for {service.name!r} (unknown host/node)"
            }

        repo = self._settings.git_repo
        base = self._settings.git_base_branch
        try:
            current_file = await self._git.read_file(repo, file_path, base)  # type: ignore[union-attr]
        except GitError as exc:
            return {"error": f"could not read {file_path} from {repo}: {exc}"}

        result = await self._generator.generate(
            service=service.model_dump(mode="json"),
            finding_type=finding.value,
            current_file=current_file,
            file_path=file_path,
            apply_mode=self._settings.apply_mode,
        )

        if not result.ok:
            proposal = self._proposals.create(
                Proposal(
                    service_id=service.id,
                    finding_type=finding,
                    file_path=file_path,
                    status=ProposalStatus.rejected,
                    rejection_reason=result.rejection_reason,
                    confidence=result.confidence,
                    actor=actor,
                )
            )
            await self._notify(
                f"WARNING: {service.name}: remediation needs manual review",
                f"Patch rejected: {result.rejection_reason}",
            )
            return {
                "rejected": result.rejection_reason,
                "proposal": proposal.model_dump(mode="json"),
            }

        if self._settings.proposal_dry_run:
            _log.info(
                "proposal_dry_run",
                service=service.name,
                file_path=file_path,
                confidence=result.confidence,
            )
            return {
                "dry_run": True,
                "service": service.name,
                "file_path": file_path,
                "confidence": result.confidence,
                "commit_message": result.commit_message,
                "pr_title": result.pr_title,
                "pr_body": result.pr_body + self._apply_footer(),
                "patch": result.patch,
            }

        branch = self._branch_name(finding, service)
        body = result.pr_body + self._apply_footer()
        try:
            await self._git.create_branch(repo, branch, base)  # type: ignore[union-attr]
            await self._git.commit_file(  # type: ignore[union-attr]
                repo, file_path, result.patch, branch, result.commit_message
            )
            opened = await self._git.open_pr(  # type: ignore[union-attr]
                repo, result.pr_title, body, branch, base, self._settings.proposal_label
            )
        except GitError as exc:
            _log.warning("proposal_git_failed", service=service.name, error=str(exc))
            return {"error": f"git operation failed: {exc}"}

        proposal = self._proposals.create(
            Proposal(
                service_id=service.id,
                finding_type=finding,
                pr_url=opened.url,
                pr_number=opened.number,
                branch=branch,
                file_path=file_path,
                diff=result.patch,
                status=ProposalStatus.open,
                confidence=result.confidence,
                actor=actor,
            )
        )
        await self._notify(
            f"[PR] {service.name}: remediation PR opened",
            f"{result.pr_title}\n{result.reasoning}".strip(),
            url=opened.url,
        )
        return proposal.model_dump(mode="json")

    # -- verification ------------------------------------------------------
    async def sweep_verifications(self) -> list[Proposal]:
        """Mark open proposals verified when their conflict has cleared."""
        verified: list[Proposal] = []
        for proposal in self._proposals.list_open(exclude_normalization=True):
            if proposal.service_id is None:
                continue
            service = self._store.get_service(proposal.service_id)
            if service is None:
                continue
            if not service.auth_mode_conflict:
                self._proposals.set_status(proposal.id, ProposalStatus.verified, resolved=True)
                await self._notify(
                    f"OK: {service.name} is now secured",
                    "The conflict cleared on the latest discovery pass — proposal verified.",
                    url=proposal.pr_url or None,
                )
                verified.append(proposal)
            elif self._age_days(proposal) > self._settings.proposal_stale_days:
                # Logged rather than notified to avoid repeating on every pass.
                _log.info(
                    "proposal_stale",
                    service=service.name,
                    age_days=self._age_days(proposal),
                    pr_url=proposal.pr_url,
                )
        return verified

    @staticmethod
    def _age_days(proposal: Proposal) -> int:
        created = proposal.created_at
        if created.tzinfo is None:
            return (utcnow().replace(tzinfo=None) - created).days
        return (utcnow() - created).days

    async def after_discovery(self) -> None:
        """Scheduler hook: verify open proposals, and (if enabled) open new ones.

        Wrapped so a proposal failure never disrupts the discovery pass.
        """
        if not self.configured:
            return
        try:
            await self.sweep_verifications()
            if self._settings.proposal_auto_create:
                await self._auto_create()
        except Exception as exc:  # never let proposals break discovery
            _log.warning("after_discovery_failed", error=str(exc))

    async def _auto_create(self) -> None:
        for service in self._store.list_services():
            if not service.auth_mode_conflict or service.stale:
                continue
            if self._proposals.find_open(service.id, FindingType.auth_mode_conflict):
                continue
            try:
                await self.create_for_service(service.id, actor="discovery:auto")
            except Exception as exc:
                _log.warning("auto_create_failed", service=service.name, error=str(exc))

    # -- lifecycle ---------------------------------------------------------
    async def cancel(self, proposal_id: str) -> dict[str, Any]:
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            return {"error": f"no proposal found for {proposal_id!r}"}
        if self.configured and proposal.pr_number:
            try:
                await self._git.close_pr(self._settings.git_repo, proposal.pr_number)  # type: ignore[union-attr]
            except GitError as exc:
                _log.warning("proposal_close_failed", proposal_id=proposal_id, error=str(exc))
        updated = self._proposals.set_status(proposal_id, ProposalStatus.cancelled)
        return (updated or proposal).model_dump(mode="json")

    async def _notify(self, title: str, body: str, url: str | None = None) -> None:
        try:
            await self._notifier.send(title, body, url)
        except Exception as exc:  # notification must never abort a proposal
            _log.warning("notify_failed", title=title, error=str(exc))
