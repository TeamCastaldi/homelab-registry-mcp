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
    komodo_api_url: str | None = Field(default=None)
    komodo_api_key: str | None = Field(default=None)
    komodo_api_secret: str | None = Field(default=None)
    komodo_timeout_seconds: float = Field(default=10.0)
    komodo_retries: int = Field(default=3)

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

    # Normalization (opt-in) — scans nodes/*/*/compose.yaml against
    # docs/specs/spec-compose-normal-form.md and opens one PR per node with
    # any safe formatting fixes. Reuses GIT_*; always a separate PR/label
    # from security proposals, never bundled with one.
    normalization_enabled: bool = Field(default=False)
    normalization_schedule: str = Field(default="weekly")
    normalization_path_glob: str = Field(default="nodes/*/*/compose.yaml")
    # Caps the diff size of a single node's PR on a first run against a messy repo.
    normalization_max_files_per_pr: int = Field(default=25)
    normalization_dry_run: bool = Field(default=False)
    # N-100 (misnamed compose file rename) makes a stack visible to the
    # deploy pipeline for the first time — opt in separately from the rest
    # of normalization, which is purely cosmetic.
    normalization_rename_misnamed: bool = Field(default=False)
    normalization_label: str = Field(default="normalization")

    # Brownfield adoption (Phase 7) — opt-in. Reuses SSH_KEY_PATH (the same key
    # Ansible uses to reach workload nodes) to inspect a live container and its
    # original compose file; reuses GIT_*/SECRETS_* for the resulting PR.
    adoption_enabled: bool = Field(default=False)
    ssh_default_user: str = Field(default="root")
    # How long a drafted adoption may sit awaiting the operator's keep/rotate
    # decision before it expires. The draft holds captured live secret values
    # in the registry SQLite (not git-crypt encrypted) until then.
    adoption_draft_ttl_minutes: int = Field(default=60)

    # Deletion confirmation gate — every hard-delete tool (registry_delete_service,
    # hardware-delete-node) requires solving a short arithmetic challenge before
    # the row is removed. Not a security boundary (single digits, shown in the
    # challenge itself) — deliberate friction against an agent or a
    # fat-fingered id deleting something irreversible.
    delete_challenge_ttl_minutes: int = Field(default=5, gt=0)

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

    # --- Chat interface — opt-in browser UI backed by a local/LAN Ollama instance ---
    # Off by default. When enabled, a session (Authentik OIDC if configured, else
    # CHAT_PASSWORD) is required to reach /chat; all lab data the assistant sees
    # flows through the same MCP tools any other client uses, via a fixed
    # read-only allowlist — CHAT_ALLOW_WRITE opts into a small write allowlist,
    # and never when the server is in read-only mode. Ollama itself is untouched
    # by this — no upstream write.
    chat_enabled: bool = Field(default=False)
    chat_ollama_url: str | None = Field(default=None)
    chat_ollama_model: str = Field(default="qwen3:14b")
    chat_ollama_timeout_seconds: float = Field(default=300.0)
    chat_ollama_retries: int = Field(default=3)
    chat_ollama_keep_alive: str = Field(default="30m")
    chat_num_ctx: int = Field(default=8192)
    chat_temperature: float = Field(default=0.6)
    chat_think: bool = Field(default=False)
    chat_max_concurrent: int = Field(default=2)
    chat_max_history_messages: int = Field(default=20)
    chat_allow_write: bool = Field(default=False)
    # Comma-separated tool names to additionally deny, on top of the built-in
    # DENY_ALWAYS set. Restrictive-only — cannot re-admit a hard-denied tool.
    chat_tool_deny: str = Field(default="")
    chat_max_tool_rounds: int = Field(default=4)
    chat_tool_result_max_chars: int = Field(default=8000)
    chat_context_max_chars: int = Field(default=6000)
    chat_context_ttl_seconds: int = Field(default=60)
    # Absolute path to an operator-specific persona overlay (e.g. a homelab
    # skill file) appended to the generic in-repo persona. Same no-expansion
    # caveat as SECRETS_REPO_PATH/ANSIBLE_CFG_PATH — pydantic-settings reads
    # this as a literal string, so `~`/`$HOME` are not expanded.
    chat_persona_path: str | None = Field(default=None)
    chat_persona_max_chars: int = Field(default=8000)
    # HMAC key signing the session cookie. Unset generates an ephemeral
    # per-process key — sessions won't survive a restart — rather than ever
    # falling open. Set it for a stable login across restarts.
    chat_session_secret: str | None = Field(default=None)
    chat_session_ttl_seconds: int = Field(default=43200)
    # Cookies are Secure by default; only disable for a direct http://<ip>:8765
    # deployment with no TLS in front, which is strictly less safe.
    chat_cookie_secure: bool = Field(default=True)
    # Comma-separated allowed Origin values for POST /chat/api/send. Unset skips
    # the check (SameSite=Lax + the JSON content-type requirement still apply).
    chat_allowed_origins: str = Field(default="")
    # Static-password fallback. Ignored when Authentik OIDC is configured below —
    # OIDC always takes precedence when both are set.
    chat_password: str | None = Field(default=None)
    # Authentik OIDC (or any OIDC provider) — takes precedence over CHAT_PASSWORD
    # when set. All four of issuer/client_id/client_secret/redirect_url are
    # required together. CHAT_OIDC_REDIRECT_URL must be the exact absolute URL
    # registered on the provider — never derived from the request Host header.
    chat_oidc_issuer: str | None = Field(default=None)
    chat_oidc_client_id: str | None = Field(default=None)
    chat_oidc_client_secret: str | None = Field(default=None)
    chat_oidc_redirect_url: str | None = Field(default=None)
    chat_oidc_scopes: str = Field(default="openid profile email")
    # Comma-separated group names; empty (the default) means any user who
    # successfully authenticates with the IdP is allowed — unlike
    # PROPOSAL_COMMENT_ALLOWED_USERS, this gates an *already-authenticated*
    # principal rather than an anonymous public commenter, so failing open
    # here means "no extra restriction", not "no auth". Set one or more
    # group names to require membership in at least one of them.
    chat_oidc_allowed_groups: str = Field(default="")

    log_level: str = Field(default="INFO")


def get_settings() -> Settings:
    """Build a `Settings` instance from the current environment."""
    return Settings()
