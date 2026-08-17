# ADR-008: Conversational Chat Interface (Resolves ADR-002 §4.4 Open Questions 1-4)

| | |
|---|---|
| **Status** | Accepted |
| **Amends** | ADR-002-Client-Interfaces.md §4.4 (Phase 3 — Conversational Interface) |
| **Resolves** | ADR-002 Open Questions 1-4 (§8) |
| **Date** | 2026-08-17 |

## Context

ADR-002 §4.4 sketched a conversational interface as a "chat panel" embedded
inside the still-unbuilt React web UI (its Phase 1/2), explicitly marked
`THEORY` and blocked on four open questions: which model, how credentials
are managed, whether the browser calls the model directly or through a
backend, and whether chat reuses the full MCP tool surface or a subset.

The operator's actual request answers those questions with a concrete,
narrower design than ADR-002 anticipated: a standalone `/chat` page served
by `homelab-registry-mcp` itself — independent of the React dashboard, which
still does not exist — backed by a **local Ollama instance the operator
runs and owns**, not a cloud AI provider. This sidesteps OQ1's
per-operator-credential problem entirely (there is no API key to manage)
and answers OQ2 directly: the MCP server is the backend: the browser never
talks to Ollama, only to `homelab-registry-mcp`'s own new HTTP surface.

Two more facts, established during implementation, shaped the rest of this
ADR:

1. **`CLAUDE.md`'s "no HTTP endpoint" convention was stale.** It was written
   against an earlier `mcp` SDK where `streamable_http_app()` exposed no
   route customization. The pinned SDK (`mcp==1.29.0`, per `uv.lock`)
   ships `FastMCP.custom_route(path, methods)`, which mounts Starlette
   routes at the lowest matching precedence inside `streamable_http_app()`
   — alongside, not instead of, the existing `/mcp` endpoint. This ADR is
   the first thing in this repo to use it, and corrects that convention.
2. **The ForwardAuth deferral doesn't apply to a browser.** `CLAUDE.md` and
   `SECURITY.md` defer Authentik ForwardAuth in front of `/mcp` because *MCP
   clients don't follow redirects*. A browser does. But `docker-compose.yml`
   binds `0.0.0.0:8765` in addition to the `traefik` network, so anyone on
   the LAN can reach the port directly and forge `X-authentik-*` headers —
   which rules out trusting ForwardAuth headers even for a browser client.
   The chat login is therefore **in-process** (OIDC authorization-code +
   PKCE, or a static password fallback), independent of whatever sits in
   front of Traefik.

## Decision

### Scope: a standalone page, not a dashboard panel

`/chat` is registered directly on the FastMCP server via
`mcp.custom_route`, gated entirely behind `CHAT_ENABLED` (default `false`).
It does not require, and is not embedded in, the React web UI ADR-002
Phases 1-2 describe — those remain unbuilt. `/mcp` is untouched: this is
strictly additive HTTP surface on the same port.

### Auth: in-app OIDC, static password fallback, fail closed

Resolution order, decided once at server startup and never mixed:
Authentik (or any OIDC provider) when all four `CHAT_OIDC_*` values are
set, else `CHAT_PASSWORD`, else the routes are **not registered at all** —
`CHAT_ENABLED=true` with neither configured is a logged startup error, not
an open endpoint. See `registry_mcp/chat/auth.py` and `routes.py`.

OIDC uses the authorization-code flow with PKCE (S256), redeemed directly
against the IdP's token endpoint (a confidential-client back-channel
exchange) — no ID-token signature verification, and therefore no JWT/JWKS
library in the dependency tree, since the token is already known to have
come from the IdP by the time it's in hand. `CHAT_OIDC_ALLOWED_GROUPS`
gates on IdP-reported group membership; unlike `PROPOSAL_COMMENT_ALLOWED_USERS`,
empty means *any authenticated user*, not *no one* — this gate sits after a
successful IdP login, not in front of an anonymous public trigger, so the
two settings' fail-closed postures are intentionally different (see the
comment on `chat_oidc_allowed_groups` in `config.py`).

Sessions are a stateless HMAC-signed cookie (stdlib `hmac`, no new
dependency), so login survives a server restart whenever
`CHAT_SESSION_SECRET` is set; unset generates an ephemeral per-process key
and logs a warning rather than ever skipping signing.

**This login is a UI convenience boundary, not a new security boundary for
the lab itself.** `/mcp` remains completely unauthenticated on the same
port, exactly as before this ADR (`CLAUDE.md`'s existing "LAN-only" posture
is unchanged) — anyone who could already call `secrets_decrypt` over `/mcp`
still can. `/chat`'s login only gates the convenience of doing so through a
chat window.

### Grounding and the tool allowlist: the actual security boundary

The assistant never talks to `RegistryStore`/`HardwareStore`/any engine
directly — every piece of lab data it can see comes back through
`mcp.list_tools()`/`mcp.call_tool()`, the identical surface any other MCP
client uses (`registry_mcp/chat/bridge.py`). This both keeps chat from
adding a third object graph next to the two `server.py` already builds (see
`docs/ruthless-reviews/review-2026-06-30.md`) and guarantees chat can never
observe state an MCP client couldn't.

Answering OQ4 (full tool surface vs. a subset): a **fixed, explicit
allowlist by tool name**, never a prefix or pattern match, partitioned into
`READ_TOOLS` (always available), `WRITE_TOOLS` (added only when
`CHAT_ALLOW_WRITE=true`, and never when the server's own startup health
check has it in read-only mode), and `DENY_ALWAYS` (all six `secrets_*`,
all four `proposal_adopt_service*`, both hard deletes, the two
`discovery_connect_*` credential-accepting tools, the ansible-shelling
`hardware-discover-now`, and `authentik_summarize_events` for cost control)
— which wins unconditionally, checked independently at dispatch time so a
caller can't re-admit a denied tool by constructing `allowed` some other
way. `secrets_decrypt` is the clearest example of why this is name-based
rather than shape-based: it reads a file and returns structured data
exactly like a dozen legitimate read tools, but what it returns is
plaintext secret material.

A small, cheaply-rebuilt **context pack** (`registry_mcp/chat/context.py`)
— node roster, service counts, staleness, health mode — is injected as a
user-role message so the common questions ("what's on heimdall", "is
anything stale") resolve in one round trip without a tool call; deeper
questions still go through the same allowlisted tools.

### Model and hosting: operator-owned, off-box

Ollama is **not** run by this repo — `docker-compose.yml` gets no changes,
matching ADR-007's standing decision that this repo's compose file
describes the MCP server, full stop. `CHAT_OLLAMA_URL` points at wherever
the operator runs it; the reference deployment is a separate LAN host with
enough GPU headroom for reliable tool calling (the Pi 5 control plane
stays a coordinator, making HTTP calls only). `CHAT_OLLAMA_MODEL` defaults
to `qwen3:14b` — a size chosen for tool-calling reliability, not just raw
chat quality, since a small model returning malformed tool calls is worse
than a large model returning none.

### Persona: generic in-repo base, private overlay

The base persona (`registry_mcp/chat/persona.md`) is generic and
hostname-free because this repository is public. An operator's actual
house knowledge (hostnames, topology, safety rules — the equivalent of a
personal DevOps skill file) is supplied via `CHAT_PERSONA_PATH`, an
absolute path read at request time with an mtime cache — typically pointed
at a file inside the already-bind-mounted `/opt/homelab` private repo, so
no compose change is needed to wire it in. That path is validated as a
plain file-exists check (`Path.is_file()`), not `gitcrypt.check_path` — the
latter is for repo-relative, caller-supplied paths confined under a repo
root, and an absolute operator-set env var is a different trust class
entirely (the same class as `SECRETS_KEY_PATH`/`ANSIBLE_CFG_PATH`).

### Transport: hand-rolled SSE, no new framework

Streaming uses `text/event-stream` over a `StreamingResponse`, hand-framed
(`registry_mcp/chat/agent.py:format_sse`) rather than pulling in
`sse-starlette` (present only transitively via `mcp`) or a WebSocket. One
user turn can span several Ollama round trips interleaved with tool calls,
and the frontend needs server-originated event types (`tool_call`,
`tool_result`, `error`) that don't exist in Ollama's own wire format —
passing that format through unmodified would leak an implementation detail
into the frontend contract. `EventSource` (the browser's native SSE client)
is GET-only, so the frontend uses `fetch()` + a manual reader loop instead.

Conversation history is **client-side only** — the browser resends the
full (server-capped) transcript each turn, and the server holds nothing
between requests. This was chosen over a server-side session store for the
same reason the session cookie is stateless: it survives a restart for
free, and there is no per-user storage to grow or leak between browser
tabs.

### Frontend: one self-contained HTML file

`registry_mcp/chat/static/index.html` — vanilla JS, no build step, no new
Dockerfile stage. `Dockerfile` installs no Node toolchain and none of this
repo's 11 runtime dependencies today are a frontend framework; introducing
one for a single page would be a disproportionate new axis of maintenance.
Model output is rendered via `textContent` only, never `innerHTML` — it can
contain lab-sourced data (a service note, a Traefik router rule, an IdP
application name) that must never be interpreted as markup. A per-response
CSP nonce (`script-src`/`style-src` only, `default-src 'none'`) is
substituted into the page on each request.

## Consequences

### Positive

- Fully opt-in and additive: `CHAT_ENABLED=false` (the default) changes
  nothing about the server's existing behavior or `/mcp` surface.
- No cloud AI dependency, no per-operator API key to provision or rotate,
  no per-token cost — the whole design assumes hardware the operator
  already controls.
- The allowlist is enforced independent of the model's behavior — a
  hallucinated or adversarial tool call for a denied tool never reaches
  `mcp.call_tool` regardless of what the model outputs.
- `/mcp` and `/chat` share nothing but the tool registry itself; a bug in
  chat's HTTP layer cannot corrupt state MCP clients see, and vice versa.

### Negative / accepted tradeoffs

- **Chat's login does not harden `/mcp`.** Restated deliberately: this ADR
  does not close the gap `CLAUDE.md:255`/`SECURITY.md:44` already document
  (LAN-only, no auth in front of `/mcp`). An operator who wants that gap
  closed still needs the unresolved ForwardAuth-for-MCP-clients problem
  solved separately.
- **Prompt injection from lab data is a real, accepted risk**, not fully
  neutralized. Traefik router rules, Docker labels, and IdP application
  names are all attacker-influenced the moment anything in the lab is
  internet-facing. `context.py`'s sanitizer strips the cheapest injection
  shapes (control characters, role-marker-shaped lines) but that is
  advisory, not a boundary — the allowlist is the actual control. With
  `CHAT_ALLOW_WRITE=false` (the default), a successful injection can only
  make the assistant say something wrong; with it `true`, injected lab data
  could in principle steer a write. Operators enabling writes should
  understand that tradeoff explicitly.
- **FastMCP runs synchronous tools inline on the event loop** (confirmed by
  reading `FuncMetadata` in the pinned SDK) — most `registry_*`/`hardware-*`
  tools are plain `def`, not `async def`, and a chat tool call blocks the
  loop for the duration of that (fast, local SQLite) call. Acceptable at
  homelab scale; the one genuinely slow candidate
  (`hardware-discover-now`, which shells out to Ansible for potentially
  minutes) is in `DENY_ALWAYS` specifically because of this.
- **Ollama itself has no authentication.** A LAN host running it with no
  firewall is an open inference endpoint to anyone who can reach it.
  Firewalling that host, or fronting it, is the operator's responsibility —
  out of scope for this repo.
- One new runtime dependency, `starlette` — already present transitively
  via `mcp`, now imported directly by `chat/routes.py`.

## Open Items

1. **DSPy-on-Ollama is explicitly out of scope here.** The operator raised
   using the same local model to expand the DSPy reasoning layer
   (`dspy_model` already accepts any litellm model id, so
   `ollama_chat/qwen3:14b` would likely work with one new `DSPY_API_BASE`
   setting and one kwarg at two call sites in `dspy/reasoner.py`) —
   including a confidence-gated escalation from the local model to Claude
   when local output looks uncertain. Real, well-isolated future work; not
   attempted in this change.
2. **No automated migration or test coverage for the real Ollama box.**
   Everything here is verified against `httpx.MockTransport` and
   `httpx.ASGITransport`; the first deployment against a live GPU host
   still needs manual verification of throughput, `CHAT_NUM_CTX` sizing,
   and `qwen3:14b`'s actual tool-calling reliability on that hardware.
3. **The event-loop-blocking tradeoff above is not deeply mitigated.**
   Offloading sync tool calls via `anyio.to_thread` was considered and
   deferred — it interacts with `RegistryStore`'s SQLite engine thread
   affinity (`registry/store.py`) in ways that deserve their own change and
   its own tests, not a drive-by fix here.
4. **ADR-002's React web UI (Phases 1-2) remains unbuilt.** This ADR
   answers Phase 3's open questions but does not depend on, or advance,
   those earlier phases — `/chat` is a standalone page.
