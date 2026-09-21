# Conversational Service Deployment — Build Plan

**Status:** Proposed — not started.
**Written:** 2026-09-21.
**Origin:** Grew out of evaluating whether artifacts in the separate `ncastaldi/ansible`
repo could feed `homelab-registry-mcp`; that evaluation surfaced open questions
(below) that block part of this plan, and the goal itself expanded into this
document. No ADR exists yet — specific decisions below should each become their
own ADR at implementation time, the same way every other phase in this codebase
has (ADR-010, ADR-013, ADR-015, ADR-016, ...).

## Goal

Nathan says "deploy `<service>`" with a link to a source repo. The system should:

1. Gather homelab facts (existing services, capacity, naming/subdomain collisions).
2. Read the target repo and work out what it needs to run.
3. Draft a `compose.yaml` that conforms to this homelab's conventions.
4. Pick a node and scaffold `nodes/<node>/<service>/`.
5. Produce a copy/paste secrets block (generated values + what Nathan must supply),
   with the Infisical path each one belongs under. Not a file write — Phase 1 scope,
   confirmed with Nathan. Direct Infisical write is deferred (Phase 8).
6. Open a PR with the new stack, same PR+merge gate every other write path here uses.
7. Deploy. Currently a manual Dockhand click; Nathan wants this automated. Blocked
   on Phase 0 below — see "The deploy-mechanism fork."

## Design decision: how this gets triggered

**Built as MCP tools (+ one MCP prompt) in this repo, not as a Claude Code skill.**

Every comparable multi-step flow already in this codebase — proposals, normalization,
adoption — is implemented as MCP tools, discoverable and callable from any MCP client,
never as a Claude-Code-side skill. This matters concretely here: per the homelab repo's
own roadmap notes, `registry-mcp.castaldifamily.com` is the literal endpoint claude.ai's
remote connector talks to. A Claude Code skill is invisible to that connector, to a
future Dockhand-webhook-triggered variant, or to any other MCP client. Building the
capability as tools keeps it reachable everywhere, including from Claude Code.

There's already a precedent for the discoverable entry point: the `diagnose_stack` MCP
prompt (Traefik/Dockhand integrations). This plan adds a matching `deploy_service`
prompt that seeds the tool-calling sequence.

A thin Claude Code skill in the homelab repo's `.claude/skills/` (matching the
`troubleshooting`/`audit` pattern already there) can still be added later purely as UX —
natural-language trigger, a gated confirm-before-PR step — but its job would be to call
these MCP tools, never to reimplement the logic client-side. Not part of this plan's
phases; add it any time after Phase 5 without blocking anything.

## Requirements → building blocks

| Capability | Status | Reuses / precedent |
|---|---|---|
| Homelab fact-gathering | Exists | `hardware-capacity-summary`, `hardware-list-nodes`, `registry_list_services`, `traefik_list_routers`, `authentik_list_applications` |
| Homelab conventions as generation input | Exists, needs wiring | `docs/spec/compose.yaml`, `references/homelab.md` (homelab repo), `normalization/rules.py` canonical shape rules |
| Complete-file generation, confidence + YAML gated, no rule-based fallback | Pattern exists | `dspy/signatures.py` (`GenerateRemediationPatch`, `NormalizeConfigFile`) |
| Branch → commit → PR | Exists as-is | `providers/git/`, `proposal/engine.py`'s `_open_proposal` |
| Dry-run before any real write | Pattern exists | `PROPOSAL_DRY_RUN`, `NORMALIZATION_DRY_RUN` |
| Generated-not-guessed secret values | Pattern exists | `adoption/` "rotate" path — `secrets.token_urlsafe`, never DSPy-generated |
| Two-call human-decision gate before anything commits | Pattern exists | `proposal_adopt_service` → `proposal_adopt_service_finalize` |
| Repo ingestion (Dockerfile/compose/README → structured requirements) | **New** | closest analog: `adoption/ssh.py`'s live-container inspection, but reads a foreign repo, not a live container |
| Compose generation for a **new** stack (vs. patching a known file) | **New** | new DSPy signature |
| Secrets-block formatter | **New** | tool output only, no file write in this phase |
| Direct Infisical write | Explicitly deferred | `INFISICAL_ALLOW_WRITE` reserved in schema, ADR-016 |
| Deploy trigger | **Open — see below** | two real candidates, not obviously interchangeable |

## Open questions that block parts of this plan (resolve first)

**The deploy-mechanism fork.** This stack already has an automated deploy path —
`ansible/roles/docker-stack-deploy` + the reusable `deploy.yml` workflow, run by a
self-hosted GitHub Actions runner on every push to `main` touching
`nodes/**/compose.yaml`, called "proven end-to-end" in this repo's own status notes.
Nathan deploys manually via Dockhand today. Those two facts need reconciling before
Phase 7 can be designed:

- If the CD pipeline already covers the target node(s) and Dockhand is just habit —
  nothing new to build; confirm it and stop clicking.
- If Dockhand really is load-bearing for some nodes/services — automating it means
  a new, narrow write tool (e.g. `dockhand-redeploy-stack`), which is a deliberate
  reversal of ADR-013's "no Dockhand write endpoint is ever exposed as a tool, even
  though the API has one." Not a default to build toward; a decision to make
  consciously when this phase is reached.

**Hardware/node identity drift**, surfaced while evaluating the `ncastaldi/ansible` repo,
unresolved as of this writing:

- `homelab-control-plane` = `watchtower`, confirmed (same Pi, documented rename).
- `p410` (ansible repo, active inventory, 10.0.0.201, Ansible-managed AI/dev-swarm
  node) vs. `panoptichron` (homelab troubleshooting reference, same IP, explicitly
  "Hands-off... Not Ansible-onboarded") — a direct contradiction, not yet resolved.
  Whether this is the same box renamed/repurposed or a stale abandoned experiment is
  unknown from the repos alone.
- `dev5432` (ansible repo, 10.0.0.203) appears to be `p1ollama`/"ollama" in the
  homelab repo — the node `homelab-registry-mcp` itself now runs on — but nothing
  in the ansible repo reflects that role change.

This matters here because Phase 3 (placement) trusts `hardware-list-nodes` /
`hardware-capacity-summary` output to pick a deploy target. If the underlying
`HardwareNode` rows carry stale or contested identity, placement can propose a node
that's wrong, or — worse — the hands-off `panoptichron` node under a different name.
Resolve before Phase 3 ships, not necessarily before Phases 1-2 (which don't touch
node identity).

**Convention freshness.** `docs/spec/compose.yaml` and `references/homelab.md` (fed
into Phase 2's generator as ground truth) should be spot-checked as current — the
troubleshooting reference is explicitly dated ("last confirmed 2026-08-17") and
self-describes as "a map, not gospel."

## Phased plan

Each phase is independently useful and testable before the next depends on it.
Stopping after Phase 5 already delivers "AI drafts the compose + tells me what
secrets to add" as a working tool.

### Phase 0 — Recon (no code)
Resolve the deploy-mechanism fork and the node-identity questions above. Confirm
the convention docs are current enough to trust as generation input. Everything
past this phase assumes these are settled.

### Phase 1 — Repo ingestion
New package `intake/` (shape mirrors `adoption/`, `normalization/`):
- `intake/fetch.py` — shallow-fetch a repo, locate Dockerfile / `docker-compose.yml`
  / `compose.yaml` / README.
- `intake/parse.py` — deterministic parsing only: exposed ports, env vars, volumes,
  base image. No LLM here, same discipline as `reconcile.py` staying detection-only.
- New DSPy signature `InferServiceRequirements` (`dspy/signatures.py`) — only for
  what deterministic parsing can't get (e.g. "does this need a database" from README
  prose). Confidence-gated; discard and leave unfilled below threshold, never guess.
- New MCP tool `service-intake-repo(repo_url)` → structured requirements JSON.
  Read-only, no confirm gate needed — it writes nothing.

### Phase 2 — Compose generation
- New DSPy signature `GenerateServiceCompose(intake, homelab_conventions,
  target_node) -> compose_yaml`.
- New package `service_deploy/` (mirrors `proposal/`'s shape):
  `service_deploy/generator.py` calls the signature, gates on
  `SERVICE_DEPLOY_CONFIDENCE_THRESHOLD` + YAML validity — same no-fallback
  discipline as `GenerateRemediationPatch`. Low-confidence or invalid output is
  never turned into a draft.
- Run the result through `normalization/formatter.py`'s canonical-shape rules
  before it's ever shown to Nathan (this is a fresh file, so `rules.is_equivalent`
  doesn't apply — there's no "before" — but the same key-order/shape rules do).
- Needs read access to `docs/spec/compose.yaml` / `references/homelab.md` in the
  homelab repo — reuse whatever `GitProvider` already exposes for reading a file.

### Phase 3 — Placement
No new tool. Orchestration logic queries `hardware-capacity-summary` /
`hardware-list-nodes` to propose a node; auto-proceed on a clear single fit, ask
Nathan when ambiguous. Folder scaffolding (`nodes/<node>/<service>/`) needs no
confirm gate — already "free rein" under homelab's own autonomy rules. **Depends
on Phase 0's identity questions being resolved.**

### Phase 4 — Secrets block
New module `service_deploy/secrets_block.py`. Classifies Phase 1's env vars into
generated (safe random values via `secrets.token_urlsafe`, same pattern as
adoption's "rotate" — never DSPy-generated) vs. operator-supplied (third-party API
keys etc.). Output: var name → value/placeholder → target Infisical path. Tool
output only — no file write, no Infisical call. This is the copy/paste phase
Nathan confirmed as the starting point.

### Phase 5 — PR assembly
- New model `models/service_deploy.py` — a `ServiceDeployDraft`, shaped like
  `AdoptionDraft`: holds the generated compose + secrets block long enough for
  Nathan to review, expires on a TTL the same way adoption drafts do.
- New `service_deploy/engine.py` (mirrors `proposal/engine.py`'s `_open_proposal`):
  branch → commit `nodes/<node>/<service>/compose.yaml` → open PR under its own
  `SERVICE_DEPLOY_LABEL` (kept distinct from `PROPOSAL_LABEL`/`NORMALIZATION_LABEL`,
  same separation-of-concerns reasoning already applied to normalization) → notify.
- Two new MCP tools, mirroring adoption's two-call gate:
  - `service-deploy-create(repo_url, service_name?, node?)` — runs Phases 1-4,
    returns the draft + secrets block, commits nothing yet.
  - `service-deploy-finalize(draft_id)` — opens the PR.
- New MCP prompt `deploy_service` — the discoverable, client-agnostic entry point
  that seeds a `service-deploy-create` call.

### Phase 6 — Dry run + feature gate
- `SERVICE_DEPLOY_ENABLED` (off by default), matching `NORMALIZATION_ENABLED` /
  `ADOPTION_ENABLED`.
- `SERVICE_DEPLOY_DRY_RUN` (recommend defaulting true on first rollout, matching
  `PROPOSAL_DRY_RUN`'s stance) — `service-deploy-finalize` logs the would-be PR
  instead of opening it. Use this to validate output quality against a handful of
  real repos before trusting it live.

### Phase 7 — Deploy automation
Design depends entirely on Phase 0's fork resolution:
- **Path A** (CD pipeline already covers it): no new tool. Harden/verify the
  existing `docker-stack-deploy` role covers the chosen node; have
  `service-deploy-finalize`'s PR body state which mechanism deploys it, mirroring
  how `APPLY_MODE` already shapes PR descriptions elsewhere.
- **Path B** (Dockhand is genuinely load-bearing): new `dockhand-redeploy-stack`
  tool in `integrations/dockhand/`, gated behind its own opt-in flag, with an ADR
  amending ADR-013 written first — not bundled into this phase silently.

### Phase 8 — Direct Infisical write (deferred, not scheduled)
Only after Phase 4's copy/paste block has been used for real, for a while. Flip on
the `INFISICAL_ALLOW_WRITE`-gated path ADR-016 already reserved for this.

## Explicitly out of scope

- Anything that lets an agent merge the PR itself, or bypass Dockhand/Ansible CD's
  role as the actual deploy actor without Phase 0/7 resolving how. The PR+merge
  gate stays the one checkpoint every write path in this repo shares.
- Provisioning a *node* itself (OS install, base config) — stays out of scope per
  ADR-012, same as everywhere else in this codebase. This plan only ever writes
  `nodes/<node>/<service>/compose.yaml` for an already-provisioned node.
- Editing `nodes/control-plane/core/compose.yaml`, anything under `*/authentik/**`,
  or TLS/certresolver config as part of this flow — those stay under homelab's
  existing "confirm before acting" rules regardless of how this feature matures.
