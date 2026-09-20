"""Infisical MCP tool (ADR-016, whole-project mode ADR-017).

Read-only by design: `infisical_status` never returns a secret's value,
only which keys exist at the configured project/environment/secret path. If
the client ever detects a live value despite requesting none (see
`InfisicalSecretValueLeakedError`), the tool fails closed and fires an
urgent alert via the configured `NotificationProvider` recommending the
affected key be rotated immediately -- it never surfaces the value itself,
including in logs or the notification body.

When `INFISICAL_RECURSIVE_SCAN=true`, the tool walks the folder tree rooted
at `INFISICAL_SECRET_PATH` instead of reading just that one folder, grouping
key names by the exact folder each lives in (`secrets_by_path`) -- still
never a value. A leak anywhere in the tree still fails the whole call
closed, same as single-folder mode.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from registry_mcp.config import Settings
from registry_mcp.integrations.infisical.client import (
    InfisicalClient,
    InfisicalError,
    InfisicalSecretValueLeakedError,
)
from registry_mcp.logging import get_logger
from registry_mcp.providers.notification import NotificationProvider

_READ_ONLY = ToolAnnotations(readOnlyHint=True)
_log = get_logger("integrations.infisical")


def register_infisical_tools(
    mcp: FastMCP, settings: Settings, notifier: NotificationProvider
) -> None:
    """Register the read-only `infisical_status` tool."""

    def _client() -> InfisicalClient | None:
        if not settings.infisical_enabled:
            return None
        if not (
            settings.infisical_base_url
            and settings.infisical_client_id
            and settings.infisical_client_secret
            and settings.infisical_project_id
            and settings.infisical_environment
        ):
            return None
        return InfisicalClient(
            settings.infisical_base_url,
            settings.infisical_client_id,
            settings.infisical_client_secret,
        )

    @mcp.tool(annotations=_READ_ONLY)
    async def infisical_status() -> dict[str, Any]:
        """List the secret key names configured at INFISICAL_PROJECT_ID's
        INFISICAL_ENVIRONMENT/INFISICAL_SECRET_PATH, under `keys` -- never
        values. Useful for checking whether a setting this server expects
        (e.g. ANSIBLE_INVENTORY_PATH) actually has an entry in Infisical.

        With INFISICAL_RECURSIVE_SCAN=true, walks every folder under
        INFISICAL_SECRET_PATH instead, returning `secrets_by_path` (key
        names grouped by the folder each lives in) and, if any folders
        couldn't be read, `inaccessible_paths` -- still never a value."""
        client = _client()
        if client is None:
            return {
                "error": "Infisical integration is not enabled or fully configured "
                "(INFISICAL_ENABLED, INFISICAL_BASE_URL, INFISICAL_CLIENT_ID, "
                "INFISICAL_CLIENT_SECRET, INFISICAL_PROJECT_ID, INFISICAL_ENVIRONMENT)"
            }
        secret_path = settings.infisical_secret_path or "/"
        try:
            if settings.infisical_recursive_scan:
                by_path, inaccessible = await client.list_secret_tree(
                    settings.infisical_project_id,  # type: ignore[arg-type]
                    settings.infisical_environment,  # type: ignore[arg-type]
                    secret_path,
                    max_folders=settings.infisical_max_folders,
                )
                result: dict[str, Any] = {"secrets_by_path": by_path}
                if inaccessible:
                    result["inaccessible_paths"] = inaccessible
                return result
            keys = await client.list_secret_keys(
                settings.infisical_project_id,  # type: ignore[arg-type]
                settings.infisical_environment,  # type: ignore[arg-type]
                secret_path,
            )
        except InfisicalSecretValueLeakedError as exc:
            location = f"{exc.path.rstrip('/')}/{exc.key}" if exc.path else exc.key
            _log.error(
                "infisical_secret_value_leaked",
                affected_env_var_name=exc.key,
                affected_path=exc.path,
                project_id=settings.infisical_project_id,
                environment=settings.infisical_environment,
                secret_path=secret_path,
            )
            await notifier.send(
                title="Rotate an Infisical secret immediately",
                body=(
                    "registry-mcp's read-only Infisical check requested "
                    "viewSecretValue=false, but Infisical returned a live "
                    f"value for {location!r} "
                    f"({settings.infisical_project_id}/{settings.infisical_environment}). "
                    "Its value reached registry-mcp's process "
                    "unexpectedly -- rotate this specific secret in Infisical "
                    "now."
                ),
            )
            return {
                "error": "Infisical returned a live secret value despite requesting "
                "none. The affected key has been logged and an alert sent "
                "recommending immediate rotation; no data is returned here."
            }
        except InfisicalError as exc:
            return {"error": str(exc)}
        return {"keys": keys}
