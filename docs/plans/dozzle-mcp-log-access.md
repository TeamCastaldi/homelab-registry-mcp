# Dozzle on the control-plane: container logs for registry-mcp over MCP

| | |
|---|---|
| Status | Planned — not started |
| Date | 2026-09-28 |
| Source | Requested 2026-09-28: "deploy dozzle to the control-plane, expose the built in mcp server, and connect the registry-mcp with it for better troubleshooting" |
| Companion | [ADR-006](../ADRs/ADR-006-Pi-Non-MCP-Services-Komodo-Traefik.md) §3 (dropped Dozzle; amended by this plan's ADR), [ADR-011](../ADRs/ADR-011-Remove-Komodo-Integration-And-Chat-Interface.md) (the "load-bearing" test a read-only integration has to pass), [ADR-013](../ADRs/ADR-013-Dockhand-Read-Only-API-Integration.md), [ADR-016](../ADRs/ADR-016-Read-Only-Infisical-Integration.md) (the credential-exchange client shape reused here), ADR-021 (drafted in Phase 3) |

## Goal

When something breaks, an MCP client connected to registry-mcp should be able to go from
a registry service to its container's recent logs and resource stats in the same
conversation, without the operator copying logs out of a UI by hand. Three parts:

1. **Deploy a Dozzle hub on the control-plane**, through the homelab repo's GitOps
   pipeline like any other stack (ADR-007).
2. **Turn on and expose Dozzle's built-in MCP server** (`/api/mcp`), so it's reachable
   by registry-mcp over the control-plane's Docker network and, optionally, by
   OAuth-capable MCP clients (Claude Code, Claude Desktop) through Traefik.
3. **Add a read-only `dozzle_*` integration to registry-mcp** that relays an allowlisted
   subset of Dozzle's tools and adds the one thing Dozzle can't do: resolve a registry
   service id to the container(s) behind it.

## Why this, and why now

- **The server has had no way to read logs since ADR-011.** `komodo_get_logs` left with
  the Komodo integration, and the `dockhand_*` tools (ADR-013) cover environments,
  stacks, containers, updates, and vulnerabilities, but not logs. The troubleshooting
  skill's Observe step still starts by asking the operator to paste them in.
- **ADR-006 §3 dropped Dozzle with "no replacement planned"** because Komodo's UI
  covered logs. That reasoning was about which *dashboard* the operator opens. This
  plan brings Dozzle back for a different job: a log reader an agent can call. ADR-021
  has to say so explicitly and amend §3.
- **Dozzle now ships an MCP endpoint.** The log read path already speaks MCP, so
  registry-mcp's client has the same shape as the existing documentation-mcp passthrough
  (`integrations/docs/client.py`), with no scraping of Dozzle's UI-coupled REST API.
  That API's maintainer has said it isn't a stable external contract (amir20/dozzle
  discussion #4604).
- **ADR-011's test.** ADR-011 withdrew Komodo because it had become "a read-only window
  onto a system the operator can already open directly." This integration has to be more
  than that, and it is in two ways: it fills a capability gap the server doesn't
  otherwise have (logs), and `dozzle_get_service_logs` joins registry identity to
  containers, which neither Dozzle nor the operator's browser does.

**Alternative considered: registry-mcp reads logs from its own Docker socket.** It
already mounts the control-plane's socket for Docker discovery. Rejected: it covers one
host only, it has none of Dozzle's level detection, JSON parsing, or multi-line grouping,
and it would put a second log reader inside this server. Dozzle agents (Phase 5) extend
the same MCP calls to other nodes with no registry-mcp change.

## What Dozzle's MCP server offers (verified 2026-09-28)

Read from Dozzle's own `docs/guide/mcp.md`, `docs/guide/authentication*.md`,
`docs/guide/agent.md`, and `internal/mcp/server.go` / `internal/web/routes.go` /
`internal/auth/` at `master` (commit `a8f2940`, 2026-09-27). The latest release is
**v11.1.2** (2026-09-27), whose notes add "OAuth sign-in for MCP clients under simple and
oidc auth". Phase 0 re-checks these facts against whatever tag gets pinned.

**Endpoint.** `/api/mcp` (under `DOZZLE_BASE` if one is set), Streamable HTTP, served by
the Dozzle container itself. Off by default; `DOZZLE_ENABLE_MCP=true` turns it on.

**Tools.** Five, all annotated `readOnlyHint: true`:

| Tool | Parameters | Returns |
|---|---|---|
| `list_hosts` | none | JSON array: `id`, `name`, `nCPU`, `memTotal`, `dockerVersion`, `type`, `available` |
| `list_containers` | `state?` | JSON array: `id`, `name`, `image`, `state`, `health`, `host`, `created`, `labels`, `group` |
| `get_container_logs` | `host`, `container_id`, `since_minutes?` (default 5), `stream?` (`stdout`/`stderr`/`all`) | NDJSON entries (`timestamp`, `level`, `stream`, `type`, `message`), or the literal `(no logs in the specified time range)` |
| `search_container_logs` | as above, plus `query`, `case_sensitive?` | A `Found N matches for "q" (scanned M entries):` header, then NDJSON, then a truncation note if it hit 1 MB |
| `get_container_stats` | `host`, `container_id` | JSON: about the last 5 minutes of CPU %, memory %, and memory bytes |

Behavior that shapes the client:

- **The log tools stop at 1 MB and keep the *oldest* entries.** `get_container_logs`
  drops the rest without saying so (only the search tool appends a note). On a noisy
  container, "the last 60 minutes" can come back as the first twenty of them.
- A failed call is an MCP `isError` result with a plain-text reason (`container not
  found: ...`, `host and container_id are required`, `invalid stream ...`).
- The output formats are prose plus NDJSON, not a versioned contract. The version pin
  (D9) and strict parser tests are what guard them.
- The requesting user's container **filter** applies to MCP reads. **Roles don't gate**
  the five tools, because none of them is an action.

**Authentication, by `DOZZLE_AUTH_PROVIDER`:**

| Provider | What `/api/mcp` accepts |
|---|---|
| `none` (default) | Anyone who can reach it |
| `simple` (`users.yml`) | OAuth for interactive clients (v11.1.2+), **or** a session JWT sent as `Authorization: Bearer`. The JWT is minted by `POST /api/token` with form fields `username`/`password` and **comes back as a `jwt` cookie, with a response body of just `OK`** (`internal/web/auth.go`) |
| `oidc` | OAuth only. There is no `POST /api/token`, because there's no password to check |
| `forward-proxy` | Whatever the proxy injects. Dozzle trusts `Remote-User` on every request and grants all roles when no roles header is present |

- The session JWT's signing key is derived from a random secret persisted in `/data` plus
  the contents of `users.yml`. Editing `users.yml`, or losing `/data`, invalidates every
  token. With `DOZZLE_AUTH_TTL` unset (`session`), a session JWT carries no expiry at all.
- Dozzle's security page notes that `docker.sock:ro` restricts nothing. `:ro` marks the
  socket *file* read-only, and API calls, create and delete included, still go through.
- An agent (`dozzle agent`, port 7007) ignores `DOZZLE_ENABLE_SHELL`/`_ACTIONS`. The
  bundled TLS certificate is the same in every image, so anyone who can reach 7007 can
  read every log on that host and exec into its containers, unless you generate your
  own pair (`generate-certs`).
- Behind Traefik, a global `compress` middleware breaks SSE, and MCP's streamable HTTP
  transport uses SSE. `text/event-stream` has to be excluded from it.

## Design decisions

Each has a recommendation. Phase 0 confirms or overturns them, and ADR-021 records the
result.

**D1: Dozzle auth is `simple` (`users.yml`).**
- *Not `none`*: every container's logs would be open to anyone on the LAN.
- *Not `forward-proxy` behind Authentik ForwardAuth*: ForwardAuth answers MCP clients
  with a redirect, which they don't follow (CLAUDE.md, "ForwardAuth in front of MCP
  clients breaks them"). Worse, registry-mcp has to reach Dozzle directly, and a direct
  path to a forward-proxy Dozzle lets the caller authenticate as anyone by setting one
  header.
- *Not `oidc`*: it has no `POST /api/token`, and a server-side client can't complete an
  interactive OAuth consent.
- Human sign-in can still go through Authentik: Dozzle's "Sign in with OIDC" is a login
  *option* on the `simple` provider, with `users.yml` staying the allowlist. That's
  optional (Phase 0 decision), and password login keeps working either way.

**D2: registry-mcp gets its own Dozzle user.** `registry-mcp` in `users.yml`, with
`roles: none` (logs are still readable, subject to its filter), a random password of 32+
bytes, and no filter at first, so it sees every container the hub sees. Its read reach is
the same as a filterless human user, and nothing more.

**D3: registry-mcp holds a username and password, not a token.** It mints a session JWT
by `POST /api/token` (reading the `jwt` cookie), caches it in memory, and on a 401 mints
once more and retries once. This is the ADR-016 Universal Auth shape: a long-lived
credential in the secrets store, and a token that is disposable. A hand-pasted JWT was
rejected because it has to be dug out of a `Set-Cookie` header by hand, never expires
under the default TTL, and dies silently on the next `users.yml` edit. Set
`DOZZLE_AUTH_TTL` (e.g. `12h`) so a leaked token ages out. This also turns browser
sessions into timed cookies, which is fine.

**D4: the hub reads Docker through a read-only socket proxy, not the raw socket.** The
control-plane is the Ansible control node and runs Authentik, Infisical, and Traefik, so
a Dozzle compromise with the raw socket is root on the most sensitive host in the lab. A
dedicated `tecnativa/docker-socket-proxy` inside the Dozzle stack (`CONTAINERS=1`,
`INFO=1`, `EVENTS=1`, `POST=0`) on an `internal: true` network, with Dozzle pointed at it
through `DOZZLE_REMOTE_HOST=tcp://dozzle-socket-proxy:2375|control-plane` and no socket
mount of its own, means a compromised Dozzle can read but can't change anything.
- *Caveat*: Dozzle's maintainer recommends agents over TCP remote hosts and says issues
  with remote hosts won't be investigated, and the built-in healthcheck's handling of a
  TCP-only host is undocumented.
- *Fallback, if proxy mode misbehaves in Phase 1*: mount the socket directly, with
  actions and shell left disabled (the defaults), accepting the risk above in writing in
  ADR-021.
- The control-plane's existing `core` bundle already runs a socket proxy. It isn't
  reused, because its permissions are set for Traefik and changing them is a
  change to Traefik's stack.

**D5: registry-mcp reaches Dozzle over the Docker network, not through Traefik.**
- Both run on the control-plane: the live registry shows registry-mcp's router as
  `registry-mcp@docker`, i.e. the control-plane Traefik's Docker provider.
- Dozzle joins the same external network registry-mcp is on, uses `expose` rather than
  `ports`, and registry-mcp calls `http://dozzle:8080/api/mcp`. No ForwardAuth and no
  public hop are involved.
- If registry-mcp later moves to another node (older notes say that migration is in
  progress), only `DOZZLE_MCP_URL` changes, to the Traefik route from D6. That works
  because D6 puts no ForwardAuth in the way.

**D6: the Traefik route has no `authentik-auth@file` middleware.** `dozzle.<your-domain>`
on `websecure` with the wildcard cert. Dozzle authenticates every path itself (D1), and
ForwardAuth would break both MCP clients and Dozzle's own OAuth consent flow. Exposure
stays LAN-only by the same means as the lab's other internal routes (Phase 0 confirms
what that is).

**D7: deploy through the CD pipeline; secrets are git-crypt files.**
- Per `conversational-deploy.md`'s Phase 0 decision, new services target the CD pipeline,
  not Dockhand.
- Everything secret on Dozzle's side is a *file*: `users.yml` now, the agent cert and key
  in Phase 5. They live git-crypt-encrypted in the stack folder and are mounted with
  compose `secrets:` (`users.yml` → `/data/users.yml`).
- `/data` itself is a named volume and is **never** in Git: it holds the session secret
  that signs every token.
- This depends on the deploy clone on the control-plane being git-crypt-unlocked. Phase 0
  checks that. If it isn't, `users.yml` is placed on the host by hand outside the repo,
  and SOP-008 says so.

**D8: registry-mcp exposes typed, allowlisted wrappers, never a generic relay.**
- The client calls only the five tool names above. If a future Dozzle release adds an
  action tool (Dozzle Cloud already runs tools), registry-mcp never offers it.
- The allowlist, D2's `roles: none`, and `DOZZLE_ENABLE_ACTIONS=false` are three
  independent layers against a write.
- Dozzle is **not** a discovery source. Docker and Dockhand discovery already produce the
  registry rows, and a third source for the same containers would add reconcile noise and
  nothing else.

**D9: pin `amir20/dozzle:v11.1.2` (or a later exact tag), never `latest`.**
- The OAuth half of D6 needs v11.1.2 or later. The session-JWT path registry-mcp uses
  (D3) works on earlier v11 releases too.
- Before pinning or bumping, check the release notes with
  `get_service_documentation("dozzle", "<tag>", "mcp")` for changes to the tool formats
  in the table above.

## registry-mcp integration design

### Settings

| Variable | Default | Notes |
|---|---|---|
| `DOZZLE_MCP_URL` | unset | Dozzle's MCP endpoint as registry-mcp reaches it, e.g. `http://dozzle:8080/api/mcp`, matching `DOCS_MCP_URL` and Dozzle's own client examples. Must end in `/api/mcp`; the token URL is its sibling `/api/token`. Enables the integration |
| `DOZZLE_MCP_USERNAME` | unset | The D2 service user |
| `DOZZLE_MCP_PASSWORD` | unset | `SecretStr` |
| `DOZZLE_MCP_TIMEOUT_SECONDS` | `15` | Per call, token mint included |
| `DOZZLE_MCP_MAX_LOG_ENTRIES` | `200` | Newest entries returned per log call; the rest are counted, not returned |
| `DOZZLE_MCP_MAX_SINCE_MINUTES` | `1440` | Upper bound on a caller's `since_minutes` |

`DOZZLE_MCP_*` sits inside Dozzle's own `DOZZLE_*` environment namespace, but no Dozzle
flag uses these names, and the two containers never share an env file. SOP-008 points
this out so nobody sets them on the Dozzle container.

### Module layout

- **`integrations/dozzle/client.py`: `DozzleMcpClient`.**
  - Token mint via `httpx` form POST. A `200` with no `jwt` cookie is an error, not an
    empty token.
  - Each call opens a streamable-http `ClientSession` with the Bearer header, exactly the
    `DocsMcpClient` pattern, with an injectable `session_factory` for tests.
  - One re-mint and one retry on a 401. Phase 3 confirms how the `mcp` SDK surfaces a 401
    from inside its task group before writing the check.
  - An `_ALLOWED_TOOLS` frozenset guards every call.
  - Failures raise `DozzleMcpError`.
- **`integrations/dozzle/tools.py`**, six tools in a new snake_case `dozzle_*` family.
  All `readOnlyHint=True` and open-world, so they are *not* added to
  `server._CLOSED_WORLD_TOOLS`. ADR-008 Tier 1.
  - `dozzle_list_hosts()`
  - `dozzle_list_containers(state?, host?)`: labels cut to an allowlist
    (`com.docker.compose.project`, `.service`, `.project.working_dir`). Traefik
    basic-auth labels carry htpasswd hashes, and other labels can carry worse.
  - `dozzle_get_container_logs(host, container_id, since_minutes?, stream?, level?, max_entries?)`:
    `level` filters on Dozzle's detected level after the fetch (e.g. only `error`),
    which saves the caller's context.
  - `dozzle_search_container_logs(host, container_id, query, since_minutes?, stream?, case_sensitive?)`
  - `dozzle_get_container_stats(host, container_id)`
  - `dozzle_get_service_logs(service_id, since_minutes?, level?, max_entries?)`: the
    registry join.
    - Resolves the `Service` row to container(s) by compose service label, then container
      name, then the router name in its Traefik labels against `Service.traefik_router`
      (provider suffix stripped). Deterministic, with no DSPy, like `reconcile.py`.
    - Exactly one match returns logs.
    - Zero or several matches return the candidates, never a guess (the "never guesses"
      convention the webhooks follow).
- **A `diagnose_service_logs(service_id)` prompt.** It seeds `service_get_full_context`
  → `dozzle_get_service_logs(level="error")` → `dozzle_search_container_logs` for what
  that surfaces → `dozzle_get_container_stats` → `get_service_documentation` at the
  pinned version.
  - The name avoids a clash with Dockhand's `diagnose_stack`.
  - There's no `dozzle://` resource, because logs are time-windowed and parameterized,
    which is a poor fit for a fixed resource URI.

### Output rules

- **Size.**
  - Return the newest `max_entries` entries and report `returned`, `scanned`, and
    `omitted`.
  - When the raw upstream response is near 1 MB, set `upstream_truncated: true` with a
    note to narrow `since_minutes`, since Dozzle kept the *oldest* entries and the
    "newest" ones returned aren't the newest in the window.
- **Credentials.** Every log message passes the existing `_scrub_credentials`
  (`proposal/generator.py`, already imported by normalization and service_deploy), and
  the response reports how many entries were scrubbed. It's pattern-based and
  best-effort. Field-name log redaction (`logging/events.py`) doesn't apply to free text,
  so the docs must not call it complete.
- **Untrusted content.** Log lines are written by the containers. Every log tool's
  docstring says the content is data, not instructions: anyone who can make a container
  log a line can put text in front of the agent. Because the tools are read-only, a line
  can't trigger a write through Dozzle, and registry-mcp's own write tools keep their PR,
  merge, and math gates regardless.
- **Errors.**
  - Not configured → `{"error": "DOZZLE_MCP_URL, DOZZLE_MCP_USERNAME and DOZZLE_MCP_PASSWORD must be configured"}`.
  - An upstream `isError` result → `{"error": <its text>}`.
  - A transport or auth failure → `{"error": ...}`.
  - Tools never raise (the `errors.py` convention).

## Phases

### Phase 0: Recon and decisions (no changes)

**Goal**: Confirm the facts D1–D9 rest on before anything is deployed.

**Tasks**:
- [ ] Confirm registry-mcp runs on the control-plane (the live router is
  `registry-mcp@docker`, read 2026-09-28 with `registry_list_services`) and find the
  external network its homelab compose joins (`traefik` in this repo's
  `docker-compose.yml`, `proxy-net` in older troubleshooting notes)
- [ ] Confirm the control-plane's folder name under `nodes/` in the homelab repo (the
  hardware registry hostname is `homelab-control-plane`; the troubleshooting reference
  uses `nodes/control-plane/`)
- [ ] Check whether `websecure` carries a global `compress` middleware and how internal
  routes are kept LAN-only (`traefik_get_entrypoints`, `traefik_list_middlewares`)
- [ ] Check whether the homelab clone the CD pipeline deploys from on the control-plane is
  git-crypt-unlocked (decides D7's delivery of `users.yml`)
- [ ] Run `get_service_documentation("dozzle", "v11.1.2", "mcp")` and reconcile this
  plan's "What Dozzle's MCP server offers" section with it
- [ ] Confirm `amir20/dozzle:v11.1.2` and `tecnativa/docker-socket-proxy:v0.5.0` publish
  `linux/arm64` images (the control-plane is a Pi 5)
- [ ] Decide: hostname; password-only or Authentik OIDC login on the `simple` provider;
  D4 proxy or direct socket; whether Phase 5 (agents) is in scope

**Verification**: every Decision above marked confirmed or changed in the Decisions log
at the end of this file.

### Phase 1: Deploy the Dozzle hub on the control-plane (homelab repo PR)

**Goal**: Dozzle running on the control-plane behind its own login, reading Docker only
through a read-only proxy, with MCP enabled.

**Tasks**:
- [ ] Generate `users.yml` with `amir20/dozzle:v11.1.2 generate`: the operator's own
  user with `docker run -it` (password prompted on stdin), and `registry-mcp` with
  `docker run -i`, `--user-roles none`, and a random password piped in on stdin, so it
  never lands in shell history
- [ ] Store the `registry-mcp` password in Infisical right away, at
  `/homelab-registry-mcp` → `DOZZLE_MCP_PASSWORD`, and nowhere else
- [ ] Add `nodes/<control-plane>/dozzle/compose.yaml` (sketch below) with `users.yml`
  git-crypt-encrypted beside it. Add the `.gitattributes` entry (`secrets_encrypt`)
  **before** `users.yml` is first committed, because git-crypt never rewrites a
  plaintext commit that's already in history
- [ ] PR → merge → CD deploy
- [ ] Once Traefik discovery picks the router up, curate the new `dozzle` registry row
  (`registry_update_service`: category `monitoring`) and link it to the control-plane
  (`hardware-link-service`)

Compose sketch, written to this repo's canonical form (key order, quoted labels, a
pinned tag, `restart`, `container_name` equal to the key, shared network `external:
true`). Placeholders in `<>`:

```yaml
services:
  dozzle:
    image: "amir20/dozzle:v11.1.2"
    container_name: dozzle
    restart: unless-stopped
    depends_on:
      - dozzle-socket-proxy
    environment:
      DOZZLE_AUTH_PROVIDER: "simple"
      DOZZLE_AUTH_TTL: "12h"
      DOZZLE_ENABLE_MCP: "true"
      DOZZLE_HOSTNAME: "control-plane"
      DOZZLE_IMAGE_CHECK_MODE: "off"
      DOZZLE_NO_ANALYTICS: "true"
      DOZZLE_RELEASE_CHECK_MODE: "manual"
      DOZZLE_REMOTE_HOST: "tcp://dozzle-socket-proxy:2375|control-plane"
      # DOZZLE_ENABLE_ACTIONS and DOZZLE_ENABLE_SHELL stay unset (false)
    volumes:
      - dozzle-data:/data
    networks:
      - dozzle-internal
      - <shared-proxy-network>
    labels:
      traefik.enable: "true"
      traefik.http.routers.dozzle.entrypoints: "websecure"
      traefik.http.routers.dozzle.rule: "Host(`dozzle.<your-domain>`)"
      traefik.http.routers.dozzle.tls.certresolver: "<your-certresolver>"
      traefik.http.services.dozzle.loadbalancer.server.port: "8080"
    healthcheck:
      test: ["CMD", "/dozzle", "healthcheck"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
    expose:
      - "8080"
    secrets:
      - source: dozzle-users
        target: /data/users.yml
  dozzle-socket-proxy:
    image: "tecnativa/docker-socket-proxy:v0.5.0"
    container_name: dozzle-socket-proxy
    restart: unless-stopped
    environment:
      CONTAINERS: "1"
      EVENTS: "1"
      INFO: "1"
      POST: "0"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    networks:
      - dozzle-internal
volumes:
  dozzle-data:
networks:
  dozzle-internal:
    internal: true
  <shared-proxy-network>:
    external: true
secrets:
  dozzle-users:
    file: ./users.yml
```

**Verification** (run on the control-plane):

```bash
docker compose -f ~/homelab/nodes/<control-plane>/dozzle/compose.yaml ps    # both healthy

# The token comes back as a cookie and the body is just "OK". The password is read from a
# file ($PWFILE, written with no trailing newline), not typed on the command line.
docker run --rm --network <shared-proxy-network> -v "$PWFILE":/pw:ro curlimages/curl \
  -sS -i -F username=registry-mcp -F 'password=</pw' http://dozzle:8080/api/token \
  | grep -i '^set-cookie: jwt='

# The proxy refuses writes: expect 403
docker run --rm --network <dozzle-internal network name> curlimages/curl -s -o /dev/null \
  -w '%{http_code}\n' -X POST http://dozzle-socket-proxy:2375/containers/dozzle/restart
```

The UI loads, sign-in works, and control-plane containers' logs stream.

### Phase 2: Expose the MCP endpoint

**Goal**: `/api/mcp` answers MCP clients correctly through Traefik. registry-mcp doesn't
need this, since it uses the internal path (D5), but a direct MCP client does, and it
proves no ForwardAuth crept in.

**Tasks**:
- [ ] If Phase 0 found a global `compress` middleware, exclude `text/event-stream` from
  it (Traefik's stack, a separate homelab PR)
- [ ] Confirm the router carries no `authentik-auth@file`
- [ ] Optional: add Dozzle to Claude Code directly (`claude mcp add --transport http
  dozzle https://dozzle.<your-domain>/api/mcp`) and complete the OAuth consent (v11.1.2+)

**Verification**:

```bash
# OAuth metadata names the public https hostname, which proves X-Forwarded-Host and
# X-Forwarded-Proto reach Dozzle
curl -sS https://dozzle.<your-domain>/.well-known/oauth-protected-resource/api/mcp

# Unauthenticated MCP request: expect 401 with a WWW-Authenticate header. A 302 means
# ForwardAuth is in front of it.
curl -sS -o /dev/null -w '%{http_code}\n' -X POST https://dozzle.<your-domain>/api/mcp
```

If the direct client was added, its `list_hosts` returns the control-plane.

### Phase 3: registry-mcp integration (this repo)

**Goal**: The six `dozzle_*` tools and the prompt from "registry-mcp integration design",
off by default, tested against fakes as strict as Dozzle itself.

**Tasks**:
- [ ] Draft `docs/ADRs/ADR-021-Read-Only-Dozzle-Log-Access.md`. It amends ADR-006 §3
  (Dozzle returns as a log reader, not a dashboard), states how it passes ADR-011's test,
  adds Dozzle to CLAUDE.md's read-only upstream list, and records D1–D9
- [ ] Add the settings to `config.py` (`DOZZLE_MCP_PASSWORD` as `SecretStr`),
  `.env.example`, and a `config_report._FEATURES` entry ("Dozzle": `needs` URL,
  username, password; `uses` timeout and the two caps). Validate that
  `DOZZLE_MCP_URL` ends in `/api/mcp`
- [ ] `integrations/dozzle/client.py`: token mint from the `jwt` cookie, cached token,
  single re-mint on 401, tool allowlist, strict parsers for the formats in the table above
- [ ] `integrations/dozzle/tools.py`: the six tools, label allowlist, output caps,
  `upstream_truncated`, credential scrub, and the `diagnose_service_logs` prompt
- [ ] Register in `server.py`, and leave the tools out of `_CLOSED_WORLD_TOOLS`
- [ ] Tests (`tests/test_dozzle_client.py`, `tests/test_dozzle_tools.py`) following
  `tests/README.md`. No fake may be more forgiving than Dozzle:
  - the token fake returns the JWT **only** as a `jwt` cookie, with body `OK`;
  - the MCP fake returns Dozzle's exact text shapes, including
    `(no logs in the specified time range)`, the `Found N matches` header, and `isError`
    results;
  - cases: not configured, bad credentials, 200 without a cookie, 401 → one re-mint →
    success, 401 twice → error, a disallowed tool name refused before any I/O, the label
    allowlist, the newest-N cap, near-1 MB → `upstream_truncated`, a scrubbed credential,
    service resolution with one, zero, and several matches
- [ ] Mutation-probe the safety properties (allowlist, scrub, label allowlist,
  single-retry bound, caps) with `mutmut` against `src/registry_mcp/integrations/dozzle/`
- [ ] Docs: CLAUDE.md (project tree, architecture section, env table, the "Upstream APIs
  are read-only" convention, tool counts in the naming convention), `README.md`

**Verification**:

```bash
uv run pytest tests/test_dozzle_client.py tests/test_dozzle_tools.py -v
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

### Phase 4: Connect registry-mcp to the live Dozzle

**Goal**: The deployed registry-mcp reads real logs.

**Tasks**:
- [ ] After the release that carries Phase 3, add `DOZZLE_MCP_URL` and
  `DOZZLE_MCP_USERNAME` to Infisical beside the password (Phase 1) and redeploy
  registry-mcp **through Dockhand**, not `docker compose up -d` (ADR-017's deployment
  note: Dockhand is what injects the Infisical values)
- [ ] Confirm registry-mcp's container is on the network Dozzle joined (D5)
- [ ] Write `docs/SOPs/SOP-008-Connect-Dozzle-MCP.md`: the service user, where each value
  lives, the verification calls below, and rotation (a new hash in `users.yml`, a new
  value in Infisical, redeploy both; editing `users.yml` already invalidates old tokens)
- [ ] Update `.claude/skills/troubleshooting/references/homelab.md` so the Observe step
  reads logs with `dozzle_*` through Registry MCP, noting that log content is untrusted
- [ ] Update CLAUDE.md's Current Status, and tick the roadmap item

**Verification** (through any MCP client connected to registry-mcp):
- `config_status` lists "Dozzle" under `features_on` with no `problems`
- `dozzle_list_hosts` returns the control-plane, with `available: true`
- `dozzle_get_service_logs` for a service on the control-plane (e.g. `traefik`) with
  `level="error"` returns entries or an explicit empty result
- After an edit to `users.yml` and a Dozzle restart, the next call still succeeds (one
  re-mint), which shows D3's recovery path works live

### Phase 5 (optional, its own decision): other nodes through Dozzle agents

**Goal**: The same calls reach heimdall, waldorf, and p1ollama, where most services run.
No registry-mcp change is needed, because `list_hosts` and `list_containers` already span
every host the hub knows.

**Tasks**:
- [ ] Generate a custom agent cert pair (`generate-certs`) and store both files
  git-crypt-encrypted. The key is a credential: whoever holds it can exec into every
  agent's containers
- [ ] One `dozzle agent` stack per node (raw socket; agents can't sit behind a socket
  proxy), with 7007 published only on the LAN interface and host-firewalled to the
  control-plane alone
- [ ] `DOZZLE_REMOTE_AGENT` on the hub lists each agent with a display name
- [ ] Never panoptichron: the troubleshooting reference keeps it hands-off for anything
  but the operator's own apps

**Rejected for Phase 5**: read-only socket proxies on each node reached over the LAN.
That's an unauthenticated, unencrypted Docker API on port 2375, and `CONTAINERS=1` allows
`GET /containers/{id}/json`, which returns every container's environment, secrets
included.

**Verification**: `dozzle_list_hosts` lists every agent as `available`, and
`dozzle_get_service_logs` resolves a heimdall-hosted service.

## Risks

| Risk | Handling |
|---|---|
| A Dozzle vulnerability on the control-plane | Read-only socket proxy (D4); actions and shell off; the service user has `roles: none`; D6 exposure stays LAN-only |
| Secrets printed in container logs reach an LLM provider | `_scrub_credentials` on every message, best-effort. SOP-008 and ADR-021 say plainly that it's pattern-based |
| Prompt injection through log lines | Docstrings mark log content as untrusted; the tools are read-only; registry-mcp's write tools keep their own gates |
| Log volume floods the caller's context | `max_entries`, `level` filter, `since_minutes` cap, and `upstream_truncated` |
| A future Dozzle MCP tool mutates containers | Tool allowlist + `roles: none` + `DOZZLE_ENABLE_ACTIONS=false` (D8) |
| v11 is new, and the tool output is unversioned prose | Exact-tag pin; strict parser tests; a docs check before every bump (D9) |
| Tokens invalidated by a `users.yml` edit or a lost `/data` | One re-mint on 401 (D3); `/data` is a named volume |
| bcrypt hashes in Git | git-crypt; the service password is random and long enough that its hash isn't worth cracking |
| TCP remote-host mode is unsupported upstream | Direct-socket fallback, recorded in ADR-021 (D4) |

## Non-goals

- Dozzle alerts and notification rules, Dozzle Cloud (never linked; no user gets the
  `cloud` role), actions, shell, and auto-update.
- LLM summaries of logs. A DSPy module in the shape of `SummarizeAccessAudit` could come
  later; it's not part of this plan.
- Dozzle as a registry discovery source (D8).
- Replacing Komodo or Dockhand.

## Found while planning (out of scope, not fixed here)

- **The `dockhand_*` tools return 302 right now.** `dockhand_list_environments` answered
  "Dockhand API returned 302" on 2026-09-28, and the registry shows Dockhand's router as
  `forward_auth`. `DOCKHAND_API_URL` probably points at the Authentik-protected hostname
  rather than an internal address.
- **registry-mcp's own Docker socket mount isn't read-only in effect.**
  `docker-compose.yml` mounts `/var/run/docker.sock:ro` and CLAUDE.md calls it read-only,
  but per Dozzle's security notes (and Docker's behavior) `:ro` doesn't restrict the API.
  Docker discovery has full Docker API access on the control-plane. Moving registry-mcp
  behind a read-only socket proxy too would be a separate change.
- **The troubleshooting reference's note that registry-mcp moved off the control-plane
  looks stale.** The live `registry-mcp@docker` router says it's still there. Phase 0
  confirms this either way, since D5 depends on it.

## Decisions log

- 2026-09-28: plan requested and written. D1–D9 are recommendations pending Phase 0.
