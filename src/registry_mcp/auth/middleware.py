"""403 for a valid token from someone outside the required group.

The SDK authenticates every request (its `AuthenticationMiddleware` runs the
token verifier and sets `scope["user"]`) and its `RequireAuthMiddleware` answers
401 on `/mcp` when that failed. Its only 403 is for a missing *scope*, and a
group isn't a scope: faking one would advertise it in the metadata, and clients
would ask Authentik for a scope it doesn't have. So this middleware sits after
the SDK's authentication and before routing, and checks the `groups` claim
Authentik's `profile` scope mapping puts in the access token.

It acts only on an authenticated request to the protected path. Anything
unauthenticated passes through to the SDK's 401, and every other route
(webhooks, approval links, metadata, `/health`) is never touched.
"""

from __future__ import annotations

import json

from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from starlette.types import ASGIApp, Receive, Scope, Send

from registry_mcp.logging.events import get_logger

_log = get_logger("auth.middleware")


def in_group(user: AuthenticatedUser, group: str) -> bool:
    groups = (user.access_token.claims or {}).get("groups")
    return isinstance(groups, list) and group in groups


class RequireGroupMiddleware:
    """Starlette middleware; add it after the SDK's auth middleware."""

    def __init__(self, app: ASGIApp, *, group: str, path: str, resource_metadata_url: str) -> None:
        self.app = app
        self.group = group
        self.path = path.rstrip("/")
        self.resource_metadata_url = resource_metadata_url

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].rstrip("/") == self.path:
            user = scope.get("user")
            if isinstance(user, AuthenticatedUser) and not in_group(user, self.group):
                _log.info(
                    "mcp_token_forbidden", reason="not_in_group", subject=user.access_token.subject
                )
                await self._forbidden(send)
                return
        await self.app(scope, receive, send)

    async def _forbidden(self, send: Send) -> None:
        description = "Your account is not in the group allowed to use this server"
        body = json.dumps({"error": "insufficient_scope", "error_description": description})
        challenge = (
            f'Bearer error="insufficient_scope", error_description="{description}", '
            f'resource_metadata="{self.resource_metadata_url}"'
        )
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"www-authenticate", challenge.encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body.encode()})
