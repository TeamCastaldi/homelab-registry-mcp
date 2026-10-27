# ADR-024: OAuth Resource Server for `/mcp`, Authentik as the Authorization Server

| | |
|---|---|
| **Status** | Accepted and implemented (2.3.0); not yet validated against the live Authentik |
| **Amends** | CLAUDE.md, "ForwardAuth in front of MCP clients breaks them … auth strategy there is deferred; the endpoint is LAN-only": `/mcp` now authenticates in-process. "No HTTP /health endpoint": `GET /health` now exists |
| **Setup** | [SOP-010](../SOPs/SOP-010-Connect-Authentik-OAuth.md) |
| **Related** | [ADR-010](ADR-010-Dockhand-Update-Webhook.md) and [ADR-020](ADR-020-Patchmon-Webhook-Email-Approval.md) (the routes that keep their own credentials) |
| **Date** | 2026-10-27 |

## Context

This server's tools include `secrets_decrypt` (it holds the homelab's git-crypt
key), Ansible runs against every host through a root-equivalent SSH key, Dockhand
container and stack actions, and Infisical reads. Until this change it had no
authentication of its own: `/mcp` trusted its network position. It was reachable
from the internet with no auth from 2026-06-01 to 2026-10-26 (a log review found
no misuse) and was then put behind Traefik's `lan-only` middleware, which cut off
the claude.ai custom connector.

A network gate can't fix this on its own:

- **An allowlist of Anthropic's egress range** (`160.79.104.0/21`) lets in every
  claude.ai user's connector, not just the operator's.
- **Authentik forward-auth** needs a browser session cookie and answers with a
  redirect. MCP clients send neither cookies nor follow redirects.

So the server itself has to decide who is calling. The MCP authorization spec
(2025-06-18 and later) says how: the MCP server is an OAuth 2.1 **resource
server**. It answers an unauthenticated request with `401` and a
`WWW-Authenticate: Bearer resource_metadata="…"` header, publishes RFC 9728
protected-resource metadata naming its authorization server, and accepts only
access tokens that authorization server issued for it.

### What the clients need (checked 2026-10-27)

From Anthropic's [connector authentication docs](https://claude.com/docs/connectors/building/authentication.md):

- A claude.ai custom connector identifies itself in one of three ways: a Client ID
  Metadata Document (CIMD), Dynamic Client Registration (DCR, RFC 7591), or **an
  OAuth client ID the operator registered and enters in the connector dialog**
  (secret optional; blank means a public client).
- claude.ai's redirect URI is `https://claude.ai/api/mcp/auth_callback`. Claude
  Code uses an RFC 8252 loopback redirect, `http://localhost:<port>/callback`, on
  a port that changes per session unless `--callback-port` fixes it.
- PKCE with `S256` on every authorization request.
- Claude uses only the **first** entry in `authorization_servers`, and the
  metadata's `resource` must equal the URL entered in Claude exactly.
- Claude requests the scopes the 401's `scope` parameter names, else the
  metadata's `scopes_supported`, plus `offline_access` when the authorization
  server advertises it.
- The authorization server's discovery and token endpoints must be reachable
  from Anthropic's egress range.

From [Authentik's docs](https://docs.goauthentik.io/add-secure-apps/providers/oauth2/) for 2026.8:

- **DCR exists from 2026.8.0 but is not anonymous**: the registering client
  needs a bearer token carrying Authentik's DCR scope, and the user behind it
  must pass the application's policies. claude.ai's DCR registers with no token,
  so it can't use it.
- **No CIMD.**
- A static OAuth2/OpenID provider supports PKCE, public clients, several
  redirect URIs (exact or regex), an asymmetric signing key published at
  `/application/o/<slug>/jwks/`, and a per-provider issuer
  `https://<host>/application/o/<slug>/`.

Client support:

| Client | How it signs in |
|---|---|
| claude.ai (web, desktop, mobile) | Custom connector, "Use your own OAuth client", client ID only |
| Claude Code | `claude mcp add --transport http --client-id <id> …`; loopback redirect on any port |
| Copilot CLI | OAuth through DCR since v0.0.389. A bug where a configured `oauth.clientId` was ignored in favour of DCR ([github/copilot-cli#2717](https://github.com/github/copilot-cli/issues/2717)) was closed in May 2026, but the fixed release isn't named. Check the version in use; the fallback is a static `Authorization: Bearer` header holding a short-lived token |

## Decision

1. **`/mcp` is an OAuth resource server**, switched on by `MCP_AUTH_ENABLED`.
   The MCP Python SDK (1.29) supplies the mechanics: its `AuthSettings` and
   `TokenVerifier` hooks, the `AuthenticationMiddleware` that runs the
   verifier, the `RequireAuthMiddleware` that answers 401 with
   `resource_metadata`, and its session binding (a session created by one
   token's subject can't be used by another's).

2. **One static Authentik provider, public client, PKCE.** Every client uses
   the same client ID. Its redirect URIs are claude.ai's callback (exact) and
   the loopback callback on any port (regex). A public client has no secret,
   so the server holds no credential at all: it only verifies.

3. **Token checks** (`auth/verifier.py`, PyJWT): the signature against the
   issuer's JWKS, found through its OpenID discovery document (or
   `MCP_AUTH_JWKS_URL`); asymmetric algorithms only, and only the algorithm
   the key itself declares (never `none` or `HS*`); `iss` exactly
   `MCP_AUTH_ISSUER_URL`; `aud` equal to `MCP_AUTH_AUDIENCE`, the provider's
   client ID; `exp`, `nbf` (30s leeway) and `sub` required. Any failure is
   `401`. The key set is cached (`MCP_AUTH_JWKS_CACHE_SECONDS`); a token naming
   an unknown key id forces at most one refetch per 30 seconds, so rotation
   works at once but made-up key ids can't make this server hammer Authentik.

4. **Authorization is a group, checked by this server** (`auth/middleware.py`).
   A valid token whose `groups` claim lacks `MCP_AUTH_REQUIRED_GROUP` gets
   `403 insufficient_scope`. The SDK's own 403 is for a missing *scope*; a
   group isn't one, and advertising a fake scope would make clients request it
   from Authentik. The Authentik application is also bound to the group, so
   other users can't complete sign-in at all; the server's check stays, because
   the server must not rely on Authentik's binding being right.

5. **The metadata advertises `scopes_supported`** (`MCP_AUTH_SCOPES`, default
   `openid profile`). Without it Claude requests no scopes, Authentik's token
   carries no `groups` claim, and every sign-in ends in 403. The SDK's route
   lists none, so this server replaces it, and also answers at the bare
   `/.well-known/oauth-protected-resource` as well as the path-suffixed one.

6. **Only `/health` and the metadata are open.** The Dockhand and PatchMon
   webhooks and the PatchMon approval links keep their own credentials and stay
   outside OAuth: a webhook sender can't run an OAuth flow, and the approval
   links must open from a phone with no login. Dockhand presents a shared
   bearer secret (it doesn't sign bodies); PatchMon an HMAC-SHA256 signature;
   an approval link a random single-use, expiring token (ADR-020). An OAuth
   token is never accepted in place of any of them, and a webhook's
   `Authorization: Bearer <secret>` is quietly refused as a token, then checked
   by the webhook as before.

7. **Feature switches are unchanged.** Auth decides who can call. Settings such
   as `DOCKHAND_ALLOW_WRITE`, `SERVICE_DEPLOY_ENABLED`, `SECRETS_ALLOW_DECRYPT`
   and `INFISICAL_ENABLED` still decide which tools exist.

8. **Off by default, fail closed when on.** `MCP_AUTH_ENABLED=false` keeps an
   upgrade from locking out a LAN client that hasn't signed in yet, and the
   server logs `mcp_auth_disabled` at startup. With it on, a missing setting, a
   non-`https://` URL, or the SSE transport stops the server from starting
   rather than letting it start open.

## Alternatives considered

- **DCR against Authentik.** Ruled out: Authentik 2026.8's registration needs an
  authenticated caller, and each registration creates a new application and
  provider that nothing cleans up.
- **A confidential client with a secret in the connector.** Works for claude.ai,
  but Claude Code and Copilot CLI would need the secret on every laptop, and
  the server gains nothing from it. Public client plus PKCE is what OAuth 2.1
  intends for these clients.
- **Token introspection** (RFC 7662) instead of local JWT checks. One HTTP round
  trip to Authentik per request, and a client secret for the server. Local
  verification against a cached JWKS is enough for a single issuer.
- **A scope instead of a group.** Authentik scopes aren't granted per user;
  any user who can sign in can request any scope the provider lists. A group is
  the thing that names the operator.
- **Keep `lan-only` and add auth.** Possible, but the point is that network
  position no longer matters. Traefik keeps routing; the app decides.

## Consequences

**Positive.**

- `/mcp` can be public again, and the claude.ai connector can reconnect.
- Being on the LAN no longer gets anyone in. A compromised LAN device can't
  call `secrets_decrypt`.
- Every tool call carries a subject; the SDK binds a session to it.

**Negative.**

- **Removing someone from the group isn't instant.** The `groups` claim is
  read from the token, so an access token issued before the change keeps
  working until it expires. Keep the provider's access-token lifetime short
  (SOP-010 says 10 minutes). A refresh issues a new token with current groups.
- **The server depends on Authentik.** If Authentik is down, no client can sign
  in, and once the cached keys go stale no token verifies. `/health` still
  answers, the webhooks and approval links still work, and stdio is unaffected.
  Setting `MCP_AUTH_ENABLED=false` and putting `lan-only` back is the
  break-glass path.
- **Each Dockhand delivery logs one `mcp_token_refused reason=malformed`** line,
  because the SDK reads its webhook secret as a bearer token first. Noise, not
  a leak: the token itself is never logged.
- **SSE is refused with auth on.** The group gate covers `/mcp` only.
  Streamable HTTP is what every current client uses.

## Open items

1. Validate against the live Authentik 2026.8.5: that the access token's `aud`
   is the client ID and that `profile` puts `groups` in the access token
   (SOP-010, step 6). The tests assume both.
2. Copilot CLI: confirm whether the version in use honours a configured client
   ID.
3. When the SDK moves to v2 (issue #183), recheck that `secure_app` still finds
   the SDK's metadata route and auth middleware; it refuses to start if it can't.
