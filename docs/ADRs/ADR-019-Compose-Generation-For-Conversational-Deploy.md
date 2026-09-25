# ADR-019: Compose Generation for Conversational Deploy (Phase 2)

| | |
|---|---|
| **Status** | Accepted |
| **Related** | `docs/plans/conversational-deploy.md` (the multi-phase plan this is Phase 2 of); [ADR-018](ADR-018-Repo-Intake-For-Conversational-Deploy.md) (Phase 1 intake, whose output this consumes); `docs/specs/spec-compose-normal-form.md` (the canonical form drafts are held to) |
| **Date** | 2026-09-22 |

## Context

ADR-018 shipped Phase 1: `service-intake-repo` turns a foreign repo's URL into
structured runtime requirements. Phase 2 turns those requirements into a draft
`compose.yaml` that follows this homelab's conventions. Phase 0's two open questions
are now settled (see the plan doc). New services deploy through the CD pipeline,
never Dockhand automation. The homelab repo's own convention docs
(`docs/spec/compose.yaml`, `references/homelab.md`) have measurable drift and are not
yet trustworthy as ground truth on their own.

Everything this codebase already generates *patches* an existing file: remediation
patches, normalization rewrites, review revisions, adoption sanitization. Compose
generation is the first path that writes a file from nothing. The deterministic
backstops built for patching (most importantly normalization's equivalence
guarantee) assume there is a "before" to compare against. Here there isn't.

## Decision

### A separate `service_deploy/` package, shaped like `proposal/`

`ComposeGenerator` (`service_deploy/generator.py`) follows `PatchGenerator`'s shape: it
calls the reasoning layer, owns every gate, and returns an `ok`/`rejection_reason`
result (`ComposeDraft`). It is a separate package for the same reason
`normalization/` is. A deploy draft should never share a code path, or (once Phase 5
opens PRs) a label, with a security remediation. Later deploy phases (placement,
secrets block, PR assembly) land in the same package.

### Conventions: this repo's rules always, the homelab spec as a supplement

The `homelab_conventions` input to the new `GenerateServiceCompose` signature is built
in two layers:

1. **Always present, deterministic:** `CANONICAL_FORM_SUMMARY` (the Tier 1 shape rules
   `NormalizeConfigFile` already receives) plus `REQUIRED_RULES_SUMMARY`. The second
   is `normalization/rules.check()`'s Tier 2 findings restated as requirements a
   fresh draft should meet up front: a pinned published image, `restart:`,
   `container_name` equal to the service key, `${PROXY_NETWORK:-swarm-net}` as the
   proxy network key, `# temporary` on any host port, and `${VAR}` for every secret.
2. **Supplementary, best-effort:** the homelab repo's compose spec at
   `SERVICE_DEPLOY_CONVENTIONS_PATH` (default `docs/spec/compose.yaml`), read through
   the configured `GitProvider`. This mirrors `PatchGenerator`'s `middleware.yml`
   fetch: a missing `GIT_*` config or a failed read falls back to layer 1 alone and
   never blocks generation. The prompt tells the model that layer 1 wins on
   conflict.

`references/homelab.md` is deliberately not included. It is a troubleshooting map,
self-described as "not gospel," and was found stale in Phase 0 recon.

### No fallback, and a shape gate in place of the equivalence gate

The gates run in the same order as `PatchGenerator`'s:

1. Credential scrub of both the draft and the returned reasoning text.
2. Confidence against `SERVICE_DEPLOY_CONFIDENCE_THRESHOLD`.
3. The draft is non-empty.
4. The draft is valid YAML.

Because there is no "before" file, normalization's `is_equivalent` gate has nothing
to compare against. A **compose-shape gate** takes its place: the draft must have a
non-empty top-level `services:` mapping containing the requested service key. Any
failed gate is a rejection with a stated reason, never a hand-written or partially
salvaged draft.

An accepted draft then runs through `normalization/formatter.normalize()`. The
formatter's own internal equivalence check still proves its reshaping changed nothing
Docker would see. If the formatter can't process the draft at all, that is a
rejection too.

### Tier 2 findings and skipped formatter rules are reported, never blocking

`rules.check()` runs on the canonicalized draft, and its findings come back alongside
it, exactly as a normalization sweep treats them. Examples are an unpinned image that
the README gave no version for, or a `build:` key when no published image exists.
These are judgment calls for the operator, not grounds for silent rejection. Any
Tier 1 rule the formatter skipped because a comment was in the way is reported the
same way. It is not escalated to `NormalizeConfigFile`: a second LLM call to fix
cosmetic key order on a fresh draft isn't worth its cost.

### A read-only tool now, ahead of the plan's Phase 5

The plan gave Phase 2 no tool of its own. `service-deploy-create` was to arrive at
Phase 5 and run Phases 1–4 together. This ADR ships `service-deploy-generate-compose`
now anyway. It is read-only and single-call like `service-intake-repo`, gated by the
existing `SERVICE_DEPLOY_ENABLED`, and returns the draft without persisting or
committing anything. The plan's own Phase 6 exists to "validate output quality
against a handful of real repos before trusting it live." Generation quality is the
biggest unknown in the whole flow, so it should be judged before placement, secrets,
and PR assembly are stacked on top of it. Expect Phase 5 to fold this tool into
`service-deploy-create` or remove it.

Supporting details:
- **Shared intake, re-run rather than passed in.** `run_intake()` was extracted from
  `service-intake-repo` so both tools share one fetch → parse → gated-inference path.
  The tool re-runs intake from the URL instead of accepting intake JSON from the
  caller, which keeps the ground-truth facts ground truth.
- **Fails before cloning without DSPy.** With `DSPY_ENABLED=false` there is nothing to
  generate and no fallback, so the tool returns an error before cloning anything.
- **Service name.** The name is taken from the caller first, then from accepted
  inference, then from the repo's last path segment. It must match
  `^[a-z0-9][a-z0-9._-]{0,62}$`, because Phase 5 will use it as a
  `nodes/<node>/<service>/` directory.
- **`target_node` is prompt context only.** It is not checked against
  `HardwareStore`; placement is Phase 3.

### Proxy network default is `swarm-net`

`${PROXY_NETWORK:-proxy-net}` (from ADR-003) is out of date: the homelab's default
proxy network is `swarm-net`. The new signature, the required-rules summary, R-005's
finding text, and the spec's R-005 row all name `${PROXY_NETWORK:-swarm-net}`.
Detection is unchanged, because R-005 matches on the `${PROXY_NETWORK` prefix.

> **Amended 2026-09-25:** the `${PROXY_NETWORK:-swarm-net}`-as-key form this section
> describes is invalid Compose. Compose interpolates values, never mapping keys, so a
> network declared as `${PROXY_NETWORK:-swarm-net}:` and joined as `- ${PROXY_NETWORK:-swarm-net}`
> fails with "service refers to undefined network swarm-net" (checked with Docker Compose
> v5.1.1). R-005 now reports a shared network (`NORMALIZATION_SHARED_NETWORKS`, default
> `swarm-net,proxy-net`) declared without `external: true`, and the required-rules summary and
> `GenerateServiceCompose` ask for that instead. An interpolated name, where wanted, goes in
> `name:` under a fixed key. The text above is kept as the record of what was decided then.

## Consequences

### Positive

- The biggest unknown in the conversational-deploy flow, whether generated compose
  files are actually good, can now be measured on real repos before Phases 3–5 depend
  on the answer.
- Drafts are held to the same canonical form and Tier 2 checks as every existing
  `compose.yaml` the normalization sweep sees. A generated file and a hand-written one
  are judged by one rulebook.
- The in-repo-rules-first layering means homelab-doc drift degrades the extra context,
  not correctness: the drift can't override the rules this repo tests.

### Negative / accepted tradeoffs

- **A tool with a planned expiry.** `service-deploy-generate-compose` is expected to be
  replaced at Phase 5. It is a small read-only surface to retire later, accepted in
  exchange for early quality signal.
- **In-repo rules can lag reality too.** The `swarm-net` correction arrived while this
  phase was being built. `REQUIRED_RULES_SUMMARY` is only as current as whoever last
  corrected it.
- **Homelab-spec drift still reaches the prompt** whenever `GIT_*` is configured. The
  precedence instruction mitigates it but doesn't eliminate it. A model can still pick
  up a stale detail layer 1 doesn't cover (a Traefik label convention, for example).
  Refreshing that doc is a separate task in `ncastaldi/homelab`.
- **Whole-file output on the patch LM's larger token budget**, so each call costs more
  than intake's short-field inference.
- **No validation yet against real repos.** The gates catch malformed or
  low-confidence drafts, not plausible-but-wrong ones (a wrong port, a missing
  volume). That judgment still belongs to the human reviewing the eventual PR.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Put `ComposeGenerator` in `proposal/` | Rejected. Same separation reasoning as `normalization/`: a deploy draft must never share a code path or PR label with a security remediation. |
| Require the homelab spec (fail when it can't be read) | Rejected. That doc is known to drift, and requiring it would make generation unusable without write-path `GIT_*` config that a read-only phase doesn't otherwise need. |
| Escalate formatter-skipped rules to `NormalizeConfigFile` | Rejected. A second LLM call to reorder keys on a fresh draft; reporting is enough until Phase 5 commits anything. |
| Reject drafts with any Tier 2 finding | Rejected. Normalization reports these as judgment calls; rejecting here would discard useful drafts, e.g. one pointing out that no published image exists. |
| Library-only until Phase 5's `service-deploy-create` | Rejected in favor of an early, read-only way to evaluate generation on real repos. |
| Accept intake JSON from the caller instead of re-running intake | Rejected. The "detected facts are ground truth" guarantee only holds if this server extracted them itself. |

## References

- `docs/plans/conversational-deploy.md`: the full plan; this ADR covers Phase 2
- `src/registry_mcp/service_deploy/generator.py`: `ComposeGenerator`, `ComposeDraft`,
  `REQUIRED_RULES_SUMMARY`
- `src/registry_mcp/dspy/signatures.py`: `GenerateServiceCompose`
- `src/registry_mcp/tools/service_deploy.py`: the `service-deploy-generate-compose`
  tool
- `src/registry_mcp/tools/intake.py`: `run_intake()`, shared with
  `service-intake-repo`
- `src/registry_mcp/normalization/`: `formatter.normalize()` and `rules.check()`,
  reused unchanged apart from R-005's message

---

*ADR-019 | github.com/TeamCastaldi/homelab-registry-mcp | MIT License | 2026*
