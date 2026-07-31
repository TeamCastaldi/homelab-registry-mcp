"""FastMCP entry point for the homelab registry server."""

from __future__ import annotations

import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from registry_mcp import __version__
from registry_mcp.adoption import AdoptionDraftStore
from registry_mcp.config import Settings, get_settings
from registry_mcp.discovery.engine import DiscoveryEngine, build_sources
from registry_mcp.discovery.scheduler import build_scheduler
from registry_mcp.dspy import Reasoner, build_reasoner
from registry_mcp.hardware import HardwareStore
from registry_mcp.health import check_health
from registry_mcp.integrations.authentik import register_authentik_tools
from registry_mcp.integrations.traefik import register_traefik_tools
from registry_mcp.logging import configure_logging, get_logger
from registry_mcp.proposal import AdoptionGenerator, PatchGenerator, ProposalEngine, ProposalStore
from registry_mcp.providers.git import GitProvider, build_git_provider
from registry_mcp.providers.notification import build_notification_provider
from registry_mcp.registry import RegistryStore
from registry_mcp.tools import (
    register_adoption_tools,
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
    )
    return engine, proposals, git


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
    proposal_engine, proposal_store, git_provider = build_proposal_engine(settings, store, reasoner)
    adoption_store = AdoptionDraftStore(store.engine)
    # Pending drafts hold captured live secret values in the (non-git-crypt)
    # registry SQLite until the operator answers — sweep anything left over
    # from a previous run past its TTL on every startup.
    adoption_store.purge_expired()
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
    register_hardware_tools(mcp, store, hardware_store, settings, read_only=read_only)
    register_proposal_tools(
        mcp, proposal_engine, proposal_store, engine, store, read_only=read_only
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

    @mcp.custom_route(settings.wud_webhook_path, methods=["POST"])
    async def wud_webhook(request: Request) -> Response:
        """WUD (What's Up Docker) HTTP trigger receiver (ADR-005).

        Turns an upstream-image-update notification into an `image_update`
        proposal via the proposal engine. Fail-closed on both feature flag and
        shared secret; a non-2xx response makes WUD retry, so a payload we
        can't act on (unknown container) still returns 200.

        NOTE: WUD's HTTP trigger payload field names are read defensively
        (multiple fallbacks) since this was written without a live WUD
        instance to verify against — confirm against the deployed trigger
        config and adjust the field lookups below if they don't match.
        """
        if not settings.wud_webhook_enabled:
            return JSONResponse({"error": "wud webhook not enabled"}, status_code=404)
        if read_only:
            return JSONResponse(
                {"error": "server is in read-only mode (startup health check failed)"},
                status_code=403,
            )
        secret = settings.wud_webhook_secret
        provided = request.headers.get("authorization", "")
        if provided.lower().startswith("bearer "):
            provided = provided[len("bearer ") :]
        if not secret or not hmac.compare_digest(provided, secret):
            return JSONResponse({"error": "unauthorized"}, status_code=403)

        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)

        container = payload.get("container") or {}
        image = container.get("image") or {}
        name = container.get("name") or container.get("id") or ""
        image_name = image.get("name") or ""
        current_tag = (image.get("tag") or {}).get("value") or ""
        update = payload.get("updateKind") or payload.get("update") or {}
        new_tag = update.get("remoteValue") or update.get("new_tag") or ""

        if not name:
            return JSONResponse({"error": "missing container name in payload"}, status_code=400)
        if not image_name or not new_tag:
            return JSONResponse(
                {"error": "missing image name or new tag in payload"}, status_code=400
            )

        service = store.get_service(name)
        if service is None:
            return JSONResponse({"skipped": "no matching service", "container": name})

        result = await proposal_engine.create_for_image_update(
            service.id, image=image_name, current_tag=current_tag, new_tag=new_tag
        )
        return JSONResponse(result)

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
        _proposal_engine, _, _ = build_proposal_engine(settings, _store, _reasoner)
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

        # Comment polling (Phase 3): never scheduled when the write path isn't
        # configured, or when the startup health check failed (read-only mode).
        _read_only = not check_health(settings).healthy
        if (
            settings.proposal_comment_poll_enabled
            and _proposal_engine.configured
            and not _read_only
        ):
            if scheduler is None:
                scheduler = AsyncIOScheduler()
            scheduler.add_job(
                _proposal_engine.poll_pr_comments,
                "interval",
                seconds=settings.proposal_comment_poll_interval_seconds,
                id="proposal-comment-poll",
                replace_existing=True,
            )
            get_logger("proposal.engine").info(
                "comment_poll_scheduled",
                interval_seconds=settings.proposal_comment_poll_interval_seconds,
            )

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
