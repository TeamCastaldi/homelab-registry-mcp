# Glances diagnostic dashboard (HTMX + Jinja2)

**Status:** Plan approved 2026-10-06. Not started; step 1 is next.
**Produces:** ADR-021 and SOP-008 (written in step 9).

## Context

The registry knows *what* runs where (`HardwareNode`, `Service`) but has no live view of host
health. This adds a read-only, browser-facing dashboard: one card per hardware node with CPU %,
memory %, disk I/O, network I/O, and container status, refreshed by HTMX polling and fed by a
Glances daemon on each host. No Node.js, no JS build step: Jinja2 on the server, a vendored
htmx 2.x file, and one small hand-written JS file.

### Decisions settled in the interrogation

| Topic | Decision | Why |
|---|---|---|
| CORS / data path | **Server-side fan-out.** The browser polls only same-origin `/dashboard/...` routes. The registry fetches Glances with async httpx and returns Jinja HTML fragments. | Browser-direct polling hits four walls at once: mixed content (HTTPS page → `http://host:61208`), Glances returns JSON but HTMX swaps HTML, htmx 2 refuses cross-origin requests (`selfRequestsOnly`), and the `HX-*` headers force a CORS preflight on every poll. The "don't block the event loop" worry doesn't apply: every integration already uses `httpx.AsyncClient`, which yields the loop while waiting. Load is bounded instead by a cache, single-flight requests, and a semaphore. |
| Offline hosts | **Stale, then offline.** Miss 1–2: last values dimmed and labelled "stale". Miss 3: OFFLINE, and polling backs off to 30s. A node whose registry `status` is `offline` renders offline immediately and still polls at 30s, so it recovers on its own. | One dropped request doesn't flicker the card. |
| Intervals | **Metrics every 5s, containers every 30s.** Polls are skipped while the tab is hidden. | Glances refreshes every 2s by default, so 5s is never stale by more than one Glances cycle. |
| Glances version | **4.x, `/api/4` only.** Glances isn't deployed yet. | Glances 4 replaced the v3 API. |
| Access | **Authentik ForwardAuth** on a dedicated Traefik router for `/dashboard`, **plus an in-app proxy-secret header check** (see Security). | Docker publishes the same app on `0.0.0.0:8765`, so without the in-app check ForwardAuth is trivially bypassed. |
| Glances address | **Per-node curated `glances_url`** on `HardwareNode`. Nodes without one show "telemetry not configured" and are never polled. | Fits a host-by-host Glances rollout. |
| Pressure signals | **CPU % and memory % only** (from Glances `quicklook`). | Your choice. Load, swap and PSI are out of v1. |
| VMs | **Containers only for now.** A VM appears only as its own host card, if it runs Glances. | No Proxmox integration in v1. |

## Architecture

```
Browser (https://registry-mcp.<domain>/dashboard)
  │ Traefik router "registry-dashboard": PathPrefix(/dashboard)
  │   middlewares: authentik ForwardAuth → headers(X-Registry-Dashboard-Secret)
  ▼
registry-mcp (Starlette routes added via FastMCP.custom_route)
  GET /dashboard                         → full page (Jinja: index.html)
  GET /dashboard/hosts/{id}/metrics      → fragment (_metrics.html), self-replacing poller
  GET /dashboard/hosts/{id}/containers   → fragment (_containers.html), self-replacing poller
  GET /dashboard/static/{name}           → allowlisted vendored assets
        │
        ▼ TelemetryHub (in-process cache, single-flight per node, semaphore 16)
        ▼ GlancesClient (async httpx, 2s timeout, no redirects, 1 MiB cap, optional Basic auth)
  http(s)://<glances_url>/api/4/{quicklook|diskio|network|containers}
```

Data flow per tick: an HTMX poll reaches the route. The route asks the hub for the node's
metrics. If the cached snapshot is younger than 0.8 × interval, the hub returns it. Otherwise it
polls Glances once for that node, and concurrent viewers share that one in-flight request. The
route then renders the fragment, carrying the next `hx-trigger` interval. **Load scales with
hosts, not viewers**, and drops to zero when nobody has the page open.

## File structure additions

```
src/registry_mcp/
├── integrations/glances/
│   ├── __init__.py
│   ├── client.py      # GlancesClient, GlancesError, normalize_glances_url(), build_glances_client()
│   └── parse.py       # pure: quicklook/diskio/network/containers JSON → typed summaries
├── dashboard/
│   ├── __init__.py
│   ├── routes.py      # register_dashboard_routes(): fail-closed gate, secret guard, 4 GET routes
│   ├── telemetry.py   # TelemetryHub, HostTelemetry, HostState (live/stale/offline/unconfigured/pending)
│   ├── render.py      # Jinja2 Environment (PackageLoader, autoescape, StrictUndefined) + filters
│   ├── templates/
│   │   ├── base.html  _host_card.html  _metrics.html  _containers.html  index.html
│   └── static/
│       ├── htmx-2.x.y.min.js   # vendored, pinned; VENDORED.md records version, source URL, sha256
│       ├── dashboard.js        # ~100 lines: sorting, hidden-tab gating, connection/auth banner
│       └── dashboard.css
docs/ADRs/ADR-021-Glances-Diagnostic-Dashboard.md
docs/SOPs/SOP-008-Connect-Glances-Dashboard.md
tests/test_glances_client.py  tests/test_glances_parse.py
tests/test_dashboard_telemetry.py  tests/test_dashboard_routes.py
```

Modified: `config.py`, `config_report.py` (`_FEATURES`), `server.py` (`build_app` wiring),
`models/hardware.py`, `hardware/store.py`, `tools/hardware.py` (docstrings and the add-node
param), `pyproject.toml` + `uv.lock` (`jinja2>=3.1` promoted to a direct dependency; it is
already in the lock as 3.1.6), `docker-compose.yml` (commented labels and env pass-throughs),
`.env.example`, `CLAUDE.md`, and `docs/ADRs/README.md`.

Hatch packages everything under `src/registry_mcp`, so templates and static files ship in the
wheel. The Docker image runs `uv sync` against `src/` directly.

## Python: settings and wiring

**`config.py`** (follows the PatchMon API group pattern; credentials are `SecretStr`):

| Setting | Default | Notes |
|---|---|---|
| `DASHBOARD_ENABLED` | `false` | Off by default. |
| `DASHBOARD_PROXY_SECRET` | unset | `SecretStr`, **required**. Without it the routes are never mounted. |
| `DASHBOARD_METRICS_INTERVAL_SECONDS` | `5` | `Field(ge=2)` |
| `DASHBOARD_CONTAINERS_INTERVAL_SECONDS` | `30` | `Field(ge=5)` |
| `GLANCES_TIMEOUT_SECONDS` | `2` | `Field(gt=0)`. If ≥ the metrics interval, registration logs a warning. |
| `GLANCES_USERNAME` / `GLANCES_PASSWORD` | `glances` / unset | Optional HTTP Basic, matching Glances `--password`. One credential for every host. |

Module constants, not settings: `OFFLINE_AFTER_MISSES = 3`, `OFFLINE_POLL_SECONDS = 30`,
`MAX_CONCURRENT_POLLS = 16`, `MAX_BODY_BYTES = 1 MiB`.

**`config_report._FEATURES`**:
`_Feature("Dashboard", lambda s: s.dashboard_enabled, ("dashboard_proxy_secret",), (intervals, glances_*))`.

**`server.build_app`** (`server.py` ~L296–310, next to `register_patchmon_routes`): build
`GlancesClient` and `TelemetryHub` only when the dashboard is enabled, then call
`register_dashboard_routes(mcp, settings, hardware_store, hub)`. The dashboard only reads, so it
**ignores `read_only`**: unlike the webhooks, it keeps working in read-only mode. No new MCP
tools, so `_CLOSED_WORLD_TOOLS` doesn't change.

## Python: inventory change (`glances_url`)

- `models/hardware.py`: `glances_url: str | None = None`. It is curated: discovery never writes it.
- `hardware/store.py`:
  - Add a `_migrate(engine)` modelled on `RegistryStore._migrate` (`registry/store.py:81-104`), using `PRAGMA table_info` then `ALTER TABLE ... ADD COLUMN glances_url VARCHAR DEFAULT NULL`. Call it from `__init__` after `create_all`. This is HardwareStore's first migration.
  - Add `glances_url` to `_MUTABLE_FIELDS`.
  - Run a per-field validator in `update_node` (~L211), beside the `_ENUM_FIELDS` check: `normalize_glances_url` raises `ValueError`, which the tool already turns into `{"error": "invalid update: ..."}`.
  - `""` clears the field. `None` can't, because `None` is skipped today.
  - `upsert_from_discovery` must leave the field alone (add a test).
- `normalize_glances_url(raw)`, in `integrations/glances/client.py`:
  - Scheme must be `http`/`https`, with a host.
  - **No userinfo**: credentials belong in `GLANCES_PASSWORD`.
  - No query or fragment; path is empty or `/`.
  - Trailing slash stripped.
  - The poll path re-checks it, for rows edited by hand.
- `hardware-add-node` gets an optional `glances_url` param. `hardware-update-node` takes it through its existing `updates` dict, so no signature change and no tool renames.

## Python: Glances client and parsers

**`GlancesClient.fetch(base_url, plugin)`**, modelled on `integrations/patchmon/client.py`:
- **Only allowlisted plugins**: `quicklook`, `diskio`, `network`, `containers`. `processlist`, whose command lines can carry secrets, is never requested.
- Builds `f"{base}/api/4/{plugin}"`.
- `follow_redirects=False`. Reads the body streamed, under `MAX_BODY_BYTES`.
- **No retries**: the next poll is the retry, and a retry would blow the 2s budget.
- Raises `GlancesError(RuntimeError)` with a short reason (`timeout`, `connect refused`, `HTTP 401`, `bad JSON`, `too large`). The reason never contains the credential.
- `fetch_many(base_url, plugins)`: one `httpx.AsyncClient` per host poll, plugins fetched with `asyncio.gather`, so they share a keep-alive connection.
- `transport=` is injectable for tests, like the other clients.

**`parse.py`**: pure functions. Every value is coerced and clipped, since agents write this data.
They reuse `webhooks/common.one_line` for strings.
- `quicklook(d) -> (cpu_pct | None, mem_pct | None)`, from the `cpu` and `mem` fields.
- `diskio(entries) -> (read_Bps, write_Bps)`:
  - Sums **whole disks only**: `^(sd|vd|xvd|hd)[a-z]+$`, `^nvme\d+n\d+$`, `^mmcblk\d+$`. This excludes partitions, `dm-*`, `loop*`, `md*` and `zram*`, which would double-count.
  - Falls back to all entries if nothing matches.
  - Reads the `*_bytes_rate_per_sec` fields.
- `network(entries) -> (rx_bps, tx_bps)`:
  - Sums **physical-looking interfaces only** (`^(en|eth|wl|ww)`), which excludes `lo`, `veth*`, `docker*`, `br-*`, `vmbr*`, `tap*`, `fw*` and bonds over members.
  - Falls back to all non-`lo` interfaces where `is_up` is true.
  - Rates come in as bytes/s and are shown as bits/s.
- `containers(entries) -> list[Container(name, status, image, uptime)]` plus running/total counts. Status is lowercased; anything unexpected becomes `unknown`.
- Glances response field names come from its docs. As with Dockhand's client, the module docstring says they are unverified against a live instance until SOP-008's validation step.

## Python: TelemetryHub and the state machine (`dashboard/telemetry.py`)

`HostTelemetry` (per node id) holds:
- `last_good` (cpu, mem, disk, net) and `last_success_at`
- `misses: int`, `last_error: str`, `in_flight: asyncio.Task | None`
- `containers` / `containers_at` / `containers_error`

`TelemetryHub(client, settings, now=utcnow)` (the clock is injectable for tests):
- `async metrics(node) -> HostView`
  1. No `glances_url` → `unconfigured`, never polled.
  2. Fresh cache (age < 0.8 × interval) → return it.
  3. Otherwise await the shared in-flight task (single-flight), under the global semaphore.
  4. Success: `misses = 0`. Failure: `misses += 1`.
  5. State = `live` if `misses == 0`; `stale` for 1–2 misses; `offline` from 3 misses, or when the registry status is `offline` and nothing has succeeded since startup.
  6. Log **transitions only** (`dashboard_host_stale` / `_offline` / `_recovered`), never every poll.
- `async containers(node) -> ContainersView`
  - Skips Glances while the host is `offline`.
  - On failure it keeps the last-good list, dimmed, with the reason. It does **not** count a miss: the metrics poll owns host state.
- `prune(known_ids)` runs on every full-page render, so the cache stays bounded to current nodes.

## Python: routes (`dashboard/routes.py`)

`register_dashboard_routes(mcp, settings, hardware_store, hub)`:
- **Fail-closed, the same shape as `webhooks/patchmon.py:185-207`**:
  - Disabled → return.
  - Enabled with no or blank `DASHBOARD_PROXY_SECRET` → log `dashboard_disabled` and register nothing.
- Every route first runs `_authorized(request)`: `hmac.compare_digest` on the `X-Registry-Dashboard-Secret` header. A mismatch gets a plain 403 page.
- Every response carries security headers in the same style as `webhooks/approval.py:53-62`, but a CSP that allows its own scripts:
  - `default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`
  - `Cache-Control: no-store` on HTML, `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`
- The four routes:

| Route | Behaviour |
|---|---|
| `GET /dashboard` | `hardware_store.list_nodes()` sorted by display_name; `hub.prune`; renders `index.html` with skeleton cards. Logs the viewer's `X-authentik-username` at info, page views only. |
| `GET /dashboard/hosts/{id}/metrics` | `get_node(id)`. Unknown id → **HTTP 286** (htmx's "stop polling") with a "removed from registry" fragment. Otherwise `await hub.metrics(node)` → `_metrics.html`. Host problems are content, so the response is always 200. |
| `GET /dashboard/hosts/{id}/containers` | Same pattern → `_containers.html`. |
| `GET /dashboard/static/{name}` | A dict allowlist `{name: (package file, media type)}` read through `importlib.resources`. Anything else is a 404, so path traversal isn't possible. `Cache-Control: public, max-age=86400`; URLs carry `?v=<package version>`. |

- SQLite reads run on the event loop, as the existing routes do (`approval.py`); they are sub-millisecond. Jinja rendering is CPU-only and small.

## HTMX / Jinja component hierarchy

```
base.html
 ├─ <meta name="htmx-config" content='{"selfRequestsOnly":true,"allowEval":false,
 │        "allowScriptTags":false,"includeIndicatorStyles":false,"historyCacheSize":0}'>
 ├─ <link dashboard.css>  <script defer htmx.min.js>  <script defer dashboard.js>
 └─ {% block body %}
index.html (extends base)
 ├─ header: title, "n hosts", sort toolbar  <button data-sort-key="name|state|cpu|mem|disk|net|containers" aria-pressed>
 ├─ <div id="conn-banner" hidden role="status">        ← shown by dashboard.js on registry/auth loss
 └─ <main id="hosts" class="grid" data-sortable>
      {% for node in nodes %}{% include "_host_card.html" %}{% endfor %}
_host_card.html   <article class="card" id="node-{{id}}" data-name data-role>
 ├─ card header: display_name, hostname, role badge, registry status badge
 ├─ _metrics.html     (initial: state=pending)
 └─ _containers.html  (initial: state=pending)
_metrics.html     <section data-metrics data-state data-cpu data-mem data-disk data-net
                           hx-get=".../metrics" hx-swap="outerHTML" hx-trigger="{{ trigger }}">
 ├─ CPU  <meter min=0 max=100 low=70 high=90 optimum=0 value=cpu>  + "42 %"
 ├─ Mem  <meter …>                                                    + "61 %"
 ├─ Disk "R 12.3 MB/s · W 1.1 MB/s"     Net "↓ 84 Mbit/s · ↑ 3 Mbit/s"
 └─ status line: "live · 3s ago" | "stale · last data 17s ago (timeout)" | "offline since 2m (connect refused)"
_containers.html  <details data-containers hx-get=".../containers" hx-swap="outerHTML" hx-trigger="{{ trigger }}">
 ├─ <summary> "Containers 12/14 running" (+ warning class when any aren't running)
 └─ rows: name · status pill · image · uptime  (server-ordered: not-running first, then name)
```

Trigger rules, rendered server-side into each fragment:
- **Initial skeleton**: `load` only (containers use `load delay:500ms` to stagger the first burst).
- **Returned fragments**: `every {interval}s, dashboard-refresh from:body`, where the interval is 5s for live/stale metrics, 30s when offline, and 30s for containers.
- **Unconfigured**: no `hx-*` attributes, so nothing polls.
- **Pitfall, pinned by a test**: a returned fragment must never contain `load`. It would refire on every swap and loop as fast as the server answers.

The CSP has no `'unsafe-inline'`, so there are no inline `style=` attributes: bars use `<meter>`, and state is shown through CSS classes. `allowEval:false` means there are no `hx-on` or trigger filters. All behaviour lives in `dashboard.js`.

## Client-side sorting (no libraries): `dashboard.js`

1. **Sort by moving DOM nodes, not CSS `order`.**
   - On a toolbar click, read each card's keys from its `[data-metrics]` child (`data-cpu`, …) and the card itself (`data-name`).
   - Compare numerically or by `localeCompare`, then `grid.append(...sortedCards)`.
   - Moving an element keeps its htmx polling: htmx only checks that the element is still in the document.
   - Unlike CSS `order`, focus and screen-reader order follow the visual order.
   - Cards with no value (`offline` / `unconfigured` / `pending`) always sort last, whichever direction.
   - Clicking the active key again toggles asc/desc and sets `aria-pressed` / `aria-sort`.
2. **Re-apply after updates.**
   - On `htmx:afterSettle`, re-sort with a 300ms debounce, so a burst of N swaps causes one reorder.
   - If focus was inside a moved card, restore it.
3. **Persist the choice** in `localStorage` (`dashboard.sort` = `{key, dir}`), wrapped in try/catch and restored on load.
4. **Hidden-tab gating.**
   - `htmx:beforeRequest` → `preventDefault()` when `document.hidden`. htmx keeps rescheduling `every` polls, so this only skips ticks.
   - On `visibilitychange` back to visible → `htmx.trigger(document.body, "dashboard-refresh")` for an immediate catch-up.
5. **Connection and auth banner.**
   - `htmx:sendError` or `htmx:responseError` → show `#conn-banner` and leave the cards as they are. This is the *registry* being unreachable, not the hosts.
   - Then probe `fetch("/dashboard", {redirect: "manual", cache: "no-store"})`. An `opaqueredirect`, 401 or 403 means the Authentik session expired → `location.reload()`, so the login page shows instead of failing polls.
   - The next successful `htmx:afterRequest` hides the banner.

## Security notes (go into ADR-021)

- **The bypass the proxy secret closes.** `docker-compose.yml` publishes `0.0.0.0:8765`, which reaches the app without Traefik.
  - Traefik's `headers` middleware *overwrites* `X-Registry-Dashboard-Secret`, so a client can't forge it through Traefik.
  - Direct-port clients don't know it.
  - It also defeats DNS-rebinding and cross-site reads: `/mcp`'s Host/Origin check doesn't cover custom routes (`server.py:128-130`).
  - The secret is readable by anyone who can read Traefik's API or run `docker inspect`. That is the same group that can already query `/mcp` on :8765, which exposes a superset of this data unauthenticated. ADR-021 records Authentik JWT verification (`X-authentik-jwt` against the provider's JWKS; `pyjwt[crypto]` is already in the lock via `mcp`) as the follow-up hardening.
- **Glances is read-only and allowlisted.** It joins CLAUDE.md's "Upstream APIs are read-only" list. SOP-008 tells operators to:
  - firewall `61208` to the control-plane IP,
  - run `-w --disable-webui`,
  - disable `processlist`.
  Basic auth over plain HTTP is noted as weak on its own.
- **Untrusted values.** Glances values are untrusted (agents report them). Jinja autoescape plus `StrictUndefined` handle markup; numbers are coerced and bounded, strings flattened and clipped.
- **Outbound requests are pinned down.** `glances_url` comes from a trusted MCP caller, and the server then calls that address. Each request is limited to a fixed path, no redirects, and a size cap.

## Docs and config

- **`docs/ADRs/ADR-021-Glances-Diagnostic-Dashboard.md`.**
  - Decision: everything above.
  - Rejected: browser-direct polling (four blockers), SSE push (more machinery than v1 needs), a background poller (load with no viewers).
  - Amends three CLAUDE.md conventions: browser-facing routes (adds `/dashboard`), upstream read-only (adds Glances), and the `custom_route` consumers line.
  - Also update the `docs/ADRs/README.md` index.
- **`docs/SOPs/SOP-008-Connect-Glances-Dashboard.md`.**
  1. Glances per node as `nodes/<node>/glances/compose.yaml` in the homelab repo (ADR-007): `nicolargo/glances:<pinned 4.x>-full`, `network_mode: host`, `pid: host`, `docker.sock:ro`, and `GLANCES_OPT="-w --disable-webui --disable-plugin processlist"`.
  2. Firewall port 61208.
  3. `hardware-update-node {glances_url}`.
  4. Traefik labels and the Authentik application, including the `/outpost.goauthentik.io/` route.
  5. `DASHBOARD_*` keys in Infisical.
  6. Validation: compare the live field names with `parse.py`.
- **`docker-compose.yml`**: commented example labels, plus `DASHBOARD_*`/`GLANCES_*` env pass-throughs:
  ```yaml
  traefik.http.routers.registry-dashboard.rule: "Host(`registry-mcp.<your-domain>`) && (Path(`/dashboard`) || PathPrefix(`/dashboard/`))"
  traefik.http.routers.registry-dashboard.priority: "100"
  traefik.http.routers.registry-dashboard.entrypoints: "websecure"
  traefik.http.routers.registry-dashboard.tls: "true"
  traefik.http.routers.registry-dashboard.service: "registry-mcp"
  traefik.http.routers.registry-dashboard.middlewares: "<authentik-forwardauth>@<provider>,registry-dashboard-secret"
  traefik.http.middlewares.registry-dashboard-secret.headers.customrequestheaders.X-Registry-Dashboard-Secret: "${DASHBOARD_PROXY_SECRET}"
  ```
- **`CLAUDE.md`**: Project Structure (`dashboard/`, `integrations/glances/`), Architecture section, env-var table, conventions above, Current Status. **`.env.example`**: new keys.

## Implementation steps (one at a time, each verified before the next)

1. **Dependency.** `uv add 'jinja2>=3.1'`. Verify: `uv sync && uv run python -c "import jinja2"`.
2. **Settings and feature entry** (`config.py`, `config_report.py`, `.env.example`). Verify: `uv run pytest tests/test_config_report.py -q`, and `DASHBOARD_ENABLED=true uv run registry-mcp-config-check` reports the missing `DASHBOARD_PROXY_SECRET`.
3. **`glances_url`**: model, `HardwareStore._migrate`, validator, add-node param. Tests: migration of an old-schema DB, validation rejects (`file://`, userinfo, query), `""` clears, discovery upsert leaves it alone. Verify: `uv run pytest tests/test_hardware*.py -q`.
4. **`integrations/glances/parse.py` + tests** (table-driven disk and NIC filters, fallbacks, junk input). Verify: `uv run pytest tests/test_glances_parse.py -q`.
5. **`integrations/glances/client.py` + tests.** The `httpx.MockTransport` fake is no more forgiving than Glances (tests/README.md): 401 without Basic auth when a password is set, 404 for unknown plugins. Tests cover the allowlist, no redirect, timeout, size cap, and that the credential never appears in errors. Verify: `uv run pytest tests/test_glances_client.py -q`.
6. **`dashboard/telemetry.py` + tests.** Cover live→stale→offline→recovered with the injected clock; N concurrent callers → 1 fetch; the fresh-cache hit; registry-offline nodes start offline; containers skipped while offline; `prune`. Verify: `uv run pytest tests/test_dashboard_telemetry.py -q`.
7. **Templates, `render.py`, static assets.** Vendor htmx 2.x and record its sha256 in VENDORED.md; write `dashboard.js` and `.css`. Verify: render smoke tests in `tests/test_dashboard_routes.py` (next step).
8. **`dashboard/routes.py` + `server.py` wiring + tests** (ASGITransport, as in `tests/test_patchmon_webhook.py:230`). Cover:
   - Disabled → 404. Enabled without a secret → 404 and `dashboard_disabled` logged.
   - Missing or wrong header → 403.
   - Every node listed, with a `<script>` display_name escaped.
   - Trigger strings: 5s live, 30s offline, none when unconfigured, never `load` in returned fragments.
   - Unknown id → 286.
   - Static allowlist (`..%2F` → 404); CSP and headers present.
   - Works in read-only mode.

   Verify: `uv run pytest tests/test_dashboard_routes.py -q`.
9. **Docs**: ADR-021, SOP-008, `CLAUDE.md`, compose comments, ADR index. Verify: `uv run ruff check . && uv run ruff format --check .`.
10. **Full gate.** `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`, then the end-to-end check below.

## End-to-end verification

1. **Fake Glances.** A throwaway Starlette app in the scratchpad serves fixture JSON for the four `/api/4/*` plugins on `:61208`. A "down" toggle makes it refuse connections.
2. **Run the server.**
   `DASHBOARD_ENABLED=true DASHBOARD_PROXY_SECRET=test REGISTRY_DB_PATH=<scratch>/r.db MCP_TRANSPORT=streamable-http uv run registry-mcp`
   Then add two nodes, one with `glances_url=http://127.0.0.1:61208` and one without.
3. **Playwright** (Chromium pre-installed at `/opt/pw-browsers`), sending the header `X-Registry-Dashboard-Secret: test`:
   - Screenshot `/dashboard`: one live card, one "not configured".
   - Stop the fake: stale after the first miss, OFFLINE after three, then requests every 30s (counted in the fake's log).
   - Restart it: the card recovers.
   - Click Sort by CPU: DOM order changes, and survives the next poll and a reload (localStorage).
   - Hide the tab (`page.evaluate` visibility override): no requests.
   - Without the header: 403.
4. **Packaging.** `uv build` and `unzip -l dist/*.whl | grep dashboard/` show the templates and static files.
