# Conversational Service Deployment — Build Plan

**Status:** In progress. Phase 0 recon run 2026-09-22 against the live
`homelab-registry-mcp` server; partially resolved — see findings inline below.
A follow-up recon session scoped directly to `ncastaldi/homelab` (also
2026-09-22) resolved the convention-freshness item — see "Phase 0 recon
findings (session 2)". **The deploy-mechanism fork is now resolved — Nathan
directly, 2026-09-22:** he's still on Dockhand today but wants off it; Phase 7
targets the CD pipeline exclusively for newly-deployed services (no
`dockhand-redeploy-stack` tool) — see "The deploy-mechanism fork" below.
**Phase 1 (repo ingestion)
shipped 2026-09-22 — [ADR-018](../ADRs/ADR-018-Repo-Intake-For-Conversational-Deploy.md).
Phase 2 (compose generation) shipped 2026-09-22 —
[ADR-019](../ADRs/ADR-019-Compose-Generation-For-Conversational-Deploy.md).**
Phases 1-2 don't touch node identity, so Phase 1 proceeded without Phase 0's
open items being resolved; Phase 3 still needs the node-identity question
settled (de-risked but not resolved, see below) — the deploy-mechanism fork
no longer blocks it.
**Written:** 2026-09-21.
**Origin:** Grew out of evaluating whether artifacts in the separate `ncastaldi/ansible`
repo could feed `homelab-registry-mcp`; that evaluation surfaced open questions
(below) that block part of this plan, and the goal itself expanded into this
document. Each phase gets its own ADR at implementation time, the same way every
other phase in this codebase has (ADR-010, ADR-013, ADR-015, ADR-016, ADR-018, ...).

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
| Homelab conventions as generation input | **Wired (Phase 2)** | `normalization/` canonical rules always; homelab `docs/spec/compose.yaml` supplementary via `SERVICE_DEPLOY_CONVENTIONS_PATH`. `references/homelab.md` deliberately excluded (stale) |
| Complete-file generation, confidence + YAML gated, no rule-based fallback | Pattern exists | `dspy/signatures.py` (`GenerateRemediationPatch`, `NormalizeConfigFile`) |
| Branch → commit → PR | Exists as-is | `providers/git/`, `proposal/engine.py`'s `_open_proposal` |
| Dry-run before any real write | Pattern exists | `PROPOSAL_DRY_RUN`, `NORMALIZATION_DRY_RUN` |
| Generated-not-guessed secret values | Pattern exists | `adoption/` "rotate" path — `secrets.token_urlsafe`, never DSPy-generated |
| Two-call human-decision gate before anything commits | Pattern exists | `proposal_adopt_service` → `proposal_adopt_service_finalize` |
| Repo ingestion (Dockerfile/compose/README → structured requirements) | **New** | closest analog: `adoption/ssh.py`'s live-container inspection, but reads a foreign repo, not a live container |
| Compose generation for a **new** stack (vs. patching a known file) | **Shipped (Phase 2, ADR-019)** | `GenerateServiceCompose` + `service_deploy/ComposeGenerator` |
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

  **Resolved 2026-09-22 — Nathan directly.** He confirmed he is still deploying via
  Dockhand today — so Dockhand is genuinely load-bearing right now, not just habit;
  the empty `dockhand_list_stacks`/`dockhand_list_containers` results above were a
  token-scope or agent-connectivity artifact, not evidence Dockhand is unused. But
  he wants to move *away* from Dockhand where possible. That's neither Path A nor
  Path B as originally framed:

  - Not Path A's premise ("Dockhand is just habit, nothing to build") — it's
    currently real.
  - Not Path B's conclusion (build `dockhand-redeploy-stack` to automate Dockhand
    itself) — that would entrench the tool Nathan wants to leave.

  **Decision: build toward Path A's outcome anyway.** The CD pipeline
  (`docker-stack-deploy` + reusable `deploy.yml`) is already proven end-to-end and
  is the direction Nathan wants to move toward. So for this plan's Phase 7, a
  conversationally-deployed service should land on the CD pipeline (a plain
  `nodes/<node>/<service>/compose.yaml` PR, merged = deployed), never on Dockhand —
  no `dockhand-redeploy-stack` tool gets built. This doesn't migrate Nathan's
  *existing* Dockhand-deployed services (that's separate, pre-existing infra, out
  of this plan's scope) — it just makes sure every *new* service this feature
  deploys goes straight onto the pipeline he's trying to consolidate onto, rather
  than adding one more thing clicked in Dockhand.

**Hardware/node identity drift**, surfaced while evaluating the `ncastaldi/ansible` repo,
unresolved as of this writing:

- `homelab-control-plane` = `watchtower`, confirmed (same Pi, documented rename).
- `p410` (ansible repo, active inventory, 10.0.0.201, Ansible-managed AI/dev-swarm
  node) vs. `panoptichron` (homelab troubleshooting reference, same IP, explicitly
  "Hands-off... Not Ansible-onboarded") — a direct contradiction, not yet resolved.
  Whether this is the same box renamed/repurposed or a stale abandoned experiment is
  unknown from the repos alone.

  **Partially de-risked by the 2026-09-22 recon, still open.** Live
  `hardware-list-nodes` (all statuses — confirmed/unconfirmed/stale all checked)
  returns exactly four `HardwareNode` rows: `homelab-control-plane` (10.0.0.200),
  `waldorf` (10.0.0.251), `heimdall` (10.0.0.151), `p1ollama` (10.0.0.203). Neither
  `p410`, `panoptichron`, nor 10.0.0.201 appears at all — not confirmed, not
  unconfirmed, not stale. That means the "worse" risk this section originally
  called out (placement proposing the hands-off `panoptichron` node under a
  different name) can't currently happen: `hardware-list-nodes` /
  `hardware-capacity-summary` simply never return it as a candidate. It does
  *not* answer what `p410`/`panoptichron` actually is or whether it should
  eventually be onboarded. **Update (2026-09-22 follow-up session against
  `ncastaldi/homelab`):** the conflict is real and already tracked — it's logged
  as unresolved in that repo's own `ansible-repo-consolidation` plan document
  (not reachable from this repo; ask Nathan or a future `ncastaldi/homelab`-scoped
  session for that plan's current state). Re-check this once `ncastaldi/ansible`
  itself is reachable, or once a future `hardware-discover-now` sweep or manual
  add changes what this registry returns.
- `dev5432` (ansible repo, 10.0.0.203) appears to be `p1ollama`/"ollama" in the
  homelab repo — the node `homelab-registry-mcp` itself now runs on — but nothing
  in the ansible repo reflects that role change.

  **Resolved by the 2026-09-22 recon.** Live `hardware-list-nodes` confirms
  10.0.0.203 is `p1ollama` in the `HardwareStore`, and live
  `dockhand_list_environments` independently confirms the same IP as Dockhand's
  "Ollama" environment. Both live sources agree with the homelab-repo side of the
  contradiction; the `ansible` repo's `dev5432` entry is the stale one. Nothing
  left to resolve here — the ansible repo's inventory should be updated to match,
  whenever that repo is next touched.

This matters here because Phase 3 (placement) trusts `hardware-list-nodes` /
`hardware-capacity-summary` output to pick a deploy target. If the underlying
`HardwareNode` rows carry stale or contested identity, placement can propose a node
that's wrong, or — worse — the hands-off `panoptichron` node under a different name.
Resolve before Phase 3 ships, not necessarily before Phases 1-2 (which don't touch
node identity). **As of the 2026-09-22 recon, the "worse" scenario is ruled out
(see above) — Phase 3 can proceed on the current registry contents without risk of
silently targeting `panoptichron`. The node's true identity/purpose is still worth
settling for its own sake, just no longer a blocker for Phase 3 specifically.**

**Convention freshness.** `docs/spec/compose.yaml` and `references/homelab.md` (fed
into Phase 2's generator as ground truth) should be spot-checked as current — the
troubleshooting reference is explicitly dated ("last confirmed 2026-08-17") and
self-describes as "a map, not gospel."

**Resolved (spot-checked) by a 2026-09-22 follow-up session scoped directly to
`ncastaldi/homelab`.** Cross-tier `add_repo` is confirmed to work when a session's
*initial* source is the target repo (it only fails when added mid-session to a
session already scoped to a different owner). That session found real drift and
staleness in both files — **they are not yet trustworthy as generation input as-is**:

- `docs/spec/compose.yaml`: an Authentik version pin, `container_name` consistency,
  and labels format have drifted from what live `nodes/*/*/compose.yaml` files
  actually look like; also flagged a note about swarm migration percentage that
  needs a closer read.
- `references/homelab.md`: at least 2 service location entries are stale, and its
  Komodo/Dockhand description doesn't match the current setup (consistent with the
  Komodo-removal history in this repo's own ADR-011).

These need a refresh pass before Phase 2 can safely use them as generation ground
truth. The session that found this ran with a summarized-only handoff — the exact
line-level diffs live in that session's own transcript (`session_01XVvKD5vvH93gENk6ThUpP2`),
not reproduced here; re-run a targeted follow-up against `ncastaldi/homelab` for the
precise line-by-line fixes before actually implementing Phase 2's generator.

## Phase 0 recon findings (2026-09-22)

Run live against the production `homelab-registry-mcp` MCP server (`health` reported
`version: 1.6.2`) from a session scoped to `TeamCastaldi/homelab-registry-mcp` only.
Summary — two of the three open questions above got real evidence, one couldn't be
touched at all from this vantage point:

| Question | Outcome |
|---|---|
| `dev5432` vs. `p1ollama` | **Resolved.** Both `hardware-list-nodes` and `dockhand_list_environments` independently confirm 10.0.0.203 = `p1ollama`/"Ollama". |
| `p410`/`panoptichron` placement risk | **De-risked, not resolved.** Not present in `hardware-list-nodes` (checked confirmed + `hardware-list-unconfirmed` + `hardware-list-stale`, all empty for it) — Phase 3 can't accidentally target it. Its actual identity/purpose is still unknown from here. |
| Deploy-mechanism fork | **Resolved — Nathan directly, 2026-09-22 (see "The deploy-mechanism fork" above).** Dockhand is genuinely in use today; the empty `dockhand_list_stacks`/`dockhand_list_containers` results were a scope/connectivity artifact, not evidence of disuse. Direction: move away from Dockhand — Phase 7 targets the CD pipeline exclusively. |
| Convention freshness | **Resolved (see "Phase 0 recon findings (session 2)" below).** Both files have real drift/staleness — not yet trustworthy as Phase 2 generation input. |

## Phase 0 recon findings (session 2, 2026-09-22)

Run against a fresh session with `ncastaldi/homelab` as its *initial* source (confirming
`add_repo` works fine for a same-owner attach — the earlier failure was specifically a
cross-owner mid-session add). Scope: convention-doc freshness only, plus the
`p410`/`panoptichron` side-question found while checking `references/homelab.md`; the
deploy-mechanism fork (Task 1 in that session's brief) was **not** covered by anything
this parent session could retrieve back — see caveat below.

- **Convention freshness: resolved, and the answer is "not yet trustworthy."** See the
  "Convention freshness" section above for the specific drift found in
  `docs/spec/compose.yaml` and `references/homelab.md`.
- **`p410`/`panoptichron`: still open, but now cross-referenced** — `ncastaldi/homelab`
  itself has an `ansible-repo-consolidation` plan doc that already tracks this as
  unresolved. Worth reading that plan directly next time a session has `ncastaldi/homelab`
  in scope, rather than re-deriving it from scratch.
- **Deploy-mechanism fork: still open.** This parent session only receives a compressed
  status summary back from a child session it spawns (no full-transcript access), and
  that summary didn't mention Task 1 at all — it's unknown whether the child session
  investigated it and the finding was lost in summarization, or never got to it. **Needs
  a dedicated follow-up session** (scoped to `ncastaldi/homelab`, Task 1 only) to get a
  real answer, or Nathan's direct read on Dockhand-vs-CD-pipeline.

Next step for whoever picks this back up: run a follow-up `ncastaldi/homelab`-scoped
session focused solely on the deploy-mechanism fork (check `.github/workflows/deploy.yml`'s
actual run history against `nodes/**/compose.yaml` pushes, per the "Deploy-mechanism fork"
section above), and separately get the line-level convention-doc fixes this session's
compressed summary didn't preserve.

## Phased plan

Each phase is independently useful and testable before the next depends on it.
Stopping after Phase 5 already delivers "AI drafts the compose + tells me what
secrets to add" as a working tool.

### Phase 0 — Recon (no code)
Resolve the deploy-mechanism fork and the node-identity questions above. Confirm
the convention docs are current enough to trust as generation input. Everything
past this phase assumes these are settled.

### Phase 1 — Repo ingestion — **shipped 2026-09-22, [ADR-018](../ADRs/ADR-018-Repo-Intake-For-Conversational-Deploy.md)**
New package `intake/` (shape mirrors `adoption/`, `normalization/`):
- `intake/fetch.py` — shallow-fetch a repo, locate Dockerfile / `docker-compose.yml`
  / `compose.yaml` / README. Went further than originally sketched here: the URL is
  an https-only allowlist (rejects `ext::`/`file://`/`ssh://`/scp-style, real
  argument-injection and arbitrary-command/disk-read/foreign-SSH-key vectors on
  `git clone`), and every file read is symlink-contained the same way
  `gitcrypt.check_path` contains repo-relative writes — a cloned repo is untrusted
  content, not just an untrusted URL.
- `intake/parse.py` — deterministic parsing only: exposed ports, env vars, volumes,
  base image. No LLM here, same discipline as `reconcile.py` staying detection-only.
  Compose wins over Dockerfile on conflict; Dockerfile-only facts stay additive.
- New DSPy signature `InferServiceRequirements` (`dspy/signatures.py`) — only for
  what deterministic parsing can't get (e.g. "does this need a database" from README
  prose). Confidence-gated on `SERVICE_DEPLOY_CONFIDENCE_THRESHOLD`; a
  below-threshold result is discarded and reported as discarded
  (`inference: null` + `inference_rejection_reason`), never guessed.
- New MCP tool `service-intake-repo(repo_url)` → structured requirements JSON.
  Read-only, no confirm gate needed — it writes nothing.
- `SERVICE_DEPLOY_ENABLED` landed with this phase rather than at Phase 6 as
  originally sketched below — see ADR-018's rationale (intake itself grants an MCP
  client outbound fetch to a caller-supplied host, which is the capability worth
  gating from the start, independent of any later write path).

### Phase 2 — Compose generation — **shipped 2026-09-22, [ADR-019](../ADRs/ADR-019-Compose-Generation-For-Conversational-Deploy.md)**
- New DSPy signature `GenerateServiceCompose(intake, homelab_conventions,
  service_name, target_node) -> compose_yaml` — `service_name` added so the draft's
  service key (and later its `nodes/<node>/<service>/` directory) is fixed by the
  caller, not left to the model.
- New package `service_deploy/` (mirrors `proposal/`'s shape):
  `service_deploy/generator.py` calls the signature, gates on
  `SERVICE_DEPLOY_CONFIDENCE_THRESHOLD` + YAML validity — same no-fallback
  discipline as `GenerateRemediationPatch`. Low-confidence or invalid output is
  never turned into a draft. With no "before" file for `rules.is_equivalent`, a
  compose-shape gate (a `services:` mapping containing the requested key) takes
  its place.
- The result runs through `normalization/formatter.py`'s canonical-shape rules
  before it's shown to anyone; Tier 2 findings are reported alongside, not
  blocking.
- Conventions went further than sketched here: this repo's own canonical rules are
  always sent and take precedence, and only `docs/spec/compose.yaml` is read from
  the homelab repo (best-effort, via `GitProvider.read_file`) — both homelab docs
  were found to drift in Phase 0 recon, and `references/homelab.md` was dropped as
  input entirely.
- **Deviation:** a read-only `service-deploy-generate-compose` tool shipped now, not
  deferred to Phase 5, so generation quality (Phase 6's stated purpose) can be judged
  on real repos early. Expect Phase 5's `service-deploy-create` to absorb or replace
  it.

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
- ~~`SERVICE_DEPLOY_ENABLED`~~ — landed with Phase 1 instead (ADR-018); intake's
  outbound-fetch capability needed gating from its own introduction, independent
  of this phase's write-path dry-run concern.
- `SERVICE_DEPLOY_DRY_RUN` (recommend defaulting true on first rollout, matching
  `PROPOSAL_DRY_RUN`'s stance) — `service-deploy-finalize` logs the would-be PR
  instead of opening it. Use this to validate output quality against a handful of
  real repos before trusting it live.

### Phase 7 — Deploy automation
**Resolved 2026-09-22 (Nathan directly) — targets Path A's outcome exclusively.**
Nathan is still deploying via Dockhand today but wants to move away from it, so
this phase never builds Dockhand automation (`dockhand-redeploy-stack`, Path B) —
that would entrench the tool he's trying to leave. Instead: no new deploy tool.
Harden/verify the existing `docker-stack-deploy` role covers the chosen node;
`service-deploy-finalize`'s PR body states that merging it is the deploy step
(mirroring how `APPLY_MODE` already shapes PR descriptions elsewhere). This only
governs *newly* conversationally-deployed services — it doesn't migrate Nathan's
existing Dockhand-managed services onto the CD pipeline; that's separate,
pre-existing infra work outside this plan's scope.

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
