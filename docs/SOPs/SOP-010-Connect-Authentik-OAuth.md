# SOP: Put `/mcp` Behind Authentik OAuth and Connect the Clients

**Owner:** the maintainer  
**Frequency:** Once; again when the Authentik provider is recreated or its signing key changes  
**Last Updated:** 2026-10-27  
**Status:** Written against Authentik 2026.8 docs and registry 2.3.0; not yet run against the live Authentik

---

### Purpose

Make `/mcp` accept only access tokens Authentik issued to a user in one group, so
the server can be public again and the claude.ai custom connector can reconnect.
See [ADR-024](../ADRs/ADR-024-OAuth-Resource-Server-For-MCP.md) for why it works
this way. The webhooks and PatchMon approval links are unaffected: they keep
their own credentials.

Values used below; substitute your own:

| | |
|---|---|
| Authentik | `https://sso.castaldifamily.com` (2026.8.5) |
| Application slug | `registry-mcp` |
| Issuer | `https://sso.castaldifamily.com/application/o/registry-mcp/` |
| Registry public URL | `https://registry-mcp.castaldifamily.com/mcp` |
| Group | `registry-mcp-admins` |

---

### When to use it

- First time turning on auth (registry 2.3.0 or later).
- After recreating the provider (new client ID) or changing its signing key.

---

### Prerequisites

- Registry **2.3.0 or later** deployed, with `MCP_AUTH_ENABLED` still unset (off).
  Its startup log says `mcp_auth_disabled`.
- Admin access to Authentik.
- Authentik's OAuth endpoints (`/application/o/…`) reachable from the internet,
  including Anthropic's egress range `160.79.104.0/21`. claude.ai fetches
  Authentik's discovery document and calls its token endpoint from there, so a
  `lan-only` or WAF rule on `sso.` breaks sign-in even when the registry is
  reachable.
- Nothing secret is created here: the client is public (PKCE, no secret).

---

### Steps

#### Authentik

1. **Create the group.** Directory → Groups → Create. Name `registry-mcp-admins`.
   Add your own user to it, and nobody else.

2. **Create the provider.** Applications → Providers → Create →
   **OAuth2/OpenID Provider**.
   - **Name:** `registry-mcp`
   - **Authorization flow:** `default-provider-authorization-explicit-consent`
     (shows a consent screen naming the app; the implicit flow also works)
   - **Client type:** **Public**. Clients send no secret and use PKCE.
   - **Client ID:** leave the generated value. Copy it: it is
     `MCP_AUTH_AUDIENCE`, and the client ID every client enters.
   - **Redirect URIs** (one entry each, with the matching mode shown):

     | Mode | Value | For |
     |---|---|---|
     | Strict | `https://claude.ai/api/mcp/auth_callback` | claude.ai web, desktop, mobile |
     | Regex | `^http://localhost:[0-9]+/callback$` | Claude Code (port changes per session) |
     | Regex | `^http://127\.0\.0\.1:[0-9]+/callback$` | Claude Code's IP-literal form |

     Add Copilot CLI's redirect only if you use it with this client ID; it is
     also a loopback URL, and the two regex entries may already cover it.
   - **Signing Key:** `authentik Self-signed Certificate` (or any RSA/EC
     certificate-key pair). **Required.** With no signing key Authentik signs
     with HS256 and the client secret, publishes no public key, and the registry
     refuses every token.
   - **Advanced protocol settings:**
     - **Access code validity:** `minutes=1` (default)
     - **Access token validity:** `minutes=10`. Group removal takes effect
       when the token expires, so keep it short.
     - **Refresh token validity:** `days=30` (or what you're comfortable with)
     - **Scopes:** keep `openid`, `profile`, `email`; make sure
       **`offline_access`** is selected so clients get a refresh token.
     - **Subject mode:** default (hashed user ID) is fine.
     - **Issuer mode:** **Each provider has a different issuer** (default).

   Save.

3. **Create the application.** Applications → Applications → Create.
   - **Name:** `homelab-registry-mcp`
   - **Slug:** `registry-mcp` (the issuer URL depends on it)
   - **Provider:** `registry-mcp`
   - Leave Launch URL empty, and don't bind the group yet: step 13 does that
     after the 403 test.

4. **Copy the issuer.** Open the provider. Its overview shows the **OpenID
   Configuration Issuer**; it should be
   `https://sso.castaldifamily.com/application/o/registry-mcp/`, trailing slash
   included. That exact string is `MCP_AUTH_ISSUER_URL`.

5. **Check discovery from outside.** From a device off your LAN:

   ```bash
   curl -s https://sso.castaldifamily.com/application/o/registry-mcp/.well-known/openid-configuration \
     | jq '{issuer, jwks_uri, code_challenge_methods_supported, scopes_supported}'
   ```

   `issuer` must match step 4 exactly, `code_challenge_methods_supported` must
   include `S256`, and `scopes_supported` must include `offline_access`.

6. **Check the token's claims.** Open the provider's **Preview** tab, pick your
   user, and read the access-token claims:
   - `aud` is the client ID from step 2
   - `iss` is the issuer from step 4
   - `groups` is a list containing `registry-mcp-admins`

   If `groups` is missing, confirm the provider's scopes include the default
   `profile` mapping (it carries group membership). If your version has no
   Preview tab, carry on: step 9 shows the same problems in the registry's log.

#### Registry

7. **Set the configuration in Infisical** (the registry's folder). None of these
   is a secret:

   ```
   MCP_AUTH_ENABLED=true
   MCP_AUTH_ISSUER_URL=https://sso.castaldifamily.com/application/o/registry-mcp/
   MCP_AUTH_AUDIENCE=<client ID from step 2>
   MCP_AUTH_REQUIRED_GROUP=registry-mcp-admins
   MCP_AUTH_RESOURCE_URL=https://registry-mcp.castaldifamily.com/mcp
   MCP_AUTH_SCOPES=openid profile offline_access
   ```

   `MCP_ALLOWED_HOSTS` must already include `registry-mcp.castaldifamily.com`,
   or every request gets HTTP 421 before auth is even checked.

8. **Redeploy through Dockhand** (not `docker compose up`, which falls back to a
   placeholder `.env`). If a setting is missing or a URL isn't `https://`, the
   container exits at startup and its log names the setting. Once it's up, the
   log has no `mcp_auth_disabled` line.

9. **Check from the command line** (LAN is fine at this point):

   ```bash
   # 401, with a pointer to the metadata
   curl -si -X POST https://registry-mcp.castaldifamily.com/mcp | grep -i '^HTTP\|www-authenticate'
   # the metadata: resource, issuer, scopes
   curl -s https://registry-mcp.castaldifamily.com/.well-known/oauth-protected-resource/mcp | jq
   # open
   curl -s https://registry-mcp.castaldifamily.com/health
   ```

   Expect `HTTP/2 401`, `WWW-Authenticate: Bearer error="invalid_token", …,
   resource_metadata="https://registry-mcp.castaldifamily.com/.well-known/oauth-protected-resource/mcp"`,
   a metadata document whose `resource` is the public URL and whose
   `authorization_servers` is `["https://sso.castaldifamily.com/application/o/registry-mcp/"]`,
   and `{"status":"ok"}`.

#### Clients

10. **Claude Code** (each machine):

    ```bash
    claude mcp remove registry-mcp 2>/dev/null
    claude mcp add --transport http --client-id <client ID> \
      registry-mcp https://registry-mcp.castaldifamily.com/mcp
    claude mcp login registry-mcp
    ```

    No `--client-secret`: the client is public. A browser opens Authentik; sign in
    and consent. `/mcp` in a session should then show the server connected with
    its tools.

11. **claude.ai** (Customize → Connectors → Add custom connector):
    - **URL:** `https://registry-mcp.castaldifamily.com/mcp`
    - **Authentication:** Sign in now
    - **OAuth client:** **Use your own OAuth client**. Client ID from step 2,
      **secret blank**
    - Add, then Connect. Authentik's sign-in and consent appear; afterwards the
      connector lists its tools.

    A connector's OAuth settings can't be edited later: to change the client
    ID, remove the connector and add it again.

12. **Copilot CLI.** Run `copilot --version` first. Configure the server with
    `oauth.clientId` set to the client ID. If it still tries Dynamic Client
    Registration (Authentik will refuse it), that version predates the fix for
    github/copilot-cli#2717. The fallback is a static
    `Authorization: Bearer <token>` header with a token you paste in, which
    expires after 10 minutes: workable for a test, not for daily use.

#### Prove the group check, then bind the group

13. **403 for someone outside the group.** Create a throwaway user
    (`registry-mcp-test`, not in the group). On a second machine or profile, run
    step 10's commands but sign in as the test user. Calling any tool must fail
    with HTTP 403 `insufficient_scope`, and the registry logs
    `mcp_token_forbidden reason=not_in_group`.

    Then bind the group: Applications → `homelab-registry-mcp` → Policy / Group /
    User Bindings → Bind existing group → `registry-mcp-admins`. Sign in as the
    test user again: Authentik now refuses at login. Delete the test user.

---

### Verification

Before calling it done, from **cellular** (not your LAN):

- `curl -si -X POST https://registry-mcp.castaldifamily.com/mcp` returns `401`
  with the `resource_metadata` header.
- A token for a user outside the group gets `403` (step 13).
- The claude.ai connector completes its login and lists tools (step 11).

`config_status` lists "MCP OAuth" as on with nothing missing.

---

### Rollback

1. Set `MCP_AUTH_ENABLED=false` in Infisical and redeploy through Dockhand.
   `/mcp` answers anyone who can reach it again.
2. **Before** that redeploy, put `lan-only@file` back on the `registry-mcp`
   Traefik router in the homelab repo, so the server is never public without
   auth.
3. The Authentik provider and application can stay; they do nothing while
   auth is off.

---

### Escalation: reading the registry's log

Every refused token logs one line with a reason and never the token:

| Log line | Meaning | Fix |
|---|---|---|
| `mcp_token_refused reason=wrong_audience` | `aud` isn't `MCP_AUTH_AUDIENCE` | Copy the client ID again (step 2) |
| `mcp_token_refused reason=wrong_issuer` | `iss` differs, often by the trailing slash | Copy the issuer exactly (step 4) |
| `mcp_token_refused reason=algorithm_not_allowed alg=HS256` | Provider has no signing key | Set one (step 2) |
| `mcp_token_refused reason=jwks_unavailable` | Registry can't fetch Authentik's keys | Check the registry can reach `sso.`; set `MCP_AUTH_JWKS_URL` to skip discovery |
| `mcp_token_refused reason=unknown_key_id` | Token signed by a key not in the JWKS | Signing key changed; wait 30s and retry |
| `mcp_token_refused reason=expired` | Normal after 10 minutes; the client refreshes | If it persists, check `offline_access` (step 2) |
| `mcp_token_refused reason=malformed` once per Dockhand delivery | Dockhand's webhook secret read as a token, then accepted by the webhook | Expected; ignore |
| `mcp_token_forbidden reason=not_in_group` | Valid user, not in `MCP_AUTH_REQUIRED_GROUP` | Add them to the group, or that's the point |

claude.ai's "Couldn't reach the MCP server" with no traffic at Authentik means it
never found the metadata: check step 9's 401 header and that
`MCP_AUTH_RESOURCE_URL` is exactly the URL entered in the connector.
