"""MCP tools for brownfield adoption (Phase 7).

Two-call flow so a human always decides secret handling before anything is
committed:

1. `proposal_adopt_service` — SSH-inspects the live container behind an
   already-discovered (but not yet GitOps-managed) service, asks the DSPy
   reasoning layer to sanitize its legacy compose file, and persists an
   `AdoptionDraft`. No Git write happens here.
2. `proposal_adopt_service_finalize` — given the operator's "keep" or "rotate"
   choice, git-crypt-encrypts a `.env` in the local homelab clone and pushes
   it, commits the sanitized compose via the Git provider, and opens the PR.

See `registry_mcp.adoption` and `registry_mcp.gitcrypt` for why the `.env`
write goes through a local clone rather than the remote Git hosting API.
"""

from __future__ import annotations

import secrets as pysecrets
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from registry_mcp import gitcrypt
from registry_mcp.adoption import ssh as remote
from registry_mcp.logging import get_logger
from registry_mcp.models import (
    AdoptionDraft,
    AdoptionDraftStatus,
    DetectedSecret,
    FindingType,
    Proposal,
    ProposalStatus,
    SourceType,
)
from registry_mcp.providers.git import GitError

if TYPE_CHECKING:
    from registry_mcp.adoption.store import AdoptionDraftStore
    from registry_mcp.config import Settings
    from registry_mcp.hardware.store import HardwareStore
    from registry_mcp.proposal import AdoptionGenerator
    from registry_mcp.proposal.store import ProposalStore
    from registry_mcp.providers.git import GitProvider
    from registry_mcp.providers.notification import NotificationProvider
    from registry_mcp.registry import RegistryStore

_log = get_logger("tools.adoption")

_SECRET_STRATEGIES = {"keep", "rotate"}


def _secret_kv(secret: DetectedSecret | dict) -> tuple[str, str]:
    """SQLModel JSON columns of nested pydantic models round-trip as plain
    dicts once reloaded from the database (the same quirk `HardwareStore`
    works around for `storage_pools`) — accept either shape."""
    if isinstance(secret, dict):
        return secret["key"], secret["live_value"]
    return secret.key, secret.live_value


def _unavailable(settings: Settings, git: GitProvider | None) -> str | None:
    """Return an error message if adoption's prerequisites aren't met."""
    if not settings.adoption_enabled:
        return "Adoption is disabled. Set ADOPTION_ENABLED=true to enable."
    if git is None or not settings.git_repo:
        return "Git write path not configured (set GIT_BASE_URL, GIT_TOKEN, GIT_REPO)."
    if not settings.secrets_repo_path:
        return "SECRETS_REPO_PATH is not configured (needed to git-crypt-encrypt the adopted .env)."
    if not settings.ssh_key_path:
        return "SSH_KEY_PATH is not configured (needed to inspect the live container)."
    return None


def register_adoption_tools(
    mcp: FastMCP,
    settings: Settings,
    store: RegistryStore,
    hardware_store: HardwareStore,
    adoption_store: AdoptionDraftStore,
    generator: AdoptionGenerator,
    git: GitProvider | None,
    proposals: ProposalStore,
    notifier: NotificationProvider,
    read_only: bool = False,
) -> None:
    def _read_only_error() -> dict[str, Any] | None:
        if read_only:
            return {
                "error": "Server is in read-only mode (startup health check failed). "
                "Run system_health_check for details."
            }
        return None

    async def _notify(title: str, body: str, url: str | None = None) -> None:
        try:
            await notifier.send(title, body, url)
        except Exception as exc:  # notification must never abort adoption
            _log.warning("notify_failed", title=title, error=str(exc))

    def _target_file_path(service: Any, node: Any) -> str:
        node_label = service.host or node.hostname
        return settings.proposal_compose_path_template.format(node=node_label, service=service.name)

    @mcp.tool()
    async def proposal_adopt_service(service_id: str, ssh_user: str | None = None) -> dict:
        """Inspect a live, pre-existing Docker service and draft its adoption
        into GitOps management.

        The service must already be discovered (via Docker) and linked to a
        `HardwareNode` (see `hardware-link-service`). SSHes into that node,
        reads the container's live env and its original docker-compose.yml,
        and asks the reasoning layer to sanitize any hardcoded secrets into
        `${VAR}` interpolations. Nothing is committed yet — the response's
        `next_step` explains what to ask the operator before calling
        `proposal_adopt_service_finalize`.
        """
        if err := _read_only_error():
            return err
        if err := _unavailable(settings, git):
            return {"error": err}

        service = store.get_service(service_id)
        if service is None:
            return {"error": f"no service found for {service_id!r}"}

        if not service.hardware_node_id:
            return {
                "error": (
                    f"{service.name!r} is not linked to a hardware node. "
                    "Run hardware-link-service first so adoption knows which host to SSH into."
                )
            }
        node = hardware_store.get_node(service.hardware_node_id)
        if node is None:
            return {"error": f"linked hardware node {service.hardware_node_id!r} not found"}
        host = node.ip_address or node.hostname

        source = store.get_source(service.id, SourceType.docker)
        if source is None:
            return {
                "error": (
                    f"{service.name!r} has no Docker provenance — adoption requires a "
                    "service discovered via the Docker source."
                )
            }
        container = str(source.raw.get("name") or source.raw.get("id") or "")
        if not container:
            return {"error": "Docker provenance is missing a container name/id"}

        user = ssh_user or settings.ssh_default_user
        key_path = settings.ssh_key_path
        assert key_path is not None  # guarded by _unavailable() above

        try:
            inspect_data = await remote.inspect_container(
                key_path=key_path, user=user, host=host, container=container
            )
        except remote.SSHError as exc:
            return {"error": f"docker inspect failed: {exc}"}

        labels = remote.labels_from_inspect(inspect_data)
        env = remote.env_dict_from_inspect(inspect_data)
        config_files, _working_dir = remote.compose_paths_from_labels(labels)
        if not config_files:
            return {
                "error": (
                    "container has no docker-compose labels — it wasn't started via "
                    "`docker compose` and can't be traced back to a compose file"
                )
            }
        compose_path = config_files[0]

        try:
            raw_compose = await remote.read_remote_file(
                key_path=key_path, user=user, host=host, path=compose_path
            )
        except remote.SSHError as exc:
            return {"error": f"could not read {compose_path!r} on {host}: {exc}"}

        result = generator.generate(
            compose_content=raw_compose, container_env=env, container_labels=labels
        )

        if not result.ok:
            proposal = proposals.create(
                Proposal(
                    service_id=service.id,
                    finding_type=FindingType.legacy_adoption,
                    status=ProposalStatus.rejected,
                    rejection_reason=result.rejection_reason,
                    confidence=result.confidence,
                    actor="manual:proposal_adopt_service",
                )
            )
            await _notify(
                f"WARNING: {service.name}: adoption needs manual review",
                f"Sanitization rejected: {result.rejection_reason}",
            )
            return {
                "rejected": result.rejection_reason,
                "proposal": proposal.model_dump(mode="json"),
            }

        detected = [
            DetectedSecret(key=key, live_value=env[key])
            for key in result.detected_secret_keys
            if key in env
        ]
        missing = [key for key in result.detected_secret_keys if key not in env]
        if missing:
            _log.warning(
                "adoption_detected_key_not_in_live_env", service=service.name, keys=missing
            )

        draft = adoption_store.create(
            AdoptionDraft(
                service_id=service.id,
                host=host,
                ssh_user=user,
                container_name=container,
                compose_path=compose_path,
                target_file_path=_target_file_path(service, node),
                sanitized_compose=result.sanitized_compose,
                detected_secrets=[s.model_dump() for s in detected],
                confidence=result.confidence,
                reasoning=result.reasoning,
                expires_at=adoption_store.ttl_expiry(settings.adoption_draft_ttl_minutes),
            )
        )

        if detected:
            next_step = (
                f"Ask the operator: keep the existing values or generate new ones for: "
                f"{', '.join(s.key for s in detected)}? Then call "
                f"proposal_adopt_service_finalize(draft_id={draft.id!r}, "
                f"secret_strategy='keep' or 'rotate')."
            )
        else:
            next_step = (
                "No secrets were detected in this compose file. Call "
                f"proposal_adopt_service_finalize(draft_id={draft.id!r}) to open the PR."
            )

        return {
            "draft_id": draft.id,
            "service": service.name,
            "detected_secret_keys": [s.key for s in detected],
            "sanitized_compose": result.sanitized_compose,
            "confidence": result.confidence,
            "reasoning": result.reasoning,
            "expires_at": draft.expires_at.isoformat(),
            "next_step": next_step,
        }

    @mcp.tool()
    async def proposal_adopt_service_finalize(draft_id: str, secret_strategy: str = "keep") -> dict:
        """Finalize a drafted adoption: write the operator's chosen secret
        values, git-crypt-encrypt them, and open the PR.

        `secret_strategy` is `"keep"` (reuse the live values captured during
        inspection) or `"rotate"` (generate fresh random values — never via
        the reasoning layer, always a local cryptographically random value).
        Ignored when the draft detected no secrets.
        """
        if err := _read_only_error():
            return err
        if err := _unavailable(settings, git):
            return {"error": err}
        if secret_strategy not in _SECRET_STRATEGIES:
            return {"error": f"secret_strategy must be one of {sorted(_SECRET_STRATEGIES)}"}

        draft = adoption_store.get_pending(draft_id)
        if draft is None:
            return {
                "error": (
                    f"no pending draft found for {draft_id!r} "
                    "(it may already be finalized, cancelled, or expired)"
                )
            }
        service = store.get_service(draft.service_id)
        if service is None:
            return {"error": f"no service found for {draft.service_id!r}"}

        env_data = {}
        for secret in draft.detected_secrets:
            key, live_value = _secret_kv(secret)
            env_data[key] = live_value if secret_strategy == "keep" else pysecrets.token_urlsafe(32)

        branch = f"adopt/{service.name}-{datetime.now().strftime('%Y-%m-%d')}"
        base = settings.git_base_branch
        repo = settings.git_repo
        assert repo is not None  # guarded by _unavailable() above

        pushed_locally = False
        if env_data:
            try:
                local_repo = gitcrypt.repo_path(settings)
                key = gitcrypt.key_bytes(settings)
            except RuntimeError as exc:
                return {"error": str(exc)}

            env_path = str(Path(draft.target_file_path).parent / ".env")
            try:
                gitcrypt.check_path(local_repo, env_path)
            except ValueError as exc:
                return {"error": str(exc)}

            try:
                await gitcrypt.git_checkout_branch(local_repo, branch, base)
                await gitcrypt.ensure_unlocked(local_repo, key)
                target = local_repo / env_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(gitcrypt.serialize_dotenv(env_data))
                await gitcrypt.ensure_gitattributes_entry(local_repo, env_path)
                await gitcrypt.git_commit_paths(
                    local_repo,
                    [env_path, ".gitattributes"],
                    f"chore: add secrets for {service.name} (adopted)",
                )
                await gitcrypt.git_push_branch(local_repo, branch)
                pushed_locally = True
            except RuntimeError as exc:
                return {"error": f"git-crypt commit/push failed: {exc}"}

        try:
            if not pushed_locally:
                await git.create_branch(repo, branch, base)  # type: ignore[union-attr]
            await git.commit_file(  # type: ignore[union-attr]
                repo,
                draft.target_file_path,
                draft.sanitized_compose,
                branch,
                f"feat: adopt {service.name} into GitOps management",
            )
            opened = await git.open_pr(  # type: ignore[union-attr]
                repo,
                f"Adopt {service.name} into GitOps management",
                _pr_body(service.name, draft, secret_strategy),
                branch,
                base,
                settings.proposal_label,
            )
        except GitError as exc:
            _log.warning("adoption_finalize_git_failed", service=service.name, error=str(exc))
            return {"error": f"git operation failed: {exc}"}

        proposal = proposals.create(
            Proposal(
                service_id=service.id,
                finding_type=FindingType.legacy_adoption,
                pr_url=opened.url,
                pr_number=opened.number,
                branch=branch,
                file_path=draft.target_file_path,
                diff=draft.sanitized_compose,
                status=ProposalStatus.open,
                confidence=draft.confidence,
                actor="manual:proposal_adopt_service_finalize",
            )
        )
        adoption_store.set_status(draft.id, AdoptionDraftStatus.finalized)
        await _notify(
            f"[PR] {service.name}: adoption PR opened",
            f"Adopted into GitOps management (secrets: {secret_strategy}).",
            url=opened.url,
        )
        return proposal.model_dump(mode="json")

    @mcp.tool()
    def proposal_adopt_service_cancel(draft_id: str) -> dict:
        """Discard a pending adoption draft without committing anything."""
        draft = adoption_store.get(draft_id)
        if draft is None:
            return {"error": f"no draft found for {draft_id!r}"}
        updated = adoption_store.set_status(draft_id, AdoptionDraftStatus.cancelled)
        return (updated or draft).model_dump(mode="json")

    @mcp.tool()
    def proposal_adopt_service_get(draft_id: str) -> dict:
        """Full detail on one adoption draft, including the sanitized compose
        preview and which secret keys are pending a keep/rotate decision."""
        draft = adoption_store.get(draft_id)
        if draft is None:
            return {"error": f"no draft found for {draft_id!r}"}
        payload = draft.model_dump(mode="json")
        # Live secret values are for the finalize decision only — never echo
        # them back through a read tool.
        payload["detected_secrets"] = [_secret_kv(s)[0] for s in draft.detected_secrets]
        return payload


def _pr_body(service_name: str, draft: AdoptionDraft, secret_strategy: str) -> str:
    keys = ", ".join(_secret_kv(s)[0] for s in draft.detected_secrets) or "none"
    return (
        f"Automated brownfield adoption of `{service_name}`, originally running from a "
        f"hand-written compose file at `{draft.compose_path}` on `{draft.host}`.\n\n"
        f"**Secrets detected:** {keys}\n"
        f"**Secret handling:** {secret_strategy}\n\n"
        f"{draft.reasoning}\n\n"
        "Review the sanitized compose file and the `.env` commit before merging."
    )
