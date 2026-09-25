"""FastMCP entry point for the homelab registry server."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import anyio
import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.applications import Starlette

from registry_mcp import __version__
from registry_mcp.adoption import AdoptionDraftStore
from registry_mcp.config import Settings, get_settings
from registry_mcp.config_report import build_report, process_environ
from registry_mcp.deletion import DeletionGateStore
from registry_mcp.discovery.engine import DiscoveryEngine, build_sources
from registry_mcp.discovery.scheduler import build_scheduler
from registry_mcp.dspy import Reasoner, build_reasoner
from registry_mcp.hardware import HardwareStore
from registry_mcp.health import check_health
from registry_mcp.integrations.authentik import register_authentik_tools
from registry_mcp.integrations.dockhand import register_dockhand_tools
from registry_mcp.integrations.docs import register_docs_tools
from registry_mcp.integrations.infisical import register_infisical_tools
from registry_mcp.integrations.traefik import register_traefik_tools
from registry_mcp.inventory import InventoryGateStore
from registry_mcp.logging import configure_logging, get_logger, install_tool_call_logging
from registry_mcp.normalization import NormalizationEngine, NormalizationGenerator, schedule_trigger
from registry_mcp.normalization.rules import network_names
from registry_mcp.proposal import AdoptionGenerator, PatchGenerator, ProposalEngine, ProposalStore
from registry_mcp.providers.git import GitProvider, build_git_provider
from registry_mcp.providers.notification import build_notification_provider
from registry_mcp.registry import RegistryStore
from registry_mcp.service_deploy import ComposeGenerator
from registry_mcp.tools import (
    register_adoption_tools,
    register_ansible_inventory_tools,
    register_discovery_tools,
    register_event_tools,
    register_hardware_tools,
    register_intake_tools,
    register_linking_tools,
    register_proposal_tools,
    register_registry_tools,
    register_secrets_tools,
    register_service_deploy_tools,
)
from registry_mcp.webhooks import register_webhook_routes

# Tools that only touch this server's own state: its SQLite DB, the local
# homelab clone (secrets_*), and the Ansible inventory file. Every other tool
# reaches an external system (Traefik, Authentik, Dockhand, Infisical, a Git
# host, SSH, a cloned repo, an LLM provider) and is marked open-world — which
# is also the MCP default when the hint is absent, hence this explicit list.
_CLOSED_WORLD_TOOLS = frozenset(
    {
        "registry_add_service",
        "registry_get_service",
        "registry_list_services",
        "registry_update_service",
        "registry_delete_service",
        "registry_delete_service_confirm",
        "events_list_discoveries",
        "events_list_changes",
        "events_get_for_service",
        "discovery_status",
        "discovery_list_stale",
        "service_link_authentik",
        "hardware-add-node",
        "hardware-get-node",
        "hardware-list-nodes",
        "hardware-update-node",
        "hardware-delete-node",
        "hardware-delete-node-confirm",
        "hardware-link-service",
        "hardware-node-services",
        "hardware-list-unconfirmed",
        "hardware-list-stale",
        "hardware-capacity-summary",
        "hardware-discovery-status",
        "ansible-inventory-sync-node",
        "ansible-inventory-sync-node-confirm",
        "proposal_list_open",
        "proposal_get",
        "proposal_adopt_service_cancel",
        "proposal_adopt_service_get",
        "secrets_status",
        "secrets_encrypt",
        "secrets_decrypt",
        "secrets_add",
        "secrets_rotate",
        "secrets_list_keys",
        "health",
        "system_health_check",
        "config_status",
    }
)


def _apply_open_world_hints(mcp: FastMCP) -> None:
    """Set `openWorldHint` on every registered tool, keeping its other hints."""
    for tool in mcp._tool_manager.list_tools():  # noqa: SLF001
        annotations = tool.annotations or ToolAnnotations()
        tool.annotations = annotations.model_copy(
            update={"openWorldHint": tool.name not in _CLOSED_WORLD_TOOLS}
        )


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def build_transport_security(settings: Settings) -> TransportSecuritySettings:
    """DNS-rebinding protection for /mcp, on for every bind address.

    FastMCP only enables it on its own for a 127.0.0.1/localhost bind, so the
    default 0.0.0.0 bind otherwise accepts any Host and Origin — letting a
    browser page on the LAN drive every tool via DNS rebinding. Custom routes
    (the Dockhand webhook) sit outside this check and keep their own auth.
    """
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_csv(settings.mcp_allowed_hosts),
        allowed_origins=_csv(settings.mcp_allowed_origins),
    )


@dataclass(frozen=True)
class Runtime:
    """The one object graph a process runs: the MCP tools, the webhook, and the
    scheduler all share these engines (and so one Reasoner and one DB engine)."""

    settings: Settings
    read_only: bool
    discovery: DiscoveryEngine
    proposals: ProposalEngine
    normalization: NormalizationEngine


def build_proposal_engine(
    settings: Settings, store: RegistryStore, reasoner: Reasoner, *, read_only: bool = False
) -> tuple[ProposalEngine, ProposalStore, GitProvider | None]:
    """Assemble the proposal engine and its store from configuration."""
    proposals = ProposalStore(store.engine)
    git = build_git_provider(settings)
    engine = ProposalEngine(
        settings=settings,
        store=store,
        proposals=proposals,
        generator=PatchGenerator(
            reasoner,
            threshold=settings.proposal_confidence_threshold,
            git=git,
            repo=settings.git_repo,
            base=settings.git_base_branch,
        ),
        notifier=build_notification_provider(settings),
        git=git,
        read_only=read_only,
    )
    return engine, proposals, git


def build_normalization_engine(
    settings: Settings, store: RegistryStore, reasoner: Reasoner, git: GitProvider | None
) -> NormalizationEngine:
    """Assemble the normalization engine, reusing the proposal store (same
    `Proposal` table, `finding_type=normalization`) and Git provider — kept
    as its own engine, never merged into `ProposalEngine`, so a normalization
    PR can never bundle a security remediation."""
    proposals = ProposalStore(store.engine)
    return NormalizationEngine(
        settings=settings,
        proposals=proposals,
        generator=NormalizationGenerator(
            reasoner, threshold=settings.proposal_confidence_threshold
        ),
        notifier=build_notification_provider(settings),
        git=git,
    )


def build_server(settings: Settings | None = None) -> FastMCP:
    """Construct the FastMCP server and register its tools."""
    return build_app(settings)[0]


def build_app(settings: Settings | None = None) -> tuple[FastMCP, Runtime]:
    """Construct the FastMCP server and the runtime its scheduler drives.

    The single composition root: `main()` schedules jobs against the same
    engines the tools use, rather than building a second, parallel set.
    """
    settings = settings or get_settings()

    store = RegistryStore(settings.registry_db_path)
    store.purge_old_events(settings.event_retention_days)
    hardware_store = HardwareStore(store.engine)
    health = check_health(settings)
    read_only = not health.healthy
    if read_only:
        get_logger("registry.server").warning(
            "starting_read_only",
            failed_checks=[c.name for c in health.checks if not c.ok],
        )
    reasoner = build_reasoner(settings)
    proposal_engine, proposal_store, git_provider = build_proposal_engine(
        settings, store, reasoner, read_only=read_only
    )
    normalization_engine = build_normalization_engine(settings, store, reasoner, git_provider)
    adoption_store = AdoptionDraftStore(store.engine)
    # Pending drafts hold captured live secret values in the (non-git-crypt)
    # registry SQLite until the operator answers — sweep anything left over
    # from a previous run past its TTL on every startup.
    adoption_store.purge_expired()
    deletion_gate = DeletionGateStore(store.engine)
    deletion_gate.purge_expired()
    inventory_gate = InventoryGateStore(store.engine)
    inventory_gate.purge_expired()
    adoption_generator = AdoptionGenerator(
        reasoner, threshold=settings.proposal_confidence_threshold
    )
    engine = DiscoveryEngine(
        store,
        build_sources(settings),
        stale_threshold=settings.discovery_stale_after_misses,
        reasoner=reasoner,
        on_pass_complete=(proposal_engine.after_discovery if proposal_engine.configured else None),
    )

    mcp = FastMCP(
        name="homelab-registry-mcp",
        host=settings.mcp_host,
        port=settings.mcp_port,
        transport_security=build_transport_security(settings),
    )
    install_tool_call_logging(mcp)

    register_registry_tools(mcp, store, deletion_gate, settings)
    register_event_tools(mcp, store)
    register_traefik_tools(mcp, settings)
    register_authentik_tools(mcp, settings, reasoner=reasoner)
    register_dockhand_tools(mcp, settings)
    register_infisical_tools(mcp, settings, build_notification_provider(settings))
    register_docs_tools(mcp, settings)
    register_discovery_tools(mcp, engine)
    register_linking_tools(mcp, store, settings, hardware_store=hardware_store)
    register_hardware_tools(
        mcp, store, hardware_store, settings, deletion_gate, read_only=read_only
    )
    register_ansible_inventory_tools(
        mcp, hardware_store, inventory_gate, settings, read_only=read_only
    )
    register_proposal_tools(
        mcp,
        proposal_engine,
        proposal_store,
        engine,
        store,
        normalization_engine,
        read_only=read_only,
    )
    register_secrets_tools(mcp, settings, read_only=read_only)
    register_adoption_tools(
        mcp,
        settings,
        store,
        hardware_store,
        adoption_store,
        adoption_generator,
        git_provider,
        proposal_store,
        build_notification_provider(settings),
        read_only=read_only,
    )
    register_webhook_routes(mcp, settings, store, proposal_engine, read_only=read_only)
    register_intake_tools(mcp, settings, reasoner)
    register_service_deploy_tools(
        mcp,
        settings,
        reasoner,
        ComposeGenerator(
            reasoner,
            threshold=settings.service_deploy_confidence_threshold,
            git=git_provider,
            repo=settings.git_repo,
            base=settings.git_base_branch,
            conventions_path=settings.service_deploy_conventions_path,
            shared_networks=network_names(settings.normalization_shared_networks),
        ),
    )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def health() -> dict[str, str]:
        """Report server liveness and version. Returns OK when the server is reachable."""
        return {
            "status": "ok",
            "service": "homelab-registry-mcp",
            "version": __version__,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def system_health_check() -> dict[str, Any]:
        """Diagnose control-plane provisioning: Git repo, ansible.cfg, and SSH key.

        Re-evaluates the checks live, but the read-only mode they gate is fixed
        at server startup — restart the server after fixing an issue to leave
        read-only mode.
        """
        current = check_health(settings)
        return {
            "mode": "read-only" if read_only else "read-write",
            **current.to_dict(),
        }

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def config_status() -> dict[str, Any]:
        """Report this server's configuration by setting name, never by value.

        Lists problems (a feature that's on but missing a setting it needs,
        and other known pitfalls), environment keys that match no setting but
        look meant for one (a typo, or a setting whose feature was removed),
        the features that are on, and which settings are set. Also lists the
        set settings that do nothing where they are: set to their default,
        set only for features that are off, or set to an empty value. Run it
        after an upgrade to find what still needs adding to, or can be removed
        from, the deployment's secrets store.
        """
        return build_report(settings, process_environ(settings))

    _apply_open_world_hints(mcp)
    return mcp, Runtime(
        settings=settings,
        read_only=read_only,
        discovery=engine,
        proposals=proposal_engine,
        normalization=normalization_engine,
    )


def build_runtime_scheduler(runtime: Runtime) -> AsyncIOScheduler | None:
    """Every scheduled job, built against the same engines the tools use.
    Returns None when there is nothing to schedule."""
    settings = runtime.settings
    scheduler = build_scheduler(runtime.discovery, settings) if runtime.discovery.sources else None

    # Comment polling (Phase 3): never scheduled when the write path isn't
    # configured, or when the startup health check failed (read-only mode).
    if (
        settings.proposal_comment_poll_enabled
        and runtime.proposals.configured
        and not runtime.read_only
    ):
        scheduler = scheduler or AsyncIOScheduler()
        scheduler.add_job(
            runtime.proposals.poll_pr_comments,
            "interval",
            seconds=settings.proposal_comment_poll_interval_seconds,
            id="proposal-comment-poll",
            replace_existing=True,
        )
        get_logger("proposal.engine").info(
            "comment_poll_scheduled",
            interval_seconds=settings.proposal_comment_poll_interval_seconds,
        )

    # Normalization sweep: same three-part gate as comment polling — opt-in
    # flag, write path configured, and never in read-only mode.
    if (
        settings.normalization_enabled
        and runtime.normalization.configured
        and not runtime.read_only
    ):
        scheduler = scheduler or AsyncIOScheduler()
        trigger = schedule_trigger(settings.normalization_schedule)
        scheduler.add_job(
            runtime.normalization.run_sweep,
            trigger,
            id="normalization-sweep",
            replace_existing=True,
            # A run due while the server was busy still happens within the hour.
            misfire_grace_time=3600,
            coalesce=True,
        )
        next_run = trigger.get_next_fire_time(None, datetime.now(trigger.timezone))
        get_logger("normalization.engine").info(
            "normalization_sweep_scheduled",
            schedule=settings.normalization_schedule,
            next_run=next_run.isoformat() if next_run else None,
        )
    return scheduler


@asynccontextmanager
async def _scheduled(runtime: Runtime, scheduler: AsyncIOScheduler | None) -> AsyncIterator[None]:
    """Run the scheduler for the life of the block. AsyncIOScheduler binds to the
    running loop in start(), so this must be entered inside the transport's own
    event loop — never before it."""
    if scheduler is not None:
        scheduler.start()
        get_logger("discovery.scheduler").info(
            "scheduler_started", sources=[s.value for s in runtime.discovery.sources]
        )
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)


def http_app(server: FastMCP, runtime: Runtime, scheduler: AsyncIOScheduler | None) -> Starlette:
    """The streamable-http or SSE app, with the scheduler tied to its lifespan.

    The FastMCP `lifespan=` hook can't host the scheduler: the SDK enters it
    once per MCP session (inside the low-level server's run()), not once per
    process. This wraps — never replaces — the app's own lifespan, which for
    streamable-http runs the session manager.
    """
    if runtime.settings.mcp_transport == "streamable-http":
        app = server.streamable_http_app()
    else:
        app = server.sse_app()
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(asgi_app: Any) -> AsyncIterator[None]:
        async with _scheduled(runtime, scheduler), inner(asgi_app):
            yield

    app.router.lifespan_context = lifespan
    return app


async def _run_stdio(server: FastMCP, runtime: Runtime, scheduler: AsyncIOScheduler | None) -> None:
    async with _scheduled(runtime, scheduler):
        await server.run_stdio_async()


async def _run_http(server: FastMCP, app: Starlette) -> None:
    # Mirrors FastMCP.run_streamable_http_async/run_sse_async, minus building the app.
    config = uvicorn.Config(
        app,
        host=server.settings.host,
        port=server.settings.port,
        log_level=server.settings.log_level.lower(),
    )
    await uvicorn.Server(config).serve()


def serve(server: FastMCP, runtime: Runtime) -> None:
    """Run `server` on the configured transport with its scheduler — on every
    transport, not just streamable-http."""
    scheduler = build_runtime_scheduler(runtime)
    if runtime.settings.mcp_transport == "stdio":
        anyio.run(_run_stdio, server, runtime, scheduler)
        return
    anyio.run(_run_http, server, http_app(server, runtime, scheduler))


def main() -> None:
    """Console entry point: build the server and run it on the configured transport."""
    settings = get_settings()
    configure_logging(settings)
    get_logger("registry.server").info("starting", transport=settings.mcp_transport)
    server, runtime = build_app(settings)
    serve(server, runtime)


if __name__ == "__main__":
    main()
