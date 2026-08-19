# ADR-008: MCP Tool Organization

| | |
|---|---|
| **Status** | DRAFT |
| **Companion to** | ADR-002 (Client Interfaces) |
| **Org** | github.com/TeamCastaldi |
| **License** | MIT |
| **Date** | 2026 |

---

## 1. Purpose

This document records the decision on how to organize the registry's 68
registered MCP tools as the server grows, and the technical verification
behind it. It defines: why the interface is split rather than the
deployment or data layer, the three-tier classification every tool has been
assigned to, and what is still gated on real usage data before the split is
implemented.

---

## 2. Context

68 MCP tools are registered on a single FastMCP server, confirmed by direct
inspection of the registered tool list (`server.py` + each `register_*_tools`
call): Authentik (10), Traefik (7), Discovery (5), Hardware (12),
Proposals + adoption (9), Registry core + service resolution (7),
Secrets (6), Events (3), Komodo (7), System/health (2). Connecting a
session to all 68 regardless of what that session actually needs is
unnecessary tool-call surface, and puts
tools with very different risk profiles (a read-only Traefik query vs.
`secrets_add`) in the same reachable set by default.

---

## 3. Decision

### 3.1 Split the interface, not the deployment or data layer

Keep one FastMCP process, one container, one SQLite store. Hardware,
Registry, Events, and Proposals cross-reference each other constantly
(e.g. `hardware-link-service` → `service_get_full_context`) — splitting
that across separate processes/databases would fragment referential
integrity for no real benefit. Instead, mount multiple MCP endpoints off
the single process (e.g. `/mcp/registry`, `/mcp/discovery`,
`/mcp/proposals`, `/mcp/secrets`, `/mcp/hardware`) so a given session only
connects the mounts it actually needs — dropping the effective tool count
per session from 68 into the teens, with ops overhead unchanged (still one
`docker compose` service, one Traefik route).

### 3.2 Sub-agents are complementary, not a substitute

Sub-agent orchestration solves task/context isolation, not tool
cardinality on the server. A sub-agent that still connects to all 68 tools
gains nothing from being a sub-agent. Sub-agents become useful *on top of*
the mount split (e.g. a scheduled "hardware capacity report" agent that
only ever connects to `/mcp/hardware`). Sequencing: mounts first,
sub-agents later, if/when there's an actual scheduled/autonomous workflow
to build.

### 3.3 Splitting axis: what a tool does, not its domain

Domain grouping (all Authentik together, etc.) is the wrong axis: Komodo
isn't a standalone workflow — it's used *in the same session* as
discovery/proposals during drift triage ("what does the registry claim"
next to "what does Komodo actually report"). The axis that matters is
which tools get reached for together in real sessions — not answerable yet
without usage data (see §6). What *is* answerable today, from existing
project rules with no usage history required, is which tools should be
isolated because of what they do:

- Rule 1 (sacred curated fields) and Rule 2 (read-only upstream / Git-only
  writes) separate observability from mutation.
- Rule 7 (secret sensitivity) isolates the `secrets_*` surface regardless
  of read/write.

---

## 4. Technical Verification

Two premises had to be checked against the actual pinned dependency before
any implementation could start — both diverged from initial assumptions
based on the standalone `fastmcp` (jlowin) PyPI package.

### 4.1 No middleware system exists

The project depends on `mcp>=1.28.1,<2` (pinned to 1.29.0 in `uv.lock`),
and `server.py` imports `from mcp.server.fastmcp import FastMCP` — the
FastMCP bundled in the **official MCP Python SDK**, not the standalone
`fastmcp` 2.x package. Direct inspection of the installed package confirms:
no `Middleware` class anywhere in `mcp` (no `on_call_tool` hook, no
built-in `LoggingMiddleware`), no `Context.session_id`, and no `mount()`
method on `FastMCP` — only `custom_route()` and `streamable_http_app()` /
`sse_app()`, which each return a plain `Starlette` app.

### 4.2 Composing sub-servers via a plain `Mount()` is broken

`FastMCP.streamable_http_app()` hardcodes its own Starlette app's lifespan
to `lambda app: self.session_manager.run()` (`mcp/server/fastmcp/server.py`).
ASGI `"lifespan"` scope is only ever dispatched to the outermost app a
server hands it to — Starlette's `Router` handles `"lifespan"` directly and
never forwards it into a mounted sub-app's own router. Verified with a
runnable spike (two toy `FastMCP` instances mounted under a parent
`Starlette(routes=[Mount(...), Mount(...)])` with no custom lifespan):
every request fails immediately with
`RuntimeError: Task group is not initialized. Make sure to use run().`
because neither sub-server's `StreamableHTTPSessionManager.run()` ever
started.

**Fix, also verified by spike:** give the parent `Starlette` app its own
combined `lifespan` that manually `AsyncExitStack`-enters every mounted
sub-server's `session_manager.run()` (each `FastMCP` exposes
`.session_manager` directly). With that in place, both mounts get
independent session IDs on `initialize` and route/tool-call correctly and
in isolation. This generalizes the existing single-server workaround in
`server.py`'s `main()` (`_streamable_with_scheduler`, which already
monkey-patches around `streamable_http_app()`'s hardcoded lifespan to start
the scheduler). That workaround's comment labels it as needed for
"FastMCP ≤ 1.27.1" — stale against the pinned `mcp==1.29.0` confirmed here;
direct inspection of the installed 1.29.0 source shows the lifespan is
still hardcoded to `session_manager.run()`, so the comment should be
corrected (drop the version ceiling) rather than treated as a signal this
is fixed upstream. The mount split replaces calling any one sub-server's
`run_streamable_http_async()` with building the composed app, entering all
N session managers plus the existing scheduler/comment-poll startup in one
combined lifespan, and serving that composed app directly via
`uvicorn.Config`/`uvicorn.Server`.

Conclusion: the mount split is technically unblocked. Not yet implemented
— see §6.

---

## 5. Tier Assignment

Every registered tool, classified by what it does rather than its domain.
Reconciled to 68 against the per-domain counts in §2 as of this ADR's
original decision; `proposal_normalize` (normalization engine) landed since,
bringing the total to 69 — see Tier 2 below.

### Tier 1 — Read-only observability (44 tools)

Low stakes if over-connected to an unrelated session.

| Domain | Tools |
|---|---|
| Traefik (7) | `traefik_get_overview`, `traefik_get_entrypoints`, `traefik_list_routers`, `traefik_get_router`, `traefik_list_services`, `traefik_list_middlewares`, `traefik_list_tls_certificates` |
| Authentik (10) | `authentik_list_applications`, `authentik_get_application`, `authentik_list_providers`, `authentik_list_outposts`, `authentik_get_outpost_status`, `authentik_list_policies`, `authentik_search_events`, `authentik_summarize_events`, `authentik_list_users`, `authentik_list_groups` |
| Komodo (7) | `komodo_health`, `komodo_list_stacks`, `komodo_get_stack`, `komodo_list_services`, `komodo_get_service`, `komodo_list_updates`, `komodo_get_logs` |
| Discovery (5) | `discovery_status`, `discovery_list_stale`, `discovery_run_now`, `discovery_connect_traefik`, `discovery_connect_authentik` |
| Registry reads (3) | `registry_get_service`, `registry_list_services`, `service_get_full_context` |
| Hardware reads (7) | `hardware-get-node`, `hardware-list-nodes`, `hardware-node-services`, `hardware-list-unconfirmed`, `hardware-list-stale`, `hardware-capacity-summary`, `hardware-discovery-status` |
| Events (3) | `events_list_discoveries`, `events_list_changes`, `events_get_for_service` |
| System/health (2) | `health`, `system_health_check` |

`discovery_run_now` is Tier 1 despite being a trigger: it only ever writes
provenance fields deterministically (Rule 1), the same effect the
APScheduler timer already produces on its own — calling it just changes
the timing. `discovery_connect_traefik`/`_authentik` never write anything
(CLAUDE.md: hands back `.env` lines, doesn't write a file or start
discovery). None of the five carry a `read_only` gate in
`register_discovery_tools`, confirming they were never meant to be gated.

`system_health_check` is a candidate for being reachable from every mount
regardless of tier, not just Tier 1 — it's what explains why a Tier 2 tool
just returned a read-only error.

### Tier 2 — Git-write / curated-field mutation / live-infra execution (19 tools)

The actual Degree-3 Agency surface: a wrong call here has a durable,
human-facing consequence even though it's PR-gated.

| Domain | Tools |
|---|---|
| Proposals + adoption (10, unsplit) | `proposal_create`, `proposal_list_open`, `proposal_get`, `proposal_cancel`, `proposal_verify`, `proposal_normalize`, `proposal_adopt_service`, `proposal_adopt_service_finalize`, `proposal_adopt_service_cancel`, `proposal_adopt_service_get` |
| Registry mutation (4) | `registry_add_service`, `registry_update_service`, `registry_delete_service`, `service_link_authentik` |
| Hardware mutation (4) | `hardware-add-node`, `hardware-update-node`, `hardware-delete-node`, `hardware-link-service` |
| Hardware live discovery (1) | `hardware-discover-now` |

Proposals/adoption is kept as one block, including its read-only views
(`proposal_list_open`, `proposal_get`, `proposal_adopt_service_get`) —
those surface uncommitted remediation patches and captured secret values
mid-review, not generic data, so splitting them out of the write-path
namespace buys nothing.

`hardware-discover-now` is the one genuinely borderline call: it only
writes provenance fields, same as `discovery_run_now` (which is Tier 1).
But unlike `discovery_run_now`, it isn't backstopped by an always-on
scheduler — it's the only way live Ansible fact-gathering happens at all —
and it executes real `ansible ... -m setup` against real hosts over SSH.
The code already treats it as higher-stakes: it's the one hardware tool
gated by `read_only` (`tools/hardware.py`), grouped with the GitOps write
tools rather than the plain reads next to it. This tier assignment follows
that existing call rather than overriding it.

### Tier 3 — Secrets (6 tools)

Isolated on sensitivity alone, regardless of read/write.

`secrets_status`, `secrets_encrypt`, `secrets_decrypt`, `secrets_add`,
`secrets_rotate`, `secrets_list_keys`.

---

## 6. Tool-Call Logging

Shipped in [PR #95](https://github.com/TeamCastaldi/homelab-registry-mcp/pull/95)
(`src/registry_mcp/logging/tool_calls.py`, `install_tool_call_logging`).
Wraps `FastMCP.call_tool` — the single dispatch point every `tools/call`
funnels through on every transport — and logs `session_id`, `tool_name`,
`success`/`failure` as a structured log line per invocation; `timestamp` is
added automatically by the existing `structlog.processors.TimeStamper`
already in the shared processor chain (`logging/events.py`), not something
`tool_calls.py` adds itself.

Deliberately a log line, not a new `ToolInvocation` table, and deliberately
omits arguments and results: key-name redaction
(`token`/`password`/`secret`/...) doesn't catch params like `secrets_add`'s
`value`, which carries a secret under an innocuous name — omitting
args/results entirely sidesteps that gap rather than relying on an
exhaustive redaction list.

Session identity is derived from the `ServerSession` object's identity (a
UUID lazily assigned per session), not the streamable-http `mcp-session-id`
header: `call_tool` executes inside the session's own persistent task,
spawned once at session creation — a contextvar bound around the per-request
HTTP task (where the header lives) never reaches it. Keying off
`Context.session` avoids that task-boundary issue and works uniformly
across stdio/sse/streamable-http, whereas the header approach would
silently produce zero correlation on stdio (the default transport for
`uv run registry-mcp`).

This is explicitly distinct from the `ChangeEvent`/`DiscoveryEvent` audit
log (`events_*` tools) — that's domain events; this is raw interaction
logging, same DB file at most, never the same table.

---

## 7. Consequences

### 7.1 Positive

- Per-session tool count drops from 68 to the teens without touching the
  deployment or data layer — one process, one container, one SQLite store.
- Tiering by what a tool does (curated-field mutation, Git-write, secret
  sensitivity) is answerable today from existing project rules, with no
  usage history required — unblocks a first cut immediately.
- The mount-composition risk (broken session lifespans) was found and
  fixed before any implementation, via a runnable spike rather than
  discovered in production.

### 7.2 Accepted Tradeoffs

- The tier boundaries are a first pass from static rules, not from
  observed co-occurrence. They will need to be sanity-checked (and
  possibly adjusted) against real session data once it exists — see §8.
- Whatever co-occurrence pattern the tool-call log collects pre-split
  reflects behavior under current all-68-exposed conditions. Once mounts
  exist and sessions pre-select which to connect, usage will shift. Early
  data is directional signal for the *first* boundaries, not a clean
  before/after experiment.

### 7.3 Known Gaps

- The mount-based split itself is not implemented. Gated on accumulating
  real tool-call log data to sanity-check the tier boundaries above,
  particularly the borderline calls (`hardware-discover-now`,
  `system_health_check`'s cross-tier reachability).
- Rule 5 ("every tool explicitly registered in `server.py`") needs to
  generalize to "explicitly registered per sub-server module" once the
  split ships — a docs update, not yet made.

---

## 8. Open Questions

| # | Question | Owner | Status |
|---|---|---|---|
| 1 | Do real sessions' tool-call patterns confirm the tier boundaries in §5, or does the data suggest different groupings (e.g. Komodo co-occurring with Proposals during drift triage, as originally observed anecdotally)? | Maintainer | Open — needs log data |
| 2 | Should `system_health_check` (and `health`) be reachable from every mount rather than only Tier 1's? | Maintainer | Open |
| 3 | What is the actual mount path scheme (`/mcp/registry` vs. something else) and does auth/access control differ per mount, particularly for Tier 3? | Maintainer | Open |

---

## 9. Implementation Phases

| Phase | Name | Scope | Status |
|---|---|---|---|
| **A** | Technical verification | Confirm FastMCP has no middleware system; confirm and fix the mount/lifespan interaction | Done |
| **B** | Tool-call logging | `session_id`/`tool_name`/`success` per invocation, no args/results | Done — PR #95 |
| **C** | Tier assignment | Full 68-tool classification (this document, §5) | Done |
| **D** | Usage data collection | Let logging run against real sessions before touching the split | In progress |
| **E** | Mount-based split | Compose sub-servers per tier (or per data from Phase D) behind one process, combined lifespan per §4.2 | Not started — gated on D |
| **F** | Sub-agent workflows | Scheduled/autonomous agents connecting to a single mount | Not started — gated on E |

---

## 10. References

- ADR-002: Client Interfaces (companion document — Discord bot's
  command-to-tool mapping is an existing example of a client connecting to
  a curated tool subset)
- [PR #95](https://github.com/TeamCastaldi/homelab-registry-mcp/pull/95) — tool-call logging implementation
- `mcp/server/fastmcp/server.py` (installed `mcp` 1.29.0) — `FastMCP.call_tool`, `streamable_http_app`, `Context`
- `mcp/server/streamable_http_manager.py` — `StreamableHTTPSessionManager`, per-session task spawning

---

*ADR-008 | github.com/TeamCastaldi | MIT License | 2026*
