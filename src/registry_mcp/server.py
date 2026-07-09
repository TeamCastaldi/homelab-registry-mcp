"""FastMCP entry point for the homelab registry server."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

from registry_mcp import __version__
from registry_mcp.config import Settings, get_settings
from registry_mcp.discovery.engine import DiscoveryEngine, build_sources
from registry_mcp.discovery.scheduler import build_scheduler
from registry_mcp.dspy import Reasoner, build_reasoner
from registry_mcp.hardware import HardwareStore
from registry_mcp.health import check_health
from registry_mcp.integrations.authentik import register_authentik_tools
from registry_mcp.integrations.traefik import register_traefik_tools
from registry_mcp.logging import configure_logging, get_logger
from registry_mcp.proposal import PatchGenerator, ProposalEngine, ProposalStore
from registry_mcp.providers.git import build_git_provider
from registry_mcp.providers.notification import build_notification_provider
from registry_mcp.registry import RegistryStore
from registry_mcp.tools import (
    register_discovery_tools,
    register_event_tools,
    register_hardware_tools,
    register_linking_tools,
    register_proposal_tools,
    register_registry_tools,
    register_secrets_tools,
)


def build_proposal_engine(
    settings: Settings, store: RegistryStore, reasoner: Reasoner
) -> tuple[ProposalEngine, ProposalStore]:
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
    )
    return engine, proposals


def build_server(settings: Settings | None = None) -> FastMCP:
    """Construct the FastMCP server and register its tools."""
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
    proposal_engine, proposal_store = build_proposal_engine(settings, store, reasoner)
    engine = DiscoveryEngine(
        store,
        build_sources(settings),
        stale_threshold=settings.discovery_stale_after_misses,
        reasoner=reasoner,
        on_pass_complete=(proposal_engine.after_discovery if proposal_engine.configured else None),
    )

    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[dict]:
        # WORKAROUND (FastMCP ≤ 1.27.1): streamable_http_app() hardcodes its
        # Starlette lifespan to session_manager.run(), so this block is never
        # called on the streamable-http transport. Scheduler startup lives in
        # main() instead (see _streamable_with_scheduler).
        #
        # TO REVERT when fixed upstream: remove _streamable_with_scheduler from
        # main(), restore the scheduler start/stop logic here, and delete this
        # comment. Track: https://github.com/modelcontextprotocol/python-sdk
        yield {}

    mcp = FastMCP(
        name="homelab-registry-mcp",
        host=settings.mcp_host,
        port=settings.mcp_port,
        lifespan=lifespan,
    )

    register_registry_tools(mcp, store)
    register_event_tools(mcp, store)
    register_traefik_tools(mcp, settings)
    register_authentik_tools(mcp, settings, reasoner=reasoner)
    register_discovery_tools(mcp, engine)
    register_linking_tools(mcp, store, settings, hardware_store=hardware_store)
    register_hardware_tools(mcp, store, hardware_store)
    register_proposal_tools(
        mcp, proposal_engine, proposal_store, engine, store, read_only=read_only
    )
    register_secrets_tools(mcp, settings, read_only=read_only)

    @mcp.tool()
    def health() -> dict[str, str]:
        """Report server liveness and version. Returns OK when the server is reachable."""
        return {
            "status": "ok",
            "service": "homelab-registry-mcp",
            "version": __version__,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    @mcp.tool()
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

    return mcp


def main() -> None:
    """Console entry point: build the server and run it on the configured transport."""
    settings = get_settings()
    configure_logging(settings)
    get_logger("registry.server").info("starting", transport=settings.mcp_transport)
    server = build_server(settings)

    # WORKAROUND (FastMCP ≤ 1.27.1): streamable_http_app() hardcodes its Starlette
    # lifespan to `lambda app: self.session_manager.run()`, silently ignoring any
    # custom lifespan passed to FastMCP(). The custom lifespan only fires on the
    # stdio transport. Work around this by monkey-patching run_streamable_http_async
    # so the scheduler starts inside the correct asyncio event loop.
    #
    # TO REVERT when fixed upstream: delete _streamable_with_scheduler and the
    # monkey-patch line, restore scheduler start/stop in the lifespan block in
    # build_server(), and delete this comment.
    _orig_streamable = server.run_streamable_http_async

    async def _streamable_with_scheduler() -> None:
        _store = RegistryStore(settings.registry_db_path)
        _reasoner = build_reasoner(settings)
        _proposal_engine, _ = build_proposal_engine(settings, _store, _reasoner)
        _engine = DiscoveryEngine(
            _store,
            build_sources(settings),
            stale_threshold=settings.discovery_stale_after_misses,
            reasoner=_reasoner,
            on_pass_complete=(
                _proposal_engine.after_discovery if _proposal_engine.configured else None
            ),
        )
        scheduler = build_scheduler(_engine, settings) if _engine.sources else None
        if scheduler is not None:
            scheduler.start()
            get_logger("discovery.scheduler").info(
                "scheduler_started", sources=[s.value for s in _engine.sources]
            )
        try:
            await _orig_streamable()
        finally:
            if scheduler is not None:
                scheduler.shutdown(wait=False)

    server.run_streamable_http_async = _streamable_with_scheduler  # type: ignore[method-assign]
    server.run(transport=settings.mcp_transport)


if __name__ == "__main__":
    main()
