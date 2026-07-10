"""Environment-driven configuration for the registry MCP server."""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Transport = Literal["stdio", "sse", "streamable-http"]
GitProviderName = Literal["gitea", "github", "gitlab"]
NotificationProviderName = Literal["ntfy", "smtp", "none"]
ApplyModeName = Literal["manual", "webhook", "ansible"]


class Settings(BaseSettings):
    """Runtime configuration, loaded from environment and `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Persistence
    registry_db_path: str = Field(default="/data/registry.db")
    registry_log_path: str = Field(default="/data/events.log")

    # Upstream APIs
    traefik_api_url: str | None = Field(default=None)
    traefik_timeout_seconds: float = Field(default=10.0)
    traefik_retries: int = Field(default=3)
    authentik_api_url: str | None = Field(default=None)
    authentik_token: str | None = Field(default=None)
    authentik_timeout_seconds: float = Field(default=10.0)
    authentik_retries: int = Field(default=3)

    # MCP transport
    mcp_transport: Transport = Field(default="streamable-http")
    mcp_host: str = Field(default="0.0.0.0")
    mcp_port: int = Field(default=8765)

    # Event log retention
    event_retention_days: int = Field(default=90)

    # Discovery
    docker_base_url: str | None = Field(default=None)
    discovery_docker_enabled: bool = Field(default=True)
    discovery_traefik_interval_seconds: int = Field(default=300)
    discovery_docker_interval_seconds: int = Field(default=300)
    discovery_authentik_interval_seconds: int = Field(default=900)
    discovery_network_enabled: bool = Field(default=False)
    discovery_stale_after_misses: int = Field(default=3)

    # Reasoning layer (DSPy) — Phase 7. Off by default: the server reasons only
    # when explicitly enabled. The deterministic discovery/reconcile path is
    # unaffected when this is false.
    dspy_enabled: bool = Field(default=False)
    dspy_model: str = Field(default="anthropic/claude-haiku-4-5-20251001")
    dspy_api_key: str | None = Field(default=None)
    dspy_confidence_threshold: float = Field(default=0.7)
    dspy_max_tokens: int = Field(default=1024)
    # Patch generation must emit a complete corrected file plus several fields,
    # so it needs a larger budget than the default reasoning calls — too small a
    # limit truncates the response and fails field parsing.
    dspy_patch_max_tokens: int = Field(default=4096)
    # Directory holding optimized modules saved by a Phase 9 optimization pass.
    dspy_compiled_path: str | None = Field(default=None)

    # --- Write path (Phase 8) — all opt-in; the server is read-only by default ---
    # Git provider: where remediation PRs are opened.
    git_provider: GitProviderName = Field(default="gitea")
    git_base_url: str | None = Field(default=None)
    git_token: str | None = Field(default=None)
    git_repo: str | None = Field(default=None)  # owner/repo
    git_base_branch: str = Field(default="main")

    # Notification provider: alerts when proposals are opened/verified.
    notification_provider: NotificationProviderName = Field(default="none")
    notification_url: str | None = Field(default=None)
    notification_topic: str = Field(default="homelab-registry")
    notification_token: str | None = Field(default=None)

    # SMTP notification provider (Phase 5) — templated HTML email per proposal
    # event. Validated in production against SMTP2GO; any standard SMTP relay
    # with STARTTLS works.
    notification_smtp_host: str | None = Field(default=None)
    notification_smtp_port: int = Field(default=587)
    notification_smtp_username: str | None = Field(default=None)
    notification_smtp_password: str | None = Field(default=None)
    notification_smtp_use_tls: bool = Field(default=True)
    notification_from_email: str | None = Field(default=None)
    notification_to_email: str | None = Field(default=None)

    # Apply mechanism: how the change lands after a human merges the PR. The
    # server never applies it — this only shapes the PR description.
    apply_mode: ApplyModeName = Field(default="manual")

    # Proposal behavior. Creation is opt-in; dry-run generates patches without
    # opening PRs. The confidence gate below this threshold rejects a patch.
    proposal_auto_create: bool = Field(default=False)
    proposal_dry_run: bool = Field(default=False)
    proposal_stale_days: int = Field(default=7)
    proposal_confidence_threshold: float = Field(default=0.8)
    proposal_label: str = Field(default="homelab-registry-mcp")
    # Template for the compose file an app service maps to in the Git repo.
    proposal_compose_path_template: str = Field(default="nodes/{node}/{service}/compose.yaml")

    # Conversational loop (Phase 3) — opt-in polling of PR comments so a human
    # can request changes to an open proposal PR without leaving GitHub/Gitea.
    # Never runs when the startup health check failed (read-only mode).
    proposal_comment_poll_enabled: bool = Field(default=False)
    proposal_comment_poll_interval_seconds: int = Field(default=300)
    # Fail-closed allowlist: comma-separated GitHub/Gitea usernames whose PR
    # comments are trusted to trigger an autonomous commit. Empty (the default)
    # means no comment is trusted, even with polling enabled — a PR is visible
    # to anyone with repo access, and an unauthenticated commenter must never be
    # able to steer a committed change.
    proposal_comment_allowed_users: str = Field(default="")

    # Normalization (opt-in; engine deferred to a later Phase 8 increment).
    normalization_enabled: bool = Field(default=False)
    normalization_schedule: str = Field(default="weekly")

    # Secrets / git-crypt (Phase C) — all opt-in; off by default.
    # secrets_key_path takes priority over secrets_git_crypt_key.
    secrets_enabled: bool = Field(default=True)
    secrets_repo_path: str | None = Field(default=None)
    secrets_key_path: str | None = Field(default=None)
    secrets_git_crypt_key: str | None = Field(default=None)

    # Startup health checks (Phase 2) — control-plane provisioning prerequisites
    # for the GitOps/Ansible write path. Absolute paths only: pydantic-settings
    # reads these as literal strings, so `~`/`$HOME` are not expanded.
    ansible_cfg_path: str | None = Field(default=None)
    ssh_key_path: str | None = Field(default=None)

    log_level: str = Field(default="INFO")


def get_settings() -> Settings:
    """Build a `Settings` instance from the current environment."""
    return Settings()
