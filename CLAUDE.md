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

CI runs `ruff check`, `ruff format --check`, `pytest -q`, and `ansible-lint` (against `ansible/`) on every push.

## Project Structure

```
src/registry_mcp/
├── server.py              # FastMCP wiring — register all tools here
├── config.py              # pydantic Settings (env vars → typed config)
├── gitcrypt.py            # shared git-crypt primitives (secrets tools + adoption's .env write)
├── models/
│   ├── service.py         # Service, ServiceSource (SQLModel tables)
│   ├── event.py           # ChangeEvent, DiscoveryEvent (audit log)
│   ├── hardware.py        # HardwareNode, HardwareChangeEvent, NodeRole, NodeStatus
│   ├── proposal.py        # Proposal, FindingType, ProposalStatus (Phase 8)
│   ├── adoption.py        # AdoptionDraft, DetectedSecret (Phase 7 brownfield adoption)
│   └── deletion.py        # PendingDeletion, DeletionEntityType — the math-gate challenge record
├── registry/
│   ├── store.py           # SQLite CRUD + event recording
│   └── reconcile.py       # Match discovered candidates → registry entries
├── discovery/
│   ├── base.py            # DiscoverySource protocol
│   ├── engine.py          # Orchestrates discovery passes
│   ├── scheduler.py       # APScheduler wiring
│   ├── traefik.py / docker.py / authentik.py  # source implementations
├── dspy/                  # reasoning layer (Phase 7) — DSPy enrichment, confidence-gated
│   ├── signatures.py      # ResolveServiceIdentity, InferServiceMetadata, SummarizeAccessAudit, GenerateRemediationPatch, DetectHardcodedSecrets
│   └── reasoner.py        # Reasoner: lazy LM config, gates, graceful degradation
├── hardware/              # hardware node registry (Phase 9a)
│   └── store.py           # HardwareStore: node CRUD, service linking, capacity summary
├── proposal/              # proposal layer (Phase 8) — opens PRs, never merges/writes FS
│   ├── generator.py       # calls DSPy GenerateRemediationPatch + confidence/YAML gates
│   ├── adoption.py        # AdoptionGenerator: calls DSPy DetectHardcodedSecrets + same gates
│   ├── engine.py          # create per finding, verification sweep, after_discovery hook
│   └── store.py           # Proposal CRUD (shares the registry SQLite engine)
├── normalization/         # normalization engine — see docs/specs/spec-compose-normal-form.md
│   ├── rules.py           # rule IDs, canonical key orders, equivalence-guarantee projection
│   ├── formatter.py       # deterministic ruamel.yaml round-trip (Tier 1, comment-safe)
│   ├── generator.py       # DSPy NormalizeConfigFile escalation for what the formatter skipped
│   ├── scanner.py         # repo-wide file listing + Tier 2 finding check, grouped by node
│   └── engine.py          # one PR per node; kept separate from proposal/ on purpose
├── adoption/              # brownfield adoption (Phase 7) — see docs/plans/updated-phases.md
│   ├── ssh.py             # SSH docker-inspect/cat helpers against a HardwareNode
│   └── store.py           # AdoptionDraftStore: the pause point between draft and finalize
├── deletion/
│   └── store.py           # DeletionGateStore: math-challenge request/confirm gate, shared by every hard-delete tool
├── providers/             # pluggable write-path backends (behind protocols)
│   ├── git/               # GitProvider protocol + Gitea/GitHub impls + factory
│   └── notification/      # NotificationProvider protocol + Ntfy/Smtp/Null + factory
├── integrations/
│   ├── traefik/           # httpx client + 7 MCP tools + resource + prompt
│   └── authentik/         # httpx client + 8 MCP tools + resource + prompt
├── tools/
│   ├── registry.py        # CRUD: add/get/list/update/delete (math-gated, see deletion/) service
│   ├── events.py          # query change + discovery logs
│   ├── discovery.py       # run_now / status / list_stale + connect_traefik / connect_authentik
│   ├── linking.py         # service_link_authentik + service_get_full_context
│   ├── hardware.py        # hardware-add-node/get/list/update/delete (math-gated) + link/capacity tools
│   ├── secrets.py         # secrets_status/encrypt/decrypt/add/rotate/list_keys (Phase C)
│   ├── proposal.py        # proposal_create/list_open/get/cancel/verify/normalize (Phase 8)
│   └── adoption.py        # proposal_adopt_service[_finalize/_cancel/_get] (Phase 7 brownfield)
├── webhooks/              # inbound HTTP receivers (ADR-010) — alerts → staged proposals
│   ├── schemas.py         # Pydantic Dockhand payload models + pure parsing helpers
│   └── dockhand.py        # POST /webhooks/dockhand — update/CVE alert → proposal
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

**Hardware node registry (Phase 9a-9b, `hardware/`):** curated inventory of physical and virtual
nodes, stored in the same SQLite database as services.
- `HardwareNode` — one row per node: hostname, role (`pve_host`, `docker_host`, `nas`, `pi`, etc.),
  status (`confirmed`/`unconfirmed`/`stale`/`offline`), IP/MAC, CPU, RAM, GPU, structured disk and
  storage-pool lists, Ansible inventory fields, and a `HardwareChangeEvent` audit log.
- 11 MCP tools: `hardware-add-node`, `hardware-get-node`, `hardware-list-nodes`,
  `hardware-update-node`, `hardware-delete-node`, `hardware-link-service`,
  `hardware-node-services`, `hardware-list-unconfirmed`, `hardware-list-stale`,
  `hardware-capacity-summary`, and `hardware-discover-now` (Phase 9b — live Ansible
  fact-gather).
- Two MCP resources: `hardware://all` (index) and `hardware://{node_id}` (detail).
- Services can be manually linked to nodes via `hardware-link-service`; the link is
  surfaced in `service_get_full_context()`.
- `hardware-discover-now` (Phase 9b, `hardware/ansible_facts.py`) runs
  `ansible <host|all> -m setup` against the operator's own inventory — `ANSIBLE_CONFIG`
  is pointed at `ANSIBLE_CFG_PATH` so it reads the same inventory the deploy workflow
  uses, no separate inventory setting to keep in sync — and upserts the parsed facts
  (IP/MAC, OS, CPU model/cores, RAM, disks) into `HardwareStore.upsert_from_discovery`.
  Only provenance fields are written; curated fields (`display_name`, `role`, `tags`,
  `notes`, `location`, ...) set via `hardware-add-node`/`hardware-update-node` are never
  touched — same curated-field convention as `Service`/`registry/reconcile.py`. Requires
  `ANSIBLE_CFG_PATH` and `SSH_KEY_PATH` (the same control-plane prerequisites Phase 2's
  health check gates); read-only mode disables it like the other GitOps write tools.
  Newly-discovered nodes are created `confirmed` with `manual=False`; unreachable hosts
  are reported back in the response's `failures` map rather than failing the whole pass.

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
- The engine consumes `GitProvider`/`NotificationProvider` protocols (Gitea/GitHub + Ntfy/Smtp/Null
  shipped); the discovery engine's `on_pass_complete` hook runs the verification sweep
  (and auto-create when enabled) after each pass — wrapped so it never breaks discovery.
- `NotificationProvider.send()` takes an optional `diff` — Smtp renders it into a templated
  HTML email (PR summary + truncated diff + Approve/Request Changes/View Diff buttons); Ntfy/Null
  ignore it (a full diff has no place in a mobile push).

**Normalization engine (`normalization/`, spec in `docs/specs/spec-compose-normal-form.md`):**
checks `nodes/*/*/compose.yaml` against a committed canonical form and opens one PR per node
with any safe fixes. Off by default (`NORMALIZATION_ENABLED=false`); requires the same `GIT_*`
as the proposal layer. Deliberately kept out of `proposal/` — a normalization PR can never
bundle a security remediation; they are always separate PRs with separate labels
(`NORMALIZATION_LABEL` vs `PROPOSAL_LABEL`), sharing only the `Proposal` table
(`finding_type=normalization`, `service_id=None`).
- **Hybrid rewrite, deterministic first:** `formatter.py` applies every Tier 1 (formatting)
  rule via a `ruamel.yaml` round-trip — no LLM call, fully repeatable. It's comment-safe by
  construction: `ruamel` anchors "a comment above key X" to the *previous* key's trailing-comment
  slot, not to X, so a blind key reorder can silently relocate a comment onto the wrong line.
  The formatter checks whether a mapping/list carries any attached comment before reordering
  it or converting its shape (labels/environment list→mapping); when one does, that specific
  rule is left unapplied and recorded in `skipped_rules` rather than risking misplacement.
- **DSPy `NormalizeConfigFile` is the escalation path**, not a co-equal half — it only ever
  finishes the *specific* rules the formatter skipped (or the whole file, on the rare case the
  formatter can't parse it at all), never rewrites a file DSPy hasn't seen partially normalized
  already. Same no-fallback discipline as `GenerateRemediationPatch`: `PROPOSAL_CONFIDENCE_THRESHOLD`
  gate, YAML validity, no rule-based patch if it fails.
- **The equivalence guarantee** is normalization's own gate, stricter than the security path's:
  `rules.is_equivalent(before, after)` parses both sides and projects labels/ports/environment
  to their representation-independent form (a labels list and a labels mapping compare equal)
  before comparing — a rewrite that changes anything Docker would see differently is never
  committed, regardless of which path produced it.
- Judgment-call findings (`:latest` tags, missing `restart:`, a `build:` key, an unflagged
  `ports:` mapping, a hardcoded proxy network or secret, a `container_name` mismatch) are
  **reported, never auto-fixed** — returned under `findings` and listed in the PR body.
- N-100 (renaming a misnamed `docker-compose.yml`/`.yaml`/`compose.yml` to the only filename
  `ansible/roles/docker-stack-deploy` can see, `compose.yaml`) is gated by its own
  `NORMALIZATION_RENAME_MISNAMED` flag, off by default — it makes a stack **deploy-visible for
  the first time**, a different kind of change than the rest of normalization's cosmetic fixes,
  and the PR body calls it out explicitly whenever it fires.
- **One PR per node, not per sweep and not per file** — `.github/workflows/deploy.yml` redeploys
  every stack a merged PR touches, so batching per node bounds that blast radius to one host per
  merge. `NORMALIZATION_MAX_FILES_PER_PR` caps a single node's diff size.
- `GitProvider` gained `list_files()` (one recursive git-trees call, repo-wide) and
  `delete_file()` (for N-100) — implemented on both Gitea and GitHub providers.
- Triggered by the `proposal_normalize` tool (`node`/`dry_run` params) or the
  `NORMALIZATION_SCHEDULE` scheduler job — same three-part gate as comment polling (opt-in flag,
  write path configured, not read-only).

**Brownfield adoption (`docs/plans/updated-phases.md` Phase 7, `adoption/` + `proposal/adoption.py`
+ `tools/adoption.py`):** brings a live, pre-existing Docker service (discovered but never
GitOps-managed) under management without leaking its hardcoded secrets. Off by default
(`ADOPTION_ENABLED=false`); requires the same `GIT_*` as the proposal layer plus
`SECRETS_REPO_PATH` and `SSH_KEY_PATH`.
- Two-call flow so a human always decides secret handling before anything is committed:
  `proposal_adopt_service(service_id)` SSHes into the service's linked `HardwareNode`
  (`hardware-link-service`), reads the live container's env and its original
  `docker-compose.yml` via `docker inspect`/`cat`, asks `DetectHardcodedSecrets` (DSPy) to
  produce a sanitized compose with `${VAR}` interpolations, and persists a pending
  `AdoptionDraft` — no Git write yet. `proposal_adopt_service_finalize(draft_id,
  secret_strategy)` takes the operator's `"keep"` (reuse the captured live values) or
  `"rotate"` (fresh `secrets.token_urlsafe` values — **never** generated by the reasoning
  layer) choice and opens the PR.
- **The `.env` write never goes through `GitProvider.commit_file()`** — that call is a raw
  hosting-API content write that bypasses git-crypt's local clean filter entirely, which
  would land the secret in the repo as plaintext despite `.gitattributes`. Instead
  `registry_mcp.gitcrypt` (shared with `tools/secrets.py`) checks out the feature branch in
  the local `SECRETS_REPO_PATH` clone, writes and git-crypt-encrypts the `.env` there, and
  `git push`es it; only the already-secret-free sanitized compose file goes through the
  remote Git provider, on that same branch.
- `AdoptionDraft` rows hold the captured live secret values only long enough for the
  operator to answer (`ADOPTION_DRAFT_TTL_MINUTES`, default 60) before expiring.

**Dockhand webhook (ADR-010, `webhooks/`):** an opt-in `POST /webhooks/dockhand` route
that turns Dockhand's outbound update and CVE alerts into staged proposals. Off by default
(`DOCKHAND_WEBHOOK_ENABLED=false`); requires the same `GIT_*` as the proposal layer. This
restores the update-triggered path ADR-006 removed with WUD, by push rather than by
ADR-004's unimplemented polling source.
- Registered via `FastMCP.custom_route` (the only route this server mounts alongside
  `/mcp`), and **fail-closed at registration**:
  disabled, or enabled with no `DOCKHAND_WEBHOOK_SECRET`, leaves the route unmounted
  entirely rather than mounted-and-rejecting. Dockhand does not sign its webhook bodies, so
  auth is a bearer secret compared with `hmac.compare_digest` — there is no HMAC to verify.
- **Dockhand's built-in Webhooks channel is not real Apprise, despite borrowing its scheme
  names.** Verified against a live instance: a `webhook.site` capture showed a native
  `node`-flavored sender, a payload shape (`{title, message, type, environment, timestamp}`)
  Apprise never produces, and a `+X-Dockhand-Token=<secret>` query parameter that lands as an
  inert query-string entry — the `+` is stripped but never converted into a header, so this
  endpoint can't authenticate a Dockhand channel pointed at it directly, regardless of
  URL-encoding. Reaching it requires routing through a real Apprise engine —
  `caronc/apprise-api` as a sidecar, with the header-carrying URL stored *there*
  (`json://<host>:8765/webhooks/dockhand?+X-Dockhand-Token=<secret>`, `jsons://` for TLS,
  where `+` genuinely is honored) — and pointing Dockhand at
  `apprise://<apprise-api-host>:8000/<key>`, the escape hatch Dockhand's own UI documents for
  a provider outside its built-in list. Since the payload apprise-api then forwards can still
  vary, `DOCKHAND_WEBHOOK_LOG_RAW_PAYLOAD=true` echoes a delivery body into the log when the
  shape is in doubt. Setup procedure: `docs/SOPs/SOP-002-Connect-Dockhand-Webhook.md`.
- The route only parses and dispatches. `ProposalEngine.create_for_image_update` /
  `create_for_vulnerability` feed `_open_proposal`, which already owns dedupe, target-file
  resolution, the DSPy confidence + YAML gates, branch/commit/PR, persistence, and
  notification. The long-dormant `context: str` seam on `_open_proposal` and
  `PatchGenerator.generate` carries the literal tag — it exists *because* of the removed
  ADR-005 flow and is re-activated rather than duplicated.
- **Two payload shapes, and a deliberate refusal to guess.** `DockhandStructuredAlert`
  (explicit `current_image`/`latest_image`/`server`) and `DockhandGenericAlert` (Dockhand's
  documented flat `{title, message, agent}`) are both accepted. The generic body's image
  refs are usually *digests*, which name no version a compose file can carry — such an alert
  normalizes to `AlertKind.ignored` with a stated reason rather than becoming a guessed tag
  bump. Inventing a tag would open a confidently wrong PR.
- A CVE alert above `DOCKHAND_WEBHOOK_VULNERABILITY_MIN_SEVERITY` with a known fixed image is
  an image bump; **without one, no PR is opened** — the finding is persisted as a `rejected`
  `Proposal` naming the CVEs and notified, since there is no file change to propose.
- Unactionable alerts (unknown container, container-state event, below-threshold CVE,
  digest-only payload) answer **200** with `{"skipped"/"ignored": ...}`; a non-2xx would make
  Dockhand retry a condition that never resolves. Malformed payloads get 422, bad
  content-type/body 400, failed auth 403, oversized body 413, internal fault a structured 500.

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
| `NOTIFICATION_PROVIDER` | `none` | `ntfy`, `smtp`, or `none` |
| `NOTIFICATION_URL` / `NOTIFICATION_TOPIC` / `NOTIFICATION_TOKEN` | unset / `homelab-registry` / unset | Ntfy push config |
| `NOTIFICATION_SMTP_HOST` / `_PORT` / `_USERNAME` / `_PASSWORD` / `_USE_TLS` | unset / `587` / unset / unset / `true` | SMTP relay config (Phase 5). Validated against SMTP2GO. All of host/from/to required or the factory falls back to the null provider |
| `NOTIFICATION_FROM_EMAIL` / `NOTIFICATION_TO_EMAIL` | unset / unset | Sender/recipient for the templated HTML proposal email |
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
| `NORMALIZATION_ENABLED` | `false` | Scans `nodes/*/*/compose.yaml` against `docs/specs/spec-compose-normal-form.md` and opens one PR per node with safe formatting fixes; requires `GIT_*` |
| `NORMALIZATION_SCHEDULE` | `weekly` | `daily`, `weekly`, `monthly`, or a raw seconds value |
| `NORMALIZATION_PATH_GLOB` | `nodes/*/*/compose.yaml` | Which files the canonical form applies to |
| `NORMALIZATION_MAX_FILES_PER_PR` | `25` | Caps one node's PR diff size on a first run against a messy repo |
| `NORMALIZATION_DRY_RUN` | `false` | Generate diffs and log them without opening PRs |
| `NORMALIZATION_RENAME_MISNAMED` | `false` | Renames `docker-compose.yml`/`.yaml`/`compose.yml` → `compose.yaml` (N-100) — makes a previously deploy-invisible stack visible, so it's opt-in separately from the rest of normalization |
| `NORMALIZATION_LABEL` | `normalization` | PR label — always distinct from `PROPOSAL_LABEL` so a normalization PR is never mistaken for a security one |
| `SECRETS_ENABLED` | `true` | Enables `secrets_*` MCP tools (Phase C git-crypt integration) |
| `SECRETS_REPO_PATH` | unset | Absolute path to the cloned private homelab repo on this node. `pydantic-settings` reads `.env` as literal strings — `$HOME`/`~` are not expanded, so use a concrete absolute path (e.g. `/opt/homelab` on the Pi, `/Users/you/homelab` on macOS) |
| `SECRETS_KEY_PATH` | unset | Absolute path to the exported git-crypt key file (priority over env var); same no-expansion caveat as `SECRETS_REPO_PATH` |
| `SECRETS_GIT_CRYPT_KEY` | unset | Base64-encoded git-crypt key bytes (fallback when no key file) |
| `ANSIBLE_CFG_PATH` | unset | Absolute path to `ansible.cfg` on this node; one of three startup health checks (Phase 2) — missing it starts the server in read-only mode |
| `SSH_KEY_PATH` | unset | Absolute path to the control-plane SSH key; same startup health check as `ANSIBLE_CFG_PATH`, same no-expansion caveat |
| `ADOPTION_ENABLED` | `false` | Enables the `proposal_adopt_service*` brownfield adoption tools |
| `SSH_DEFAULT_USER` | `root` | User for the ad-hoc SSH connection adoption uses to inspect a live container; reuses `SSH_KEY_PATH` |
| `ADOPTION_DRAFT_TTL_MINUTES` | `60` | How long a drafted adoption may await the operator's keep/rotate decision before expiring |
| `DELETE_CHALLENGE_TTL_MINUTES` | `5` | How long a `registry_delete_service`/`hardware-delete-node` math challenge stays answerable via its `*_confirm` tool before expiring |
| `DOCKHAND_WEBHOOK_ENABLED` | `false` | Registers `POST /webhooks/dockhand` (ADR-010). `true` with no `DOCKHAND_WEBHOOK_SECRET` leaves the route unregistered, never open |
| `DOCKHAND_WEBHOOK_PATH` | `/webhooks/dockhand` | |
| `DOCKHAND_WEBHOOK_SECRET` | unset | Shared secret Dockhand presents as `Authorization: Bearer <secret>` or `X-Dockhand-Token`; Dockhand does not sign bodies, so there is no HMAC to verify |
| `DOCKHAND_WEBHOOK_MAX_BODY_BYTES` | `65536` | Cap on an accepted request body |
| `DOCKHAND_WEBHOOK_VULNERABILITY_ENABLED` | `true` | Whether CVE alerts also earn a proposal |
| `DOCKHAND_WEBHOOK_LOG_RAW_PAYLOAD` | `false` | Logs each authorized delivery body verbatim for diagnosing an unknown payload shape; bypasses field-name redaction, so turn it back off |
| `DOCKHAND_WEBHOOK_VULNERABILITY_MIN_SEVERITY` | `high` | `low`/`medium`/`high`/`critical`; an unrecognized label surfaces rather than being dropped |
| `EVENT_RETENTION_DAYS` | `90` | Old events purged on startup |
| `LOG_LEVEL` | `INFO` | |

Copy `.env.example` to `.env` and fill in the upstream URLs before running locally.

## Key Conventions

- **Curated fields are sacred**: `display_name`, `category`, `tags`, `notes` set by humans are never overwritten by discovery. Discovery only updates provenance fields (`host`, `urls`, `traefik_router`, `authentik_app_slug`, `auth_mode`).
- **Never hard-delete discovered services**: mark `stale=True` after threshold misses.
- **Every hard delete is math-gated**: `registry_delete_service` and `hardware-delete-node` only request deletion — they return an `x + y = ?` challenge (`deletion/store.py`'s `DeletionGateStore`) that must be solved and passed to `registry_delete_service_confirm`/`hardware-delete-node-confirm` within `DELETE_CHALLENGE_TTL_MINUTES` before the row is actually removed. Not a security boundary (single digits, shown in the challenge itself) — a deliberate human-in-the-loop friction point against an agent or a fat-fingered id deleting something irreversible; a wrong answer invalidates the challenge rather than allowing retries.
- **Upstream APIs are read-only**: Traefik, Authentik, and Docker are never modified.
- **The write path writes to Git only**: the proposal layer opens PRs; it never merges them and never writes the filesystem Traefik/Docker watch. The PR + human merge is the safety gate. All write behavior defaults off.
- **All patch generation goes through DSPy**: `proposal/generator.py` has no rule-based fallback. Low-confidence or invalid-YAML patches become `rejected` Proposals, never commits.
- **A normalization rewrite must prove behavior equivalence before it's committed**: `normalization/rules.is_equivalent()` projects both the before and after YAML to a representation-independent form and compares them; a rewrite that changes anything Docker would see differently is never committed, regardless of whether the deterministic formatter or the DSPy escalation produced it. Security patches (`proposal/generator.py`) intentionally change behavior and have no equivalent gate.
- **Normalization and security proposals are never bundled**: `normalization/` is its own engine, never merged into `proposal/`, and opens PRs under a separate label (`NORMALIZATION_LABEL`).
- **New tools must be registered in `server.py`** — FastMCP doesn't auto-discover them.
- **An inbound webhook never mutates, and never guesses**: `webhooks/` receivers parse, validate, and hand off to the proposal engine — they never write the registry or touch a container. An alert that doesn't carry enough to build a correct change (a digest where a tag is needed) is acknowledged with a reason, never turned into a speculative PR. Unactionable alerts answer 200 so the sender doesn't retry forever; only malformed input or failed auth earns a non-2xx.
- **No LLM calls in the detection layer**: `reconcile.py` and discovery sources stay deterministic. Reasoning (DSPy) lives in `dspy/` and is wired in via injected callables; those layers never `import dspy`.
- **DSPy/`dspy/` subpackage does not shadow the library**: Python 3 absolute imports resolve `import dspy` to the top-level package; the library is imported lazily so a disabled reasoning layer adds no startup cost.
- **Naming**: kebab-case for MCP tool names, snake_case for Python, PascalCase for classes.
- **Log secrets are redacted**: any field named `token`, `password`, `secret`, `key`, `authorization`, `api_key` is replaced with `***redacted***` before writing to logs.
- **All repo-relative paths go through `gitcrypt.check_path`**: every user- or draft-supplied path (`secrets_*` tools, adoption's `.env` write) is validated by the shared helper in `gitcrypt.py` — reject absolute paths, reject `..` traversal, then `.resolve()` + `is_relative_to(repo)` as a final containment check (also catches symlink escapes). Never join a repo base with a caller-supplied path without it; `Path(base) / "/etc/passwd"` silently discards `base` and returns `/etc/passwd`.
- **A secret never reaches Git through `GitProvider.commit_file()`**: that call is a raw hosting-API content write and bypasses git-crypt's local clean filter entirely. Anything that must land encrypted (the `.env` files `secrets_*` and adoption write) goes through `gitcrypt.py`'s local-clone subprocess helpers instead — see the brownfield adoption entry above.
- **Structured logs go to stderr + file** — keeps stdio JSON-RPC transport clean.
- **No HTTP /health endpoint on `/mcp` itself**: Dockerfile still uses a TCP probe on `MCP_PORT` for container health. `FastMCP.custom_route` (available since the pinned `mcp` SDK, 1.29.0) does let the server expose arbitrary Starlette routes alongside `/mcp` — the Dockhand webhook (`webhooks/dockhand.py`, ADR-010) is the only thing that uses it — but no `/health` HTTP route has been added, and this line describes that gap, not a technical limitation.
- **ForwardAuth in front of MCP clients breaks them** (clients don't follow redirects). This applies to `/mcp` itself — auth strategy there is deferred; the endpoint is LAN-only. It applies equally to `/webhooks/dockhand`, which authenticates in-process with a bearer secret rather than sitting behind a redirect-based proxy. There is no browser-facing route on this port.

## Testing

Tests use `pytest-asyncio` (`asyncio_mode="auto"`) and an in-memory SQLite fixture to avoid touching `.env` or real APIs.

```bash
uv run pytest                            # all tests
uv run pytest -v tests/test_linking.py   # one file
uv run pytest --cov=src                  # with coverage
```

Fixtures live in `tests/conftest.py` (IsolatedSettings, in-memory store).

### Installer validation (two-tier)

`scripts/install.sh` / `scripts/bootstrap.sh` have two separate test loops, each catching a different class of bug. Run the fast one first; reach for the slow one only when a change needs fidelity the fast one structurally can't provide.

- **Fast loop — `.github/workflows/install-validation.yml`** (GitHub Actions, `ubuntu-latest`). Runs `install.sh` non-interactively — every prompt pre-seeded via env vars of the same name, `INSTALL_SKIP_NETWORK=true` skips the static-IP swap (which would otherwise risk severing the runner's own network connectivity mid-job) — and asserts the `homelab-registry-mcp` container comes up healthy. Triggers on `workflow_dispatch` (`gh workflow run install-validation.yml --ref <branch>` — test a change without opening a PR) and on `pull_request` touching `scripts/**`. Catches logic bugs, env-var plumbing issues, and container-health regressions in minutes, without a merge to `main`.
- **Slow loop — `vagrant/slow-loop/` (Vagrant + libvirt, Debian trixie64)**. `cd vagrant/slow-loop && vagrant up && vagrant ssh`, then run the installer by hand inside — see `vagrant/slow-loop/README.md` for the full walkthrough. Real systemd and real network-interface ownership catch what the fast loop structurally can't — e.g. the ifupdown-vs-netplan detection bug: `ubuntu-latest` ships netplan, not ifupdown, so only a real Debian VM reproduces that class of failure — and it's the only place the static-IP step (`bootstrap.sh` Phase 6) actually runs at all, since the fast loop always skips it. `vagrant destroy -f` between rounds; both scripts assume a genuinely fresh node.

Both loops clone from GitHub rather than a local working tree, so push your branch before testing either one. `install.sh` honors `VERSION` (the same variable the documented `curl -fsSL .../${VERSION}/scripts/install.sh` one-liner already uses) for its own internal clone too — `export VERSION=your-branch-name` first (must be exported, not just assigned, or the `bash -c` subprocess running `install.sh` never sees it) and both loops test that branch end-to-end (`bootstrap.sh` and `scripts/` included), not just main with a different `install.sh` grafted on top.

`vagrant/` also holds `vagrant/workload-node/`, an unrelated fixture — a live Traefik + demo-services VM for testing discovery/linking code against something real, not part of this installer-validation strategy. See `vagrant/README.md` for the full fixture index.

Both loops are required for full confidence; neither replaces the other.

## Docker / Homelab Deploy

**Fresh control-plane node**: `curl -fsSL .../scripts/install.sh | bash` — clones
the repo, provisions the OS (`scripts/bootstrap.sh --skip-network`: Docker,
Ansible, `uv`, `git-crypt`, `gh`, SSH key), prompts for Git config and a DSPy
opt-in, then optionally creates the private homelab config repo itself
(folded in from `scripts/setup-homelab-repo.sh`; offers to run the one-time
`gh auth login` device-code flow right there if needed, reuses the
`owner/name` from the Git config prompt when it was answered `github`, skips
cleanly with guidance if `gh`/`git-crypt` aren't available or the login isn't
completed) — and if that repo now exists,
optionally seeds this node into the Ansible inventory `hardware-discover-now`
reads (folded in from `scripts/setup-ansible-inventory.sh`; skips cleanly with
guidance if no repo exists at all). Writes `.env`, brings the server up, then
applies the static IP last (`bootstrap.sh --network-only`) so the server is
already running when the SSH session drops. See `scripts/README.md`.

Assumes a **greenfield** setup — no Traefik or Authentik yet, so `install.sh`
doesn't ask about them. Once those exist, connect them via the
`discovery_connect_traefik` / `discovery_connect_authentik` MCP tools
(`tools/discovery.py`): each live-tests the URL/credentials and hands back the
`.env` lines to add plus a restart — they never write a file themselves (the
container has no filesystem access to the host's `.env`) and never start
discovery immediately (`Settings` and the scheduler are only read/built at
server startup).

**Pi non-MCP services — Komodo + Traefik (ADR-006, now GitOps-managed per ADR-007)**:
`docker-compose.yml` in this repo defines only `homelab-registry-mcp` — Komodo
(container management, logs, update detection) and Traefik (this node's
central ingress; other nodes' `traefik-kop` instances publish routes to its
Redis) are no longer bundled into this repo's compose file or `install.sh`.
They're deployed instead as ordinary `nodes/<node>/<service>/compose.yaml`
entries in the operator's private homelab repo, through the same Ansible +
GitHub Actions GitOps pipeline (Phase 4/ADR-001) every other node service
uses — see ADR-007. ADR-006's standing choice of *which* tools (Komodo +
Traefik, supersedes the ADR-005 monitoring/ingress stack — Beszel, Gatus,
Dozzle, WUD, Homepage, Glance, `docker-socket-proxy`, Autorestic,
Healthchecks.io — none of which remains, including the `/webhooks/wud` →
`image_update` proposal flow WUD used to drive) is unchanged; only the deploy
mechanism moved.

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

### Automated Deployment Pipeline (Phase 4 — GitOps CD)

The action lives here; the config lives in each operator's private homelab
repo. `homelab-registry-mcp` ships `ansible/roles/docker-stack-deploy` (git
pull + `docker compose pull && up -d` for one `nodes/<node>/<service>/`
directory) and a reusable `.github/workflows/deploy.yml` (`on: workflow_call`).
An operator's private repo never carries the deploy logic — only its own
inventory, `ansible.cfg`, `nodes/` compose files, and a thin caller workflow:

```yaml
# <your-homelab-repo>/.github/workflows/deploy.yml
name: Deploy
on:
  push:
    branches: [main]
jobs:
  deploy:
    uses: TeamCastaldi/homelab-registry-mcp/.github/workflows/deploy.yml@main
```

The reusable workflow diffs the push for changed `nodes/**/compose.yaml`
paths, checks out `homelab-registry-mcp` alongside the caller's checkout to
get the role, and runs `ansible-playbook` against the *caller's* inventory —
using the self-hosted runner already registered to the caller's repo (ADR-001
§5.1 step 11). See `ansible/README.md` and
`ansible/roles/docker-stack-deploy/README.md` for the full variable contract.

## Current Status

- **Delete confirmation gate complete**: every hard-delete tool (`registry_delete_service`,
  `hardware-delete-node`) now only *requests* deletion — it returns a single-digit
  `x + y = ?` arithmetic challenge (`deletion/store.py`'s `DeletionGateStore`, backed by the
  new `PendingDeletion` table) that a new `registry_delete_service_confirm`/
  `hardware-delete-node-confirm` tool must be called with the correct answer within
  `DELETE_CHALLENGE_TTL_MINUTES` (default 5) before the row is actually removed. Wrong
  answer, expired challenge, or an already-resolved one all invalidate it outright — no
  retries, just call the delete tool again for a fresh problem. Expired challenges are also
  swept on every server startup, same idiom as `AdoptionDraftStore.purge_expired`.
- **ADR-010 complete**: Dockhand webhook (`webhooks/`) — `POST /webhooks/dockhand` turns container-update and CVE alerts into staged `image_update`/`vulnerability_scan` proposals through the existing engine. Restores the update-triggered path ADR-006 removed with WUD and closes its Open Item. Off by default (`DOCKHAND_WEBHOOK_ENABLED=false`), fail-closed at registration when no secret is set. Accepts both Dockhand payload shapes; the stock generic body is digest-only and is deliberately ignored rather than guessed at — see ADR-010's Negative consequences.
- **ADR-011 accepted**: withdraws the Komodo integration and the `/chat` interface from the
  server's supported surface. Supersedes ADR-009 in full and amends ADR-006 §1 (the
  integration only — Komodo still runs on the Pi). See the two bullets below.
- **Komodo integration removed**: `integrations/komodo/` and its 7 read-only tools
  (`komodo_health`, `komodo_list_stacks`, `komodo_get_stack`, `komodo_list_services`,
  `komodo_get_service`, `komodo_list_updates`, `komodo_get_logs`), the `komodo://stacks/{name}`
  resource, the `diagnose_stack` prompt, and the five `KOMODO_*` settings are gone. Komodo was
  never a discovery source — no `SourceType` member, no reconciler path, no registry rows — so
  nothing in the database references it. **ADR-006 §1's decision to run Komodo on the Pi for
  operational visibility still stands**; per ADR-007 that deployment lives in the operator's
  private homelab repo as a `nodes/<node>/<service>/compose.yaml` entry, and only this server's
  integration with it was withdrawn.
- **ADR-009 removed**: the web chat interface (`chat/`) and its Ollama backend are gone —
  `/chat`, `/chat/auth/*`, `/chat/api/*`, the `CHAT_*` settings, the `READ_TOOLS`/
  `WRITE_TOOLS`/`DENY_ALWAYS` bridge, and the JS markdown renderer with it. `/mcp` is
  unaffected; `webhooks/dockhand.py` is now the sole `FastMCP.custom_route` consumer, so
  `starlette` remains a required dependency. ADR-002 §4.4's Open Questions 1-4, which
  ADR-009 had resolved, are open again.
- **Phase 7 complete**: cross-source linking (Authentik ↔ Traefik ↔ Docker), `service_get_full_context()`, and the DSPy reasoning layer (`ResolveServiceIdentity`, `InferServiceMetadata`, `SummarizeAccessAudit`) — off by default via `DSPY_ENABLED`
- **Phase 8 in progress**: security write path landed — `GenerateRemediationPatch`, Gitea + Ntfy/Smtp/Null providers, `Proposal` model/store, proposal engine (create + verification sweep), and the `proposal_*` tools. Off by default (`GIT_*` unset, `PROPOSAL_AUTO_CREATE=false`); see ADR-002. Normalization path complete — `docs/specs/spec-compose-normal-form.md`, the `normalization/` engine (`ruamel.yaml` deterministic formatter + `NormalizeConfigFile` DSPy escalation + `yamllint`), and the `proposal_normalize` tool + `NORMALIZATION_SCHEDULE` scheduler job. Off by default (`NORMALIZATION_ENABLED=false`).
- **Phase 8 remaining**: flipping `PROPOSAL_DRY_RUN=false` (and `NORMALIZATION_DRY_RUN=false`) against the homelab repo (a deliberate human step); runbooks, cold-restore testing, Ansible provisioning. (GitHub provider landed — `GitHubGitProvider` alongside Gitea, selected via `GIT_PROVIDER=github`.)
- **Phase 9a-9b complete**: hardware node registry — `HardwareNode` model + `HardwareStore` + 11 MCP tools registered in `server.py`; `hardware-discover-now` runs a live Ansible `setup` fact-gather against `ANSIBLE_CFG_PATH`'s inventory and upserts provenance fields (curated fields untouched). `scripts/setup-ansible-inventory.sh` bootstraps the `ansible.cfg`/inventory prerequisite itself (seeds the control-plane node, then prompts for more hosts) until the OOBE CLI replaces it — also folded inline into `scripts/install.sh` (opt-in, only offered when a homelab config repo already exists) so hardware onboarding can start from a fresh install rather than a separate manual step; the standalone script remains the way to add more hosts later.
- **Phase C complete**: git-crypt secrets integration — 6 `secrets_*` MCP tools, `scripts/setup-homelab-repo.sh` bootstrap, `git-crypt` in Dockerfile. Path validation hardened against arbitrary file read/write via absolute paths (`check_path` in `gitcrypt.py`, shared with Phase 7 adoption); `setup-homelab-repo.sh` and `.env.example` work cross-platform (macOS/Linux/WSL), defaulting to `$HOME`-relative paths instead of `/opt/homelab` — also folded inline into `scripts/install.sh` (Pi defaults there) so a fresh install can create the repo without a separate manual step; `setup-homelab-repo.sh` remains the standalone/cross-platform path
- **Phase D complete, routing model since moved to Docker labels**: migrated registry-mcp off the workload node onto the dedicated control-plane node; GitHub Actions self-hosted runner operational; first automated CD deploy proven end-to-end; `docker-compose.yml` binds `0.0.0.0:8765`. Originally routed via a Traefik static backend (`docs/plans/phase-d.md`, written when Traefik lived on a separate workload node); now that Traefik runs co-located on this same node (ADR-006/ADR-007), `docker-compose.yml` carries standard Traefik Docker labels and joins the external `traefik` network instead — see the Docker/Homelab Deploy section above.
- **Installer modularized into per-phase scripts**: `install.sh`'s 9 steps and `bootstrap.sh`'s 6 phases each moved into their own self-contained file under `scripts/phases/install/` and `scripts/phases/bootstrap/` respectively, sharing prompt/`.env`-write/detection helpers via `scripts/lib/common.sh`. `install.sh`/`bootstrap.sh` are now thin orchestrators that just call each phase script in order; every phase is independently runnable (`bash scripts/phases/install/06-write-env.sh`, `bash scripts/phases/bootstrap/06-static-ip.sh`, ...) for debugging or a targeted brownfield/greenfield rerun without re-driving the whole installer. Cross-phase handoff goes through a small state file (a real env var of the same name still always wins, so every documented pre-seeding trick is unchanged) — see [scripts/README.md](scripts/README.md#modular-phase-scripts). External behavior (prompts, `.env` output, CI env-var pre-seeding) is unchanged; `.github/workflows/install-validation.yml` exercises it end-to-end same as before.
- **`docs/plans/updated-phases.md` Phases 1-6 complete** (separate numbering from the phases above): `scripts/install.sh` one-shot installer for a fresh control-plane node (Phase 1); `health.py` startup checks (Git repo/`ansible.cfg`/SSH key) + always-on `system_health_check` tool + read-only degradation of the GitOps write tools when unhealthy (Phase 2); conversational GitOps loop — `poll_pr_comments`/`apply_review_feedback` push a DSPy-generated revision commit in response to a trusted PR comment, gated by a fail-closed `PROPOSAL_COMMENT_ALLOWED_USERS` allowlist and the same confidence/YAML gates as initial patch generation (Phase 3); `ansible/roles/docker-stack-deploy` + reusable `.github/workflows/deploy.yml` — the deploy *action* ships here, each operator's private homelab repo supplies only the *config* and a thin caller workflow (Phase 4); `SmtpNotificationProvider` — templated HTML proposal email (PR summary, diff, Approve/Request Changes/View Diff buttons) via stdlib `smtplib`, validated against SMTP2GO, `NOTIFICATION_PROVIDER=smtp` (Phase 5); public release scrub — removed an accidentally-committed operator-specific `nodes/` config and genericized real hostnames/IPs/personal identifiers across scripts and docs (Phase 6)
- **`docs/plans/updated-phases.md` Phase 7 complete** (brownfield adoption & secret interception — the final phase in that plan): `proposal_adopt_service`/`_finalize`/`_cancel`/`_get` tools, `AdoptionDraft` model/store, `DetectHardcodedSecrets` DSPy signature, and the shared `gitcrypt.py` module (extracted from `tools/secrets.py` so both features encrypt through the same local-clone path rather than the remote Git API, which bypasses git-crypt's filter). Off by default (`ADOPTION_ENABLED=false`).
- **ADR-005 superseded by ADR-006**: the monitoring/ingress stack ADR-005 introduced (Beszel, Gatus, Dozzle, WUD, docker-socket-proxy, Homepage, Glance, Autorestic, Healthchecks.io heartbeat) has been fully removed — **except `traefik-kop` + Redis, which ADR-006 §2 explicitly kept**: cross-node routing still works exactly as ADR-005 §4 described, just re-centered on the Pi. The removal included the WUD-driven `/webhooks/wud` → `image_update` proposal flow (`FindingType.image_update`, `ProposalEngine.create_for_image_update`) — that update-triggered proposal path is **restored by ADR-010** (`webhooks/dockhand.py`), sourced from Dockhand push alerts instead of WUD polling; `FindingType.image_update` is back alongside a new `vulnerability_scan`.
- **ADR-006 complete, deploy mechanism superseded by ADR-007**: the Pi's only non-MCP services are Komodo (container management, logs, update detection) and Traefik (this node's central ingress, moved from a workload node; other nodes' `traefik-kop` instances publish to its Redis). Komodo is operational visibility only — the Ansible + GitHub Actions GitOps pipeline (Phase 4/ADR-001) remains the only path for MCP-proposed changes, on the Pi or any other node. Per ADR-007, both are now deployed as ordinary `nodes/<node>/<service>/compose.yaml` entries in the operator's private homelab repo through that same GitOps pipeline, rather than bundled into this repo's `docker-compose.yml`/`scripts/install.sh` as Compose profiles.
- **ARD-004 proposed, partly advanced by ADR-010**: upstream version detection — `HomelabrepoDiscoverySource`, `UpstreamRegistrySource`, `ResolveLatestTag` DSPy module — none of the polling sources are implemented. ADR-010 supplies the `image_update` `FindingType` ADR-004 asked for and covers the detection gap by push instead: Dockhand sends the exact target tag, so `ResolveLatestTag` isn't needed on that path. ADR-004's three-way drift model (intended/actual/available) still wants a repo-reading source
- **OOBE CLI** (ARD-003): fully documented but not yet implemented; currently a manual process
- **Deferred**: network probe discovery (no source, no `SourceType` member, and no setting — it was never implemented, so the placeholders were removed rather than left looking wired), real auth (Bearer/mTLS), multi-node Ansible bootstrap (Phase E)
