"""PatchMon integration: a read-only client for its scoped Integration API.

Read-only by convention (CLAUDE.md's "Upstream APIs are read-only" rule and
ADR-020's 2026-09-27 amendment): the host `DELETE` endpoint is never called,
and the credential needs only `host:get`.
"""

from registry_mcp.integrations.patchmon.client import (
    PatchmonClient,
    PatchmonError,
    build_patchmon_client,
    is_host_id,
)

__all__ = ["PatchmonClient", "PatchmonError", "build_patchmon_client", "is_host_id"]
