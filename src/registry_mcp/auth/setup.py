"""Turn the `MCP_AUTH_*` settings into what FastMCP and the HTTP app need.

`build_auth` runs before the server exists and fails closed: auth switched on
but missing a setting, or on a transport this package doesn't gate, stops the
server from starting rather than letting it start open. `secure_app` runs on
the built Starlette app: it swaps in our protected-resource metadata routes and
adds the group gate after the SDK's authentication middleware.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import BaseRoute, Route

from registry_mcp.auth.middleware import RequireGroupMiddleware
from registry_mcp.auth.routes import (
    WELL_KNOWN,
    metadata_url,
    protected_resource_routes,
)
from registry_mcp.auth.verifier import AuthentikTokenVerifier
from registry_mcp.config import Settings
from registry_mcp.logging.events import get_logger

_log = get_logger("auth.setup")

_REQUIRED = (
    "mcp_auth_issuer_url",
    "mcp_auth_audience",
    "mcp_auth_required_group",
    "mcp_auth_resource_url",
)
_HTTPS_URLS = ("mcp_auth_issuer_url", "mcp_auth_resource_url", "mcp_auth_jwks_url")


class AuthConfigError(ValueError):
    """MCP_AUTH_ENABLED is on but the server can't enforce it; names settings, never values."""


@dataclass(frozen=True)
class McpAuth:
    settings: AuthSettings
    verifier: AuthentikTokenVerifier
    group: str
    resource_url: str
    issuer_url: str
    scopes: list[str]


def build_auth(settings: Settings) -> McpAuth | None:
    """None when auth is off. Raises `AuthConfigError` when it's on but unusable."""
    if not settings.mcp_auth_enabled:
        if settings.mcp_transport != "stdio":
            _log.warning(
                "mcp_auth_disabled",
                detail="MCP_AUTH_ENABLED is off: /mcp answers anyone who can reach it",
            )
        return None
    missing = [name.upper() for name in _REQUIRED if not getattr(settings, name)]
    if missing:
        raise AuthConfigError(f"MCP_AUTH_ENABLED is on but missing {', '.join(missing)}")
    not_https = [
        name.upper()
        for name in _HTTPS_URLS
        if getattr(settings, name) and not getattr(settings, name).startswith("https://")
    ]
    if not_https:
        raise AuthConfigError(f"{', '.join(not_https)} must be an https:// URL")
    if settings.mcp_transport == "sse":
        raise AuthConfigError("MCP_AUTH_ENABLED needs MCP_TRANSPORT=streamable-http, not sse")
    issuer = settings.mcp_auth_issuer_url
    resource = settings.mcp_auth_resource_url
    return McpAuth(
        settings=AuthSettings(
            issuer_url=AnyHttpUrl(issuer),
            resource_server_url=AnyHttpUrl(resource),
            required_scopes=None,
        ),
        verifier=AuthentikTokenVerifier(
            issuer=issuer,
            audience=settings.mcp_auth_audience,
            jwks_url=settings.mcp_auth_jwks_url,
            cache_seconds=settings.mcp_auth_jwks_cache_seconds,
        ),
        group=settings.mcp_auth_required_group,
        resource_url=resource,
        issuer_url=issuer,
        scopes=[s for s in re.split(r"[\s,]+", settings.mcp_auth_scopes) if s],
    )


def secure_app(app: Starlette, auth: McpAuth, *, mcp_path: str) -> Starlette:
    """Replace the SDK's metadata route with ours and add the group gate.

    Must run before the app serves its first request: Starlette builds its
    middleware stack then, and our gate has to sit after the SDK's
    `AuthContextMiddleware` (which follows its `AuthenticationMiddleware`).
    """
    kept: list[BaseRoute] = [
        route
        for route in app.router.routes
        if not (isinstance(route, Route) and route.path.startswith(WELL_KNOWN))
    ]
    app.router.routes = [
        *protected_resource_routes(
            resource_url=auth.resource_url, issuer_url=auth.issuer_url, scopes=auth.scopes
        ),
        *kept,
    ]
    if not any(m.cls is AuthContextMiddleware for m in app.user_middleware):
        raise AuthConfigError("the MCP SDK did not install its auth middleware")
    app.user_middleware.append(
        Middleware(
            RequireGroupMiddleware,
            group=auth.group,
            path=mcp_path,
            resource_metadata_url=metadata_url(auth.resource_url),
        )
    )
    return app
