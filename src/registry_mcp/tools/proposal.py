"""MCP tools for the proposal layer (Phase 8)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

if TYPE_CHECKING:
    from registry_mcp.discovery.engine import DiscoveryEngine
    from registry_mcp.proposal import ProposalEngine, ProposalStore
    from registry_mcp.registry import RegistryStore


def register_proposal_tools(
    mcp: FastMCP,
    engine: ProposalEngine,
    proposals: ProposalStore,
    discovery_engine: DiscoveryEngine,
    store: RegistryStore,
) -> None:
    """Register the `proposal_*` write-path tools.

    All tools degrade gracefully when the write path is not configured: the
    create/cancel paths return a structured error rather than raising.
    """

    @mcp.tool()
    async def proposal_create(service_id: str) -> dict[str, Any]:
        """Open a remediation pull request for a flagged service.

        Reads the target file from Git, asks the reasoning layer for a patch,
        and (unless `PROPOSAL_DRY_RUN=true`) opens a PR. Low-confidence patches
        are recorded as rejected and flagged for manual review instead.
        """
        return await engine.create_for_service(service_id)

    @mcp.tool()
    def proposal_list_open() -> dict[str, Any]:
        """List open proposals (PRs this server has opened), under `items`."""
        return {"items": [p.model_dump(mode="json") for p in proposals.list_open()]}

    @mcp.tool()
    def proposal_get(proposal_id: str) -> dict[str, Any]:
        """Full detail on one proposal, including the proposed file content."""
        proposal = proposals.get(proposal_id)
        if proposal is None:
            return {"error": f"no proposal found for {proposal_id!r}"}
        return proposal.model_dump(mode="json")

    @mcp.tool()
    async def proposal_cancel(proposal_id: str) -> dict[str, Any]:
        """Close a proposal's PR without merging and mark it cancelled."""
        return await engine.cancel(proposal_id)

    @mcp.tool()
    async def proposal_verify(service_id: str) -> dict[str, Any]:
        """Force a discovery pass and check whether a service's conflict cleared.

        Runs discovery across all sources, sweeps open proposals for
        verification, and reports the service's current conflict state.
        """
        await discovery_engine.run_all()
        await engine.sweep_verifications()
        service = store.get_service(service_id)
        if service is None:
            return {"error": f"no service found for {service_id!r}"}
        return {
            "service": service.name,
            "auth_mode_conflict": service.auth_mode_conflict,
            "verified": not service.auth_mode_conflict,
        }
