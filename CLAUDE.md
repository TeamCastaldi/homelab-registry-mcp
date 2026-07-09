# homelab-registry-mcp

Python MCP server that is the authoritative service catalog for a homelab. It discovers services from Traefik, Docker, and Authentik; maintains a curated SQLite registry; and exposes the data as MCP tools, resources, and prompts for AI agents.

## Commands

```bash
uv sync                                 # install/sync deps (always run after pulling)
uv run registry-mcp                     # start server (stdio by default)
uv run registry-mcp-seed <file.yaml>    # idempotent YAML bootstrap

uv run pytest                           # run all tests
uv run pytest tests/test_linking.py -v  # run a specific test file
uv run ruff check .                     # lint
uv run ruff format .                    # format (line-length: 100)
```

CI runs `ruff check`, `ruff format --check`, and `pytest -q` on every push.

## Project Structure

```
src/registry_mcp/
├── server.py              # FastMCP wiring — register all tools here
├── config.py              # pydantic Settings (env vars → typed config)
├── models/
│   ├── service.py         # Service, ServiceSource (SQLModel tables)
│   ├── event.py           # ChangeEvent, DiscoveryEvent (audit log)
│   ├── hardware.py        # HardwareNode, HardwareChangeEvent, NodeRole, NodeStatus
│   └── proposal.py        # Proposal, FindingType, ProposalStatus (Phase 8)
├── registry/
│   ├── store.py           # SQLite CRUD + event recording
│   └── reconcile.py       # Match discovered candidates → registry entries
├── discovery/
│   ├── base.py            # DiscoverySource protocol
│   ├── engine.py          # Orchestrates discovery passes
│   ├── scheduler.py       # APScheduler wiring
│   ├── traefik.py / docker.py / authentik.py  # source implementations
├── dspy/                  # reasoning layer (Phase 7) — DSPy enrichment, confidence-gated
│   ├── signatures.py      # ResolveServiceIdentity, InferServiceMetadata, SummarizeAccessAudit, GenerateRemediationPatch
│   └── reasoner.py        # Reasoner: lazy LM config, gates, graceful degradation
├── hardware/              # hardware node registry (Phase 9a)
│   └── store.py           # HardwareStore: node CRUD, service linking, capacity summary
├── proposal/              # proposal layer (Phase 8) — opens PRs, never merges/writes FS
│   ├── generator.py       # calls DSPy GenerateRemediationPatch + confidence/YAML gates
│   ├── engine.py          # create per finding, verification sweep, after_discovery hook
│   └── store.py           # Proposal CRUD (shares the registry SQLite engine)
├── providers/             # pluggable write-path backends (behind protocols)
│   ├── git/               # GitProvider protocol + Gitea/GitHub impls + factory
│   └── notification/      # NotificationProvider protocol + Ntfy/Null + factory
├── integrations/
│   ├── traefik/           # httpx client + 7 MCP tools + resource + prompt
│   └── authentik/         # httpx client + 8 MCP tools + resource + prompt
├── tools/
│   ├── registry.py        # CRUD: add/get/list/update/delete service
│   ├── events.py          # query change + discovery logs
│   ├── discovery.py       # run_now / status / list_stale
│   ├── linking.py         # service_link_authentik + service_get_full_context
│   ├── hardware.py        # hardware-add-node/get/list/update/delete + link/capacity tools
│   ├── secrets.py         # secrets_status/encrypt/decrypt/add/rotate/list_keys (Phase C)
│   └── proposal.py        # proposal_create/list_open/get/cancel/verify (Phase 8)
├── logging/events.py      # structlog config with secret redaction
└── seed.py                # YAML bootstrap logic
tests/                     # mirrors src/ layout; uses in-memory SQLite
```

## Architecture

**Data model (SQLite via SQLModel):**
- `Service` — canonical record; curated fields (display_name, category, tags, notes) are never overwritten by discovery
- `ServiceSource` — provenance: one row per source that reported the service
- `ChangeEvent` — append-only audit log of every field change
- `DiscoveryEvent` — one row per discovery pass per source (counts, status, error)

**Discovery flow:**
1. APScheduler fires each enabled source on its configured interval
2. Source's `discover()` returns `DiscoveredService` candidates
3. Reconciler matches by name → `traefik_router` → shared URL host
4. Match → update provenance fields only; no match → create with `manual=False`
5. Services missing for `DISCOVERY_STALE_AFTER_MISSES` (default 3) passes are marked `stale=True` — never hard-deleted

**Cross-source linking (Phase 7):**
- Authentik proxy provider `external_host` matched against Traefik router rule hosts
- Traefik `service_name` matched against Docker container labels
- `service_get_full_context(id)` returns service + router + auth app + recent events in one call

**Hardware node registry (Phase 9a, `hardware/`):** curated inventory of physical and virtual
nodes, stored in the same SQLite database as services.
- `HardwareNode` — one row per node: hostname, role (`pve_host`, `docker_host`, `nas`, `pi`, etc.),
  status (`confirmed`/`unconfirmed`/`stale`/`offline`), IP/MAC, CPU, RAM, GPU, structured disk and
  storage-pool lists, Ansible inventory fields, and a `HardwareChangeEvent` audit log.
- 11 MCP tools: `hardware-add-node`, `hardware-get-node`, `hardware-list-nodes`,
  `hardware-update-node`, `hardware-delete-node`, `hardware-link-service`,
  `hardware-node-services`, `hardware-list-unconfirmed`, `hardware-list-stale`,
  `hardware-capacity-summary`, and a stub `hardware-discover-now` (Phase 9b).
- Two MCP resources: `hardware://all` (index) and `hardware://{node_id}` (detail).
- Services can be manually linked to nodes via `hardware-link-service`; the link is
  surfaced in `service_get_full_context()`.
- Live Ansible fact-gather discovery (`hardware-discover-now`) is a Phase 9b stub —
  registration is currently manual via `hardware-add-node`.

**Reasoning layer (Phase 7, `dspy/`):** DSPy enrichment modules, off by default
(`DSPY_ENABLED=false`). They *reason and return typed results — they never write*.
The detection layer (`reconcile.py`) and discovery engine stay LLM-free: the engine
injects the reasoner's callables into `store.reconcile`, so `reconcile.py` never imports
dspy. Three modules, each confidence-gated (DSPy 3.x removed `dspy.Assert`, so gates are
explicit threshold checks; below threshold → discard and fall back to deterministic):
- `ResolveServiceIdentity` — fuzzy cross-source match *only when deterministic matching fails*
- `InferServiceMetadata` — infer display_name/category/auth_mode/notes for new Traefik-only services
- `SummarizeAccessAudit` — backs the additive `authentik_summarize_events` tool

**Proposal layer (Phase 8, `proposal/` + `providers/`):** degree-3 agentic write
path — opens one PR per finding, never merges, never writes the filesystem.
Off by default; requires `GIT_BASE_URL`/`GIT_TOKEN`/`GIT_REPO` to be configured
at all, and `PROPOSAL_AUTO_CREATE=true` for unattended creation.
- `GenerateRemediationPatch` (DSPy) produces the **complete corrected file**;
  the generator gates on `PROPOSAL_CONFIDENCE_THRESHOLD` (0.8) and YAML validity.
  There is no rule-based fallback — a failed/low-confidence/invalid patch is
  recorded as a `rejected` Proposal and flagged for manual review, never committed.
- Flow per finding: read current file from Git → DSPy patch → gate → branch →
  commit → open PR (labelled) → notify → persist `Proposal`. `PROPOSAL_DRY_RUN=true`
  stops before any Git write and returns the patch for review.
- The engine consumes `GitProvider`/`NotificationProvider` protocols (Gitea/GitHub + Ntfy/Null
  shipped); the discovery engine's `on_pass_complete` hook runs the verification sweep
  (and auto-create when enabled) after each pass — wrapped so it never breaks discovery.

**A source only runs when its upstream env var is set** (e.g., no Traefik discovery if `TRAEFIK_API_URL` is unset).

## Environment Variables

| Variable | Default | Notes |
|---|---|---|
| `TRAEFIK_API_URL` | unset | Enables Traefik discovery; e.g. `http://traefik.lan:8080` |
| `TRAEFIK_TIMEOUT_SECONDS` | `10` | |
| `TRAEFIK_RETRIES` | `3` | |
| `AUTHENTIK_API_URL` | unset | Enables Authentik discovery; e.g. `https://auth.lan/api/v3` |
| `AUTHENTIK_TOKEN` | unset | **Read-only service-account token only** (never admin) |
| `AUTHENTIK_TIMEOUT_SECONDS` | `10` | |
| `AUTHENTIK_RETRIES` | `3` | |
| `DOCKER_BASE_URL` | unset | Enables Docker discovery; e.g. `unix:///var/run/docker.sock` |
| `REGISTRY_DB_PATH` | `/data/registry.db` | SQLite location |
| `REGISTRY_LOG_PATH` | `/data/events.log` | JSON event log |
| `MCP_TRANSPORT` | `streamable-http` | `stdio`, `sse`, or `streamable-http` |
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `8765` | |
| `DISCOVERY_TRAEFIK_INTERVAL_SECONDS` | `300` | |
| `DISCOVERY_DOCKER_INTERVAL_SECONDS` | `300` | |
| `DISCOVERY_AUTHENTIK_INTERVAL_SECONDS` | `900` | |
| `DISCOVERY_STALE_AFTER_MISSES` | `3` | |
| `DSPY_ENABLED` | `false` | Enables the Phase 7 reasoning layer (identity/metadata enrichment + audit summaries) |
| `DSPY_MODEL` | `anthropic/claude-haiku-4-5-20251001` | litellm model id for the reasoning LM |
| `DSPY_API_KEY` | unset | Falls back to `ANTHROPIC_API_KEY` env when unset |
| `DSPY_CONFIDENCE_THRESHOLD` | `0.7` | Below this, reasoning output is discarded and the deterministic path applies |
| `DSPY_MAX_TOKENS` | `1024` | Max output tokens per reasoning call |
| `DSPY_COMPILED_PATH` | unset | Dir of optimized modules saved by a Phase 9 pass; loaded at startup if present |
| `GIT_PROVIDER` | `gitea` | `gitea` (also Forgejo) or `github` (GitHub.com by default; for GHES set `GIT_BASE_URL` to its API root, e.g. `https://ghe.example.com/api/v3`); `gitlab` reserved (not yet implemented) |
| `GIT_BASE_URL` / `GIT_TOKEN` / `GIT_REPO` | unset | Enables the write path; repo is `owner/name`. All three required |
| `GIT_BASE_BRANCH` | `main` | Branch PRs target |
| `NOTIFICATION_PROVIDER` | `none` | `ntfy` or `none` |
| `NOTIFICATION_URL` / `NOTIFICATION_TOPIC` / `NOTIFICATION_TOKEN` | unset / `homelab-registry` / unset | Ntfy push config |
| `APPLY_MODE` | `manual` | `manual` or `ansible` — shapes PR description only |
| `PROPOSAL_AUTO_CREATE` | `false` | Open PRs automatically on discovery passes |
| `PROPOSAL_DRY_RUN` | `false` | Generate + log patches without opening PRs |
| `PROPOSAL_STALE_DAYS` | `7` | Open proposals older than this are logged as stale |
| `PROPOSAL_CONFIDENCE_THRESHOLD` | `0.8` | Below this a patch is rejected for manual review |
| `PROPOSAL_LABEL` | `homelab-registry-mcp` | Label applied to opened PRs |
| `PROPOSAL_COMPOSE_PATH_TEMPLATE` | `nodes/{node}/{service}/compose.yaml` | Repo path an app service maps to |
| `PROPOSAL_COMMENT_POLL_ENABLED` | `false` | Poll open proposal PRs for comments and push a DSPy-revised commit in response (never scheduled in read-only mode) |
| `PROPOSAL_COMMENT_POLL_INTERVAL_SECONDS` | `300` | Poll interval (seconds) when `PROPOSAL_COMMENT_POLL_ENABLED=true` |
| `PROPOSAL_COMMENT_ALLOWED_USERS` | unset | Comma-separated GitHub/Gitea usernames trusted to trigger a revision. **Fails closed** — empty means every comment is ignored |
| `NORMALIZATION_ENABLED` | `false` | Reserved; normalization engine is a later Phase 8 increment |
| `NORMALIZATION_SCHEDULE` | `weekly` | Reserved |
| `SECRETS_ENABLED` | `true` | Enables `secrets_*` MCP tools (Phase C git-crypt integration) |
| `SECRETS_REPO_PATH` | unset | Absolute path to the cloned private homelab repo on this node. `pydantic-settings` reads `.env` as literal strings — `$HOME`/`~` are not expanded, so use a concrete absolute path (e.g. `/opt/homelab` on the Pi, `/Users/you/homelab` on macOS) |
| `SECRETS_KEY_PATH` | unset | Absolute path to the exported git-crypt key file (priority over env var); same no-expansion caveat as `SECRETS_REPO_PATH` |
| `SECRETS_GIT_CRYPT_KEY` | unset | Base64-encoded git-crypt key bytes (fallback when no key file) |
| `ANSIBLE_CFG_PATH` | unset | Absolute path to `ansible.cfg` on this node; one of three startup health checks (Phase 2) — missing it starts the server in read-only mode |
| `SSH_KEY_PATH` | unset | Absolute path to the control-plane SSH key; same startup health check as `ANSIBLE_CFG_PATH`, same no-expansion caveat |
| `EVENT_RETENTION_DAYS` | `90` | Old events purged on startup |
| `LOG_LEVEL` | `INFO` | |

Copy `.env.example` to `.env` and fill in the upstream URLs before running locally.

## Key Conventions

- **Curated fields are sacred**: `display_name`, `category`, `tags`, `notes` set by humans are never overwritten by discovery. Discovery only updates provenance fields (`host`, `urls`, `traefik_router`, `authentik_app_slug`, `auth_mode`).
- **Never hard-delete discovered services**: mark `stale=True` after threshold misses.
- **Upstream APIs are read-only**: Traefik, Authentik, and Docker are never modified.
- **The write path writes to Git only**: the proposal layer opens PRs; it never merges them and never writes the filesystem Traefik/Docker watch. The PR + human merge is the safety gate. All write behavior defaults off.
- **All patch generation goes through DSPy**: `proposal/generator.py` has no rule-based fallback. Low-confidence or invalid-YAML patches become `rejected` Proposals, never commits.
- **New tools must be registered in `server.py`** — FastMCP doesn't auto-discover them.
- **No LLM calls in the detection layer**: `reconcile.py` and discovery sources stay deterministic. Reasoning (DSPy) lives in `dspy/` and is wired in via injected callables; those layers never `import dspy`.
- **DSPy/`dspy/` subpackage does not shadow the library**: Python 3 absolute imports resolve `import dspy` to the top-level package; the library is imported lazily so a disabled reasoning layer adds no startup cost.
- **Naming**: kebab-case for MCP tool names, snake_case for Python, PascalCase for classes.
- **Log secrets are redacted**: any field named `token`, `password`, `secret`, `key`, `authorization`, `api_key` is replaced with `***redacted***` before writing to logs.
- **All `secrets_*` paths go through `_check_path`**: every user-supplied path is validated by the shared helper in `tools/secrets.py` — reject absolute paths, reject `..` traversal, then `.resolve()` + `is_relative_to(repo)` as a final containment check (also catches symlink escapes). Never join a repo base with a caller-supplied path without it; `Path(base) / "/etc/passwd"` silently discards `base` and returns `/etc/passwd`.
- **Structured logs go to stderr + file** — keeps stdio JSON-RPC transport clean.
- **No HTTP /health endpoint**: Dockerfile uses a TCP probe on `MCP_PORT`; the streamable-http transport doesn't expose arbitrary HTTP routes.
- **ForwardAuth in front of MCP clients breaks them** (clients don't follow redirects). Auth strategy is deferred; server is LAN-only for now.

## Testing

Tests use `pytest-asyncio` (`asyncio_mode="auto"`) and an in-memory SQLite fixture to avoid touching `.env` or real APIs.

```bash
uv run pytest                            # all tests
uv run pytest -v tests/test_linking.py   # one file
uv run pytest --cov=src                  # with coverage
```

Fixtures live in `tests/conftest.py` (IsolatedSettings, in-memory store).

## Docker / Homelab Deploy

**Fresh control-plane node**: `curl -fsSL .../scripts/install.sh | bash` — clones
the repo, provisions the OS (`scripts/bootstrap.sh --skip-network`: Docker,
Ansible, `uv`, `git-crypt`, `gh`, SSH key), prompts for Traefik/Authentik/Git
config and a DSPy opt-in, writes `.env`, brings the server up, then applies the
static IP last (`bootstrap.sh --network-only`) so the server is already running
when the SSH session drops. See `scripts/README.md`.

**Existing Docker host**:

```bash
docker compose pull
docker compose up -d
docker compose logs -f homelab-registry-mcp   # watch for "scheduler_started"

# Optional: bootstrap registry from a YAML file (no source checkout needed)
docker compose exec homelab-registry-mcp registry-mcp-seed /path/to/services.yaml
```

No source checkout needed on the target host — the image is pulled from
GHCR. Pin the release by setting `REGISTRY_MCP_VERSION=v0.6.1` in `.env`.

Pre-reqs: Traefik on external `traefik` Docker network, DNS for `registry-mcp.<your-domain>`. Docker socket is mounted read-only.

## Current Status

- **Phase 7 complete**: cross-source linking (Authentik ↔ Traefik ↔ Docker), `service_get_full_context()`, and the DSPy reasoning layer (`ResolveServiceIdentity`, `InferServiceMetadata`, `SummarizeAccessAudit`) — off by default via `DSPY_ENABLED`
- **Phase 8 in progress**: security write path landed — `GenerateRemediationPatch`, Gitea + Ntfy/Null providers, `Proposal` model/store, proposal engine (create + verification sweep), and the `proposal_*` tools. Off by default (`GIT_*` unset, `PROPOSAL_AUTO_CREATE=false`); see ADR-002.
- **Phase 8 remaining**: normalization path (`NormalizeConfigFile`, yamllint, `proposal_normalize`); flipping `PROPOSAL_DRY_RUN=false` against the homelab repo (a deliberate human step); runbooks, cold-restore testing, Ansible provisioning. (GitHub provider landed — `GitHubGitProvider` alongside Gitea, selected via `GIT_PROVIDER=github`.)
- **Phase 9a complete**: hardware node registry — `HardwareNode` model + `HardwareStore` + 11 MCP tools registered in `server.py`; manual registration only (live discovery is Phase 9b)
- **Phase C complete**: git-crypt secrets integration — 6 `secrets_*` MCP tools, `scripts/setup-homelab-repo.sh` bootstrap, `git-crypt` in Dockerfile. Path validation hardened against arbitrary file read/write via absolute paths (`_check_path` in `tools/secrets.py`); `setup-homelab-repo.sh` and `.env.example` work cross-platform (macOS/Linux/WSL), defaulting to `$HOME`-relative paths instead of `/opt/homelab`
- **Phase D complete**: migrated from Heimdall to Watchtower (Pi at `10.0.0.200`); Traefik static backend routes `registry-mcp.castaldifamily.com` → Watchtower; GitHub Actions self-hosted runner operational; first automated CD deploy proven (ConvertX on Panoptichron in 18s); `docker-compose.yml` binds `0.0.0.0:8765`
- **`docs/plans/updated-phases.md` Phases 1-3 complete** (separate numbering from the phases above): `scripts/install.sh` one-shot installer for a fresh control-plane node (Phase 1); `health.py` startup checks (Git repo/`ansible.cfg`/SSH key) + always-on `system_health_check` tool + read-only degradation of the GitOps write tools when unhealthy (Phase 2); conversational GitOps loop — `poll_pr_comments`/`apply_review_feedback` push a DSPy-generated revision commit in response to a trusted PR comment, gated by a fail-closed `PROPOSAL_COMMENT_ALLOWED_USERS` allowlist and the same confidence/YAML gates as initial patch generation (Phase 3)
- **ADR-004 proposed**: upstream version detection — `HomelabrepoDiscoverySource`, `UpstreamRegistrySource`, `ResolveLatestTag` DSPy module, `IMAGE_UPDATE` proposal type — not yet implemented
- **OOBE CLI** (ADR-003): fully documented but not yet implemented; currently a manual process
- **Deferred**: network probe discovery (`DISCOVERY_NETWORK_ENABLED=false`), real auth (Bearer/mTLS), Phase 9b live Ansible fact-gather, multi-node Ansible bootstrap (Phase E)
