"""The 403 group gate and the protected-resource metadata, on a bare Starlette
app wired the way FastMCP wires /mcp: the SDK's AuthenticationMiddleware (with
the real verifier) first, then the group gate, then the SDK's 401 wrapper."""

import httpx
import pytest
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend, RequireAuthMiddleware
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route

from auth_fakes import AUDIENCE, GROUP, ISSUER, RESOURCE, FakeAuthentik
from registry_mcp.auth.middleware import RequireGroupMiddleware
from registry_mcp.auth.routes import metadata_paths, metadata_url, protected_resource_routes
from registry_mcp.auth.verifier import AuthentikTokenVerifier

METADATA_URL = "https://registry-mcp.example.com/.well-known/oauth-protected-resource/mcp"


async def _tool_endpoint(scope, receive, send):
    await JSONResponse({"ok": True})(scope, receive, send)


async def _webhook(_request):
    return JSONResponse({"webhook": True})


@pytest.fixture
def authentik():
    return FakeAuthentik()


@pytest.fixture
def client(authentik):
    verifier = AuthentikTokenVerifier(
        issuer=ISSUER, audience=AUDIENCE, transport=authentik.transport()
    )
    app = Starlette(
        routes=[
            Route(
                "/mcp",
                endpoint=RequireAuthMiddleware(_tool_endpoint, [], AnyHttpUrl(METADATA_URL)),
            ),
            Route("/webhooks/dockhand", _webhook, methods=["POST"]),
            *protected_resource_routes(
                resource_url=RESOURCE, issuer_url=ISSUER, scopes=["openid", "profile"]
            ),
        ],
        middleware=[
            Middleware(AuthenticationMiddleware, backend=BearerAuthBackend(verifier)),
            Middleware(AuthContextMiddleware),
            Middleware(
                RequireGroupMiddleware, group=GROUP, path="/mcp", resource_metadata_url=METADATA_URL
            ),
        ],
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


async def test_a_token_in_the_group_reaches_the_endpoint(client, authentik):
    response = await client.post("/mcp", headers=bearer(authentik.token()))
    assert response.status_code == 200


@pytest.mark.parametrize(
    "groups",
    [
        pytest.param(["family"], id="other-group"),
        pytest.param([], id="no-groups"),
        pytest.param(None, id="no-groups-claim"),
        pytest.param(GROUP, id="string-not-list"),
        pytest.param([GROUP.upper()], id="different-case"),
    ],
)
async def test_a_valid_token_outside_the_group_gets_403(client, authentik, groups):
    response = await client.post("/mcp", headers=bearer(authentik.token(groups=groups)))
    assert response.status_code == 403
    assert response.json()["error"] == "insufficient_scope"
    challenge = response.headers["www-authenticate"]
    assert challenge.startswith('Bearer error="insufficient_scope"')
    assert f'resource_metadata="{METADATA_URL}"' in challenge


async def test_no_token_is_401_not_403(client):
    response = await client.post("/mcp")
    assert response.status_code == 401
    assert f'resource_metadata="{METADATA_URL}"' in response.headers["www-authenticate"]


async def test_an_invalid_token_is_401_even_with_the_group(client, authentik):
    response = await client.post("/mcp", headers=bearer(authentik.token(aud="other")))
    assert response.status_code == 401


async def test_other_routes_are_not_gated_by_the_group(client):
    assert (await client.post("/webhooks/dockhand")).status_code == 200


async def test_a_trailing_slash_does_not_slip_past_the_gate(client, authentik):
    response = await client.post(
        "/mcp/", headers=bearer(authentik.token(groups=["family"])), follow_redirects=True
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "path", ["/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"]
)
async def test_metadata_names_the_resource_issuer_and_scopes_without_a_token(client, path):
    response = await client.get(path)
    assert response.status_code == 200
    body = response.json()
    assert body["resource"] == RESOURCE
    assert body["authorization_servers"] == [ISSUER]
    assert body["scopes_supported"] == ["openid", "profile"]
    assert body["bearer_methods_supported"] == ["header"]


def test_metadata_paths_and_url():
    assert metadata_paths(RESOURCE) == (
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
    )
    assert metadata_paths("https://registry-mcp.example.com") == (
        "/.well-known/oauth-protected-resource",
    )
    assert metadata_url(RESOURCE) == METADATA_URL
