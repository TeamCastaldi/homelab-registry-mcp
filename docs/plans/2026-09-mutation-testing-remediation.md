# Mutation testing remediation plan

**Status:** Phase 0 (tooling) complete. Phases 1–7 not started.
**Supersedes:** Tier 4 ("wiring and coverage gaps") of
[`2026-09-test-suite-audit.md`](2026-09-test-suite-audit.md). That tier was a
hand-compiled list of gaps found by reading code against test files; this plan
replaces it with an exhaustive, automated list from actually breaking the code
and watching whether any test notices. Tier 4's specific named gaps are still
real and still open — they now show up as concrete mutant IDs in the data this
plan works from, rather than as a separate list to reconcile by hand.

## Background

`mutmut` (config in `pyproject.toml`'s `[tool.mutmut]`, workflow in
`tests/README.md`'s "Mutation testing" section) was added and run against all
of `src/registry_mcp/` on 2026-09-26/27. It works by changing one small thing
in the source at a time — a comparison operator, a constant, an argument — and
rerunning the suite. If a test fails, that mutation is **killed**: something
was watching that code. If every test still passes, it **survived**: nothing
would notice if that exact thing broke for real.

Full-tree result: **11,462 mutants** — 7,236 killed (63%), 3,868 survived
(34%), 344 with no covering test at all, 14 timeouts. 36 of 103 files came
back fully clean. The other 67 files have at least one survivor; **4,226
mutants across those 67 files** are the actual scope of this plan.

**Not every survivor is a bug waiting to happen.** A mutation testing run
always includes "equivalent mutants" — changes that are genuinely harmless
(reworded log text, a docstring, an alternate spelling of the same value) that
no test should ever need to catch. The real work per file is reading each
survivor's diff, sorting real gap from harmless noise, and writing (or
strengthening) a test for the real ones — the same read-the-actual-diff
discipline this branch's Tier 2/Tier 3 work already used, just pointed at
`mutmut show`'s output instead of hand-picked suspects. On the one file
already triaged this way (`adoption/ssh.py`, 58 survivors), that came down to
one real, serious gap (no test exercises the constructed SSH command line —
a mutant changing the identity-file flag `-i` to the not-real flag `-I`
survives, which would break every real adoption SSH call in production) and
one cosmetic one (an error message's exact text). Expect a similar ratio
elsewhere: the phase counts below are upper bounds on effort, not a promise of
that many new tests.

## Workflow (per file, every phase)

1. `uv run mutmut results` — list that file's survivors (`grep` the module
   path).
2. `uv run mutmut show <id>` for each — read the actual diff.
3. Classify: **real gap** (the mutated behavior is something a caller could
   observe and would care about) vs. **equivalent/low-value** (message
   text, formatting, something no caller can observe, or already covered by
   a stronger check elsewhere — verify that claim the way Tier 3 did, don't
   just assert it).
4. For real gaps, write or extend a test. Mutation-probe it in a throwaway
   worktree exactly like Tier 1–3: apply the mutation, confirm the new test
   fails, revert, confirm it passes clean.
5. Run the full suite + `ruff check` + `ruff format --check`.
6. Commit with a short note on what was found and what was judged
   equivalent (so the next session doesn't re-litigate the same survivor).
7. Re-run `mutmut run` for that file's mutants only (`only_mutate` scoped in
   `pyproject.toml`, temporarily) to confirm the new tests actually moved
   mutants from survived to killed, not just that new tests exist.

"Done" for a phase is a short written summary (real gaps found and fixed,
equivalent mutants identified and why) — not a target test count.

## Phases

Grouped by subsystem and risk, front-loaded toward the write path (the code
that actually commits changes or handles secrets) and highest-count files.
Phase 7 is a long tail of smaller files and is coarser-grained; it will likely
get re-split once the earlier phases show how long a file this size actually
takes.

### Phase 1 — Write path core (~1,101 mutants)

The code that decides what gets committed and where. Highest stakes: a silent
gap here means a proposal or secret write can go wrong with nothing to notice.

| File | Survivors |
|---|---|
| `proposal/engine.py` | 364 |
| `gitcrypt.py` | 187 |
| `providers/git/gitea.py` | 157 |
| `providers/git/github.py` | 156 |
| `proposal/generator.py` | 138 |
| `proposal/adoption.py` | 54 |
| `proposal/lifecycle.py` | 28 |
| `proposal/store.py` | 13 |
| `providers/git/__init__.py` (factory/protocol) | 4 |

Already known from the numbers alone (see prior turn's spot check):
`ProposalEngine._resolve_target` — the function that computes which file on
disk a proposal writes to — has a surviving mutant that swaps the real
hostname for `None` in the path. Start here.

### Phase 2 — Reasoning/gating layer (~447 mutants)

| File | Survivors |
|---|---|
| `dspy/reasoner.py` | 447 |

One file, but the single largest concentration in the codebase, and it's the
confidence-threshold/no-fallback gate every DSPy-backed feature (patches,
normalization, adoption secret detection, compose generation) depends on.
`dspy/signatures.py` has zero survivors — it's declarative (Signature field
definitions), nothing to mutate meaningfully.

### Phase 3 — Normalization engine (~530 mutants)

| File | Survivors |
|---|---|
| `normalization/engine.py` | 199 |
| `normalization/formatter.py` | 132 |
| `normalization/generator.py` | 98 |
| `normalization/rules.py` | 63 |
| `normalization/scanner.py` | 38 |

Kept as one phase because the subsystem is kept as one unit in the source
(CLAUDE.md: "kept separate from `proposal/` on purpose") with its own
equivalence-guarantee gate (`rules.is_equivalent`) — a survivor in `rules.py`
specifically could mean that gate itself has a blind spot, which is worth
knowing before trusting it further.

### Phase 4 — Core registry + server wiring (~355 mutants)

| File | Survivors |
|---|---|
| `server.py` | 174 |
| `registry/store.py` | 163 |
| `registry/reconcile.py` | 18 |

### Phase 5 — Discovery, hardware, inbound webhooks (~694 mutants)

| File | Survivors |
|---|---|
| `discovery/engine.py` | 128 |
| `webhooks/schemas.py` | 103 |
| `discovery/authentik.py` | 99 |
| `hardware/store.py` | 87 |
| `webhooks/dockhand.py` | 77 |
| `hardware/ansible_facts.py` | 77 |
| `discovery/traefik.py` | 52 |
| `discovery/docker.py` | 52 |
| `discovery/outpost.py` | 9 |
| `discovery/scheduler.py` | 6 |
| `discovery/dockhand.py` | 4 |

### Phase 6 — Conversational deploy + brownfield adoption (~306 mutants)

| File | Survivors |
|---|---|
| `intake/fetch.py` | 73 |
| `service_deploy/generator.py` | 62 |
| `adoption/ssh.py` | 58 (partially triaged already — see Background) |
| `tools/adoption.py` | 41 |
| `intake/parse.py` | 39 |
| `tools/intake.py` | 23 |
| `tools/service_deploy.py` | 6 |
| `adoption/store.py` | 4 |

### Phase 7 — Everything else (~793 mutants, ~29 files)

Provisional grouping — smaller files, likely to get re-split by actual
subsystem once reached. Roughly in descending survivor count:
`integrations/infisical/client.py` (91), `health.py` (79),
`config_report.py` (78), `seed.py` (77), `providers/notification/smtp.py`
(54), `integrations/authentik/client.py` (48),
`integrations/dockhand/client.py` (41), `integrations/traefik/client.py`
(34), `tools/linking.py` (24), `tools/hardware.py` (24),
`logging/events.py` (23), `providers/notification/ntfy.py` (22),
`integrations/authentik/tools.py` (19), `inventory/writer.py` (28 — note:
`W1` in the old Tier 4 list already covered one specific gap here; the rest
are unreviewed), `inventory/store.py` (18), `deletion/store.py` (18),
`tools/registry.py` (13), `providers/notification/__init__.py` (13),
`integrations/dockhand/tools.py` (13), `tools/secrets.py` (12),
`logging/tool_calls.py` (12), `integrations/docs/client.py` (10),
`integrations/traefik/tools.py` (9), `tools/discovery.py` (7),
`providers/notification/null.py` (7), `tools/proposal.py` (6),
`integrations/infisical/tools.py` (6), `integrations/docs/tools.py` (5),
`models/service.py` (1), `models/hardware.py` (1).

## Non-goals

- Not every one of the 4,226 becomes a new test. Equivalent mutants get
  recorded as reviewed-and-skipped with a one-line reason, same as Tier 3's
  three "audit was wrong, kept the test" writeups — the bar is "would a real
  regression here matter and go unnoticed," not "kill every mutant."
- This plan does not re-run mutation testing continuously or gate CI on it.
  That's a separate, later decision once the current survivor backlog is
  worked down — revisit after Phase 7.
- Phase boundaries past Phase 3 or so are working estimates. Expect
  resequencing as earlier phases reveal how long a file this dense actually
  takes.
