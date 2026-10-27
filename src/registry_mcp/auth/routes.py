"""Protected-resource metadata (RFC 9728): where a client learns to sign in.

The SDK serves this document only at the path-suffixed location
(`/.well-known/oauth-protected-resource/mcp`) and with no `scopes_supported`.
Claude requests exactly the scopes that list names when the 401 carries none,
so without it the token would lack Authentik's `profile` scope, and with it the
`groups` claim, and every sign-in would end in 403. These routes replace the
SDK's and also answer at the bare path, which RFC 9728 clients without a path
component and the spec's fallback probe both try.

The document holds only public values: the resource URL, the issuer, scopes.
"""

from __future__ import annotations

from mcp.server.auth.handlers.metadata import ProtectedResourceMetadataHandler
from mcp.server.auth.routes import build_resource_metadata_url, cors_middleware
from mcp.shared.auth import ProtectedResourceMetadata
from pydantic import AnyHttpUrl
from starlette.routing import Route

WELL_KNOWN = "/.well-known/oauth-protected-resource"


def metadata_paths(resource_url: str) -> tuple[str, ...]:
    """The bare path, then the RFC 9728 path-suffixed one (if it differs)."""
    suffixed = build_resource_metadata_url(AnyHttpUrl(resource_url)).path or WELL_KNOWN
    return (WELL_KNOWN,) if suffixed == WELL_KNOWN else (WELL_KNOWN, suffixed)


def metadata_url(resource_url: str) -> str:
    """The URL a 401's `resource_metadata` points at: the path-suffixed one."""
    return str(build_resource_metadata_url(AnyHttpUrl(resource_url)))


def protected_resource_routes(
    *, resource_url: str, issuer_url: str, scopes: list[str]
) -> list[Route]:
    metadata = ProtectedResourceMetadata(
        resource=AnyHttpUrl(resource_url),
        authorization_servers=[AnyHttpUrl(issuer_url)],
        scopes_supported=scopes or None,
        resource_name="homelab-registry-mcp",
    )
    handler = ProtectedResourceMetadataHandler(metadata)
    return [
        Route(
            path,
            endpoint=cors_middleware(handler.handle, ["GET", "OPTIONS"]),
            methods=["GET", "OPTIONS"],
        )
        for path in metadata_paths(resource_url)
    ]
