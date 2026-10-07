# Stateless MCP (2026-07-28) via Python SDK v2

**Status:** Drafted 2026-10-07, not yet approved. Not started; step 0 is next.
**Produces:** ADR-022 (step 1; ADR-021 is reserved by
[`2026-10-glances-dashboard.md`](2026-10-glances-dashboard.md)), an `mcp>=2.3,<3` pin, and
tests for both protocol eras. No new settings.

## Context

[SEP-2575](https://modelcontextprotocol.io/seps/2575-stateless-mcp) (Final) removes MCP's
`initialize` handshake. Every request now carries its own protocol version and client
capabilities in `_meta`, and the `MCP-Protocol-Version` header must match. A new
`server/discover` RPC replaces the handshake's capability exchange. It shipped in the
2026-07-28 spec together with SEP-2567, which removes `Mcp-Session-Id` and protocol-level
sessions. Any server instance can then answer any request, with nothing to lose on restart.

This server can't offer that yet. `pyproject.toml` pins `mcp>=1.28.1,<2` (locked at 1.29.0).
The 1.x line is in maintenance mode, gets security fixes only, and never speaks 2026-07-28.
SDK v2 (2.0.0 on 2026-07-28; 2.3.0 on 2026-10-02) serves both protocol eras from one
`streamable_http_app()`, so 2025-era clients keep working through the change.

The tool surface is already stateless. None of the 78 tools or 11 resources and prompts takes
a `Context`, reports progress, logs to the client, or uses sampling, elicitation, roots,
list-changed notifications, or resource subscriptions. The multi-step flows (delete and
inventory-write challenges, adoption drafts, patch approvals) keep their state in SQLite
behind an id the model passes back. That is the explicit-handle pattern the 2026-07-28
release recommends in place of sessions. So this is an SDK upgrade with one real redesign
(tool-call logging), not a protocol port.

**What this gains:** a container restart or a `publish.yml` redeploy no longer drops client
sessions (today clients get `404 Session not found` until they re-initialize). A 2026-era
client skips the handshake, the docs passthrough becomes one POST, logs can name the calling
client, and the server is back on a maintained SDK. **What it doesn't gain:** horizontal
scale. SQLite and the in-process APScheduler jobs keep this a single-replica server.

### Proposed decisions (confirm at plan approval)

| Topic | Decision | Why |
|---|---|---|
| SDK target | **`mcp>=2.3,<3`.** | 2.3.0 is current; 2.2.0 added redirect restrictions and OAuth issuer validation. 1.x gets security fixes only. |
| Protocol eras | **Serve both.** 2026-07-28 requests and 2025-era `initialize` from the same app. | v2 does it with nothing to configure, and the operator's clients (Claude Desktop, Claude Code, VS Code) move on their own schedule. |
| Legacy leg | **`stateless_http=True`.** | It only affects pre-2026 clients. No tool uses the back-channel it removes (sampling, push elicitation, `roots/list`, resumability), so the only effect is that legacy clients also survive restarts. Without it, v2.2.0+ expires idle legacy sessions after 30 minutes. |
| Tool-call log identity | **Log `client_name`, `client_version` and `request_id`; drop `session_id`.** | v2 builds a new `ServerSession` per inbound message and 2026-era requests have no session, so the current object-identity key would be a new id on every call. `clientInfo` is optional per request (spec PR #3002), so both client fields may be null. |
| Error convention | **Keep `{"error": ...}` → `isError: true`.** | Unchanged contract. Note: in v2 an *uncaught* exception becomes a `-32603` protocol error, not an `isError` result, which makes the convention more important, not less. |
| Out of scope | `subscriptions/listen`, list-changed notifications, SEP-2322 elicitation (`Resolve`), tasks, `Mcp-Name` routing rules in Traefik, auth on `/mcp`. | Nothing needs them. The math gates stay SQLite-backed handles; SEP-2577 has started deprecating roots, sampling, and logging. |

### Coordination with the Glances dashboard plan

[`2026-10-glances-dashboard.md`](2026-10-glances-dashboard.md) is approved and next up per the
2026-10-06 snapshot. It adds `/dashboard` routes through `FastMCP.custom_route` and claims
ADR-021. Whichever plan lands second ports the other's code: if Glances lands first, step 3
below also renames `dashboard/routes.py`'s registrations. If this lands first, the Glances plan
writes its routes against `MCPServer`.

## What changes, by file

Counted on 2026-10-07 at `75f1be6`.

| Area | Files | Change |
|---|---|---|
| Server class | 22 in `src/` import `mcp.server.fastmcp.FastMCP`: `server.py`, `errors.py`, `logging/tool_calls.py`, every `tools/*.py` (11), the five `integrations/*/tools.py`, and `webhooks/{dockhand,patchmon,approval}.py` | `FastMCP` → `MCPServer` from `mcp.server.mcpserver` |
| Constructor | `server.py:256` | `host`, `port` and `transport_security` leave the constructor. `transport_security` and `stateless_http=True` go to `streamable_http_app()`. `_run_http` reads host and port from `Settings`, not `server.settings` |
| Private SDK internals | `server.py:112` (`_apply_open_world_hints`), `logging/tool_calls.py:68`, `tests/test_service_deploy_tools.py:173` | All use `_tool_manager`. Replace with the public listing and hook the step 0 spike finds |
| Exceptions | `ResourceError` (4 imports, including `errors.py`), `ToolError` (2 tests) | New import paths under `mcp.server.mcpserver` |
| Types | `ToolAnnotations` (17 imports), `CallToolResult`/`TextContent` (`tool_calls.py`, `tests/conftest.py`) | Python attributes become snake_case: `open_world_hint`, `is_error`, `structured_content`, `input_schema`. 21 attribute uses across `server.py`, `tool_calls.py`, `integrations/docs/client.py`, `conftest.py`, `test_mcp_surface.py`, `test_tool_call_logging.py`, `test_events.py` |
| Docs client | `integrations/docs/client.py` | `streamablehttp_client` is removed; use v2's `Client` (default `mode='auto'`) |
| Test helper | `tests/conftest.py:41` `tool_payload()` | It assumes v1's `call_tool()` returns a `(content, structured)` tuple on success. v2 returns `CallToolResult`. 18 test files go through this one helper, and 20 call `server.call_tool()` |
| Handshake tests | `test_tool_call_logging.py`, `test_transport_security.py`, `test_server_runtime.py`, `test_mcp_surface.py`, `test_docs.py` | They drive `initialize` by hand. That stays valid as legacy-era coverage; the session-id assertions in `test_tool_call_logging.py` go |
| Custom routes | `webhooks/dockhand.py:145`, `webhooks/patchmon.py:225`, `webhooks/approval.py:323,327` | Move with the rename; step 0 confirms they still sit outside the `/mcp` Host/Origin check |

## Implementation steps (one at a time, each verified before the next)

Steps 2–4 leave the full suite red; their checks are scoped. The suite must be green again at
the end of step 5, and stays green from there.

0. **Spike in a throwaway worktree.** Install `mcp>=2.3,<3` and answer, with a scratch script
   or test in the scratchpad (never committed):
   1. Do `MCPServer.custom_route` routes still bypass `TransportSecuritySettings`? The webhooks
      and `/patch/*` links depend on it (ADR-010, ADR-020).
   2. Does v2's result validation accept an `is_error=True` `CallToolResult` whose
      `structured_content` doesn't match the tool's output schema? v1 skipped that check for a
      passed-through `CallToolResult`.
   3. Which supported hook sees every tool call with its name, `_meta` and outcome: the
      low-level `server.middleware` (marked provisional) or something on `MCPServer`?
   4. What does `MCPServer.call_tool()` return, and is there a public tool listing to set
      annotations on (replacing `_tool_manager.list_tools()`)?
   5. Do `httpx2` and the integrations' `httpx>=0.27` coexist in one lock?
   6. Do `sse_app()` and `run_stdio_async()` still exist (`MCP_TRANSPORT=sse|stdio`)?
   7. Which headers does a v2 server require on a 2026-era POST (`MCP-Protocol-Version`,
      `Mcp-Method`, `Mcp-Name`)?

   Write the answers into this plan's Open questions. Verify: each answer cites the scratch
   output or the SDK source line. **If 1 or 2 comes back "no", stop and re-plan.**
1. **ADR-022, Proposed:** "Adopt MCP 2026-07-28 via Python SDK v2". Decision table above;
   rejected options: stay on 1.x, wait for clients to move first, adopt subscriptions and
   SEP-2322 now. Add it to `docs/ADRs/README.md`. Verify: the index lists it.
2. **Pin.** `uv add 'mcp>=2.3,<3'`. Verify:
   `uv sync && uv run python -c "from mcp.server.mcpserver import MCPServer"`.
3. **Mechanical port.** The renames, import moves, and snake_case attributes in the table
   above, in `src/` and `tests/`. No behavior change. Verify: `uv run ruff check .` and
   `uv run pytest --collect-only -q` (collection only, so imports resolve).
4. **`server.py`.** Constructor and `streamable_http_app(transport_security=...,
   stateless_http=True)`; `_run_http` takes host and port from `Settings`;
   `_apply_open_world_hints` on the public listing from step 0.4. `http_app()`'s wrapper
   stays (it still has to start the scheduler once per process), but its docstring changes:
   v2 enters `lifespan=` once at manager startup, not once per session. Verify:
   `uv run pytest tests/test_server_runtime.py tests/test_transport_security.py tests/test_mcp_surface.py -q`.
5. **Tool-call logging redesign** (`logging/tool_calls.py`), on the hook from step 0.3.
   - Log `tool_name`, `client_name`, `client_version`, `request_id`, `success`. Still never
     arguments or results.
   - Keep the reported-error → `is_error=True` conversion with `structured_content`.
   - Update `tests/conftest.py`'s `tool_payload()` for v2's return shape.
   - Rewrite `tests/test_tool_call_logging.py`: a 2026-era call logs its `clientInfo`; one
     without `clientInfo` logs nulls and still succeeds; a legacy call logs; a reported error
     is `isError: true` with the payload intact; an uncaught exception logs `success=False`.
     Mutation-probe each in a throwaway worktree (as the test-suite audit did).

   Verify: `uv run pytest tests/test_tool_call_logging.py -q`, then the full suite:
   `uv run pytest -q`.
6. **`DocsMcpClient`** on v2's `Client` (`mode='auto'`, bearer header, timeout in float
   seconds). Keep the injectable session factory for tests. `test_docs.py`'s in-process test
   covers a 2026-era server and a forced `mode='legacy'` call. Verify:
   `uv run pytest tests/test_docs.py -q`.
7. **Protocol tests** (`tests/test_mcp_stateless.py`), with the strict-fake rule from
   `tests/README.md`. Cover:
   - A 2026-era `tools/call` with no `initialize`; no `Mcp-Session-Id` on the response.
   - `server/discover` lists 2026-07-28 and the legacy versions, plus capabilities.
   - Header and `_meta` version mismatch: 400. Unsupported version: `-32022` with
     `supported`. Missing `clientCapabilities`: `INVALID_PARAMS`, 400.
   - Host allowlist on a sessionless POST: 421. Foreign `Origin`: 403.
   - Legacy `initialize` with `stateless_http=True` returns no session id, and a follow-up
     call works.
   - A request built once succeeds against two separately built apps (the restart case).

   Mutation-probe the Host/Origin tests. Verify: `uv run pytest tests/test_mcp_stateless.py -q`.
8. **Optional: real health check.** Replace the `Dockerfile` TCP probe with a `server/discover`
   POST to `127.0.0.1` (already in the default `MCP_ALLOWED_HOSTS`), stdlib `urllib` only.
   Decide at plan approval. Verify: `docker build` and `docker inspect` reports `healthy`; if
   Docker isn't available in the session, say so and leave it for the deploy check.
9. **Docs.**
   - ADR-022 to Accepted.
   - `CLAUDE.md`: Commands are unchanged; update the `FastMCP.custom_route` mentions, the
     tool-call logging description in Key Conventions, the Naming bullet ("the function name
     FastMCP uses by default"), the "No HTTP /health" bullet if step 8 lands, and a Current
     Status entry.
   - `docs/SETUP.md`: a note that 2025-era and 2026-era clients both work, and that restarts
     no longer drop sessions.
   - `docs/plans/2026-09-mutation-testing-remediation.md`: the `server.py` and
     `logging/tool_calls.py` baselines predate this change.

   Verify: `uv run ruff check . && uv run ruff format --check .`, and
   `grep -rn "fastmcp\|FastMCP" src tests CLAUDE.md docs/SETUP.md` shows only intended
   historical mentions.
10. **Full gate.** `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`;
    `uv sync` leaves `uv.lock` clean; then the end-to-end check below.

## End-to-end verification

1. **Run the server.**
   `REGISTRY_DB_PATH=<scratch>/r.db MCP_TRANSPORT=streamable-http uv run registry-mcp`
2. **A 2026-era request, no handshake.** Add the headers step 0.7 found to be required:
   ```bash
   curl -sS -i http://127.0.0.1:8765/mcp \
     -H 'Content-Type: application/json' \
     -H 'Accept: application/json, text/event-stream' \
     -H 'MCP-Protocol-Version: 2026-07-28' \
     -d '{"jsonrpc":"2.0","id":1,"method":"server/discover","params":{"_meta":{
           "io.modelcontextprotocol/protocolVersion":"2026-07-28",
           "io.modelcontextprotocol/clientCapabilities":{},
           "io.modelcontextprotocol/clientInfo":{"name":"curl","version":"0"}}}}'
   ```
   Expect 200, supported versions in the body, and no `Mcp-Session-Id` header. Repeat with
   `tools/call` for `registry_list_services`.
3. **Restart and repeat.** Stop and start the server, then resend step 2's `tools/call`
   unchanged. Expect 200.
4. **Legacy client.** A scratch script under `uv run --with 'mcp<2'` does `initialize` and a
   tool call, the server restarts, and the same client's next call still succeeds.
5. **After deploy.** Claude Desktop and Claude Code each call a tool; the log line names the
   client. Redeploy the stack through Dockhand and confirm the next call works without
   reconnecting.
6. **Docs passthrough**, when `DOCS_MCP_URL` is configured: `get_service_documentation`
   returns text.

## Rollback

Revert the merge. No database schema or setting changes, so nothing else needs undoing. The
`tool_call` log line loses `session_id` and gains `client_name`, `client_version` and
`request_id`; anything that queries those logs needs the same revert.

## Open questions

Answered by step 0; until then, unknown.

- [ ] Do custom routes still bypass the `/mcp` Host/Origin check?
- [ ] Is an `is_error` result with off-schema `structured_content` accepted?
- [ ] Which hook replaces the `_tool_manager.call_tool` patch?
- [ ] What replaces `_tool_manager.list_tools()` for annotations?
- [ ] Do `httpx2` and `httpx` coexist?
- [ ] Do `sse_app()` and `run_stdio_async()` remain?
- [ ] Which headers must a 2026-era POST carry?
- [ ] Is documentation-mcp on SDK v2 yet? If not, the docs lookup falls back to the handshake
      until it is.

## Sources

- [SEP-2575: Make MCP Stateless](https://modelcontextprotocol.io/seps/2575-stateless-mcp)
- [2026-07-28 specification release candidate](https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/)
- [`mcp` on PyPI](https://pypi.org/project/mcp/) and
  [Python SDK releases](https://github.com/modelcontextprotocol/python-sdk/releases)
  (1.x maintenance-mode policy, v2.x notes)
- [What's new in Python SDK v2](https://py.sdk.modelcontextprotocol.io/whats-new/),
  [v1 → v2 migration guide](https://py.sdk.modelcontextprotocol.io/migration/),
  [Deploy and scale](https://py.sdk.modelcontextprotocol.io/run/deploy/index.md)
