---
description: Run a full, evidence-based audit of the test suite against the testing-standards skill's rules, and write a dated, machine-checkable report.
disable-model-invocation: true
---

# Audit tests

Read `TEST_COMMAND` and `LINT_COMMAND` from the `## Session Config` section of
`CLAUDE.md`. Load the `testing-standards` skill first — every judgment call
below applies its rules (the four properties, the M1–M5/F1 mock-and-fake
table, T1/R1–R3/G1) rather than restating them here.

**Role:** A meticulous test engineer doing a for-cause audit, not a
rubber-stamp coverage check. Assume every test is redundant or a tautology
until the application behavior it guards can be named. This is a deliberate,
occasional, and fairly expensive procedure — run it after a large refactor,
when CI time or flakiness starts to hurt, or when asked to check whether the
suite can actually be trusted. It is not a per-session habit; that's what the
`testing-standards` skill is for.

## Phase 0: baseline

1. Run `{TEST_COMMAND}` and record the pass/fail count. Every later step must
   reproduce this exactly — an audit changes no test. If anything fails for
   reasons unrelated to what you're about to audit (a broken local toolchain,
   a missing system library), note it and its cause; judge those tests on
   their assertions, not their current pass status.
2. Confirm (or create) `docs/audits/` at the repo root. If it doesn't exist
   yet, create `docs/audits/README.md` with a short note: this folder holds
   one dated subfolder per test-suite audit (`YYYY-MM-DD-test-suite/`), each
   with a machine-validated `files.json` plus a human-readable `README.md`
   summary; see the `testing-standards` skill and this command for the
   procedure and schema.
3. Create today's audit folder: `docs/audits/YYYY-MM-DD-test-suite/`.

## Phase 1: file-by-file audit

Work through the test suite one file (or one small batch) at a time. For
each test:

1. Identify the application code it exercises. Read that code, not just the
   test — a tautology is usually only visible from the code side.
2. Apply the `testing-standards` skill's §3 mock/fake table and §4 rules.
3. Record a finding only for a test that is **not** KEEP.

Write one JSON object per test file into this audit's `files.json`:

```json
{
  "file_name": "test_example.py",
  "logic_flaws": "Brief summary of tautologies or poor mocks, or \"None\"",
  "is_redundant": false,
  "unique_value": "One sentence naming the unique application logic this validates, or \"None\"",
  "verdict": "KEEP",
  "rationale": "One sentence justifying the verdict",
  "split_recommendation": null,
  "findings": [
    {
      "test": "test_name_or_Class::test_name",
      "verdict": "REWRITE",
      "rules": ["M2"],
      "rationale": "One sentence naming the flaw and the fix"
    }
  ]
}
```

- **`findings`** lists only non-KEEP tests. Name a parametrized test once, by
  its function name, without parameter IDs.
- **`rules`** uses the codes from the `testing-standards` skill: M1–M5, T1,
  R1, R2, R3, G1. (F1 — a fake too forgiving — is a property of the fake
  itself, usually shared by many tests; record it once in `logic_flaws` or as
  a coverage gap in `gaps.json` below rather than tagging every test that
  happens to use the fake.)
- **`verdict` (file level) follows from `findings`, never chosen separately:**
  `DELETE` only when `findings` covers every test in the file and every
  finding is DELETE; `REWRITE` when `findings` is non-empty; `KEEP` only when
  `findings` is empty.
- **`is_redundant`** is `true` when any finding carries R1, R2, or R3.
- **`split_recommendation`** is `null` or one sentence naming the split along
  the unit under test (skill §6). It never affects the verdict.
- **Edge cases:** a file with no assertions gets verdict `DELETE`, rationale
  `"No active assertions found"`. A file that doesn't parse gets `REWRITE`,
  rationale `"Syntax invalid"`.
- **Style is out of scope.** No formatting findings — `{LINT_COMMAND}` owns
  that.

Validate each batch before moving on:

```bash
python3 .claude/skills/testing-standards/scripts/validate_test_audit.py \
  docs/audits/<date>-test-suite/files.json
```

Add `--tests-file <path>` once you have a real collected test list (Phase 2
shows how to produce one per stack) to also verify that every test named in
`findings` actually exists, and add `--complete` for the final check that
every test file in the repo has exactly one entry.

## Phase 2: coverage-based redundancy shortlist

Produce the real test list your stack's runner can give you, one line per
test as `<file_name><TAB><test_id>`, for `validate_test_audit.py --tests-file`:

| Stack | Collect | Coverage-context run |
|---|---|---|
| Python / pytest | `pytest --collect-only -q` | `pytest --cov=<src> --cov-context=test --cov-report=` (writes `.coverage`) |
| JS/TS / vitest | `vitest list` | `vitest run --coverage` (Istanbul) |
| JS/TS / jest | `jest --listTests` | `jest --coverage` |
| Go | `go test -list '.*' ./...` | `go test -covermode=count -coverprofile=cover.out ./...` |
| Rust | `cargo test -- --list` | `cargo llvm-cov` or `cargo tarpaulin` |
| Java/Kotlin | build-tool test list task | JaCoCo with per-test context (TestNG/JUnit listeners) |

The point of a per-test coverage run isn't the aggregate percentage — it's
identifying pairs where one test's executed lines are a subset of another's.
That is a **shortlist of possible redundancy only.** Review every shortlisted
pair by hand: covering the same lines is not the same as asserting the same
thing, and an error-path test very often shares every line with the
happy-path test that reaches the same code while checking something
different. Record reviewed pairs, and what each was found to actually cover,
in `cross-file.json` alongside `files.json`.

## Phase 3: mutation-testing spot checks

Passing tests prove nothing survived; they don't prove a test would actually
*fail* if the code broke. A mutation tool plants small changes in the source
(flip a comparison, change a constant, drop a call, invert a condition) and
reruns the suite against each one. A mutant no test catches is either a real
gap or an equivalent mutant (behaviorally identical, uncatchable by any test)
— telling those apart takes reading the actual diff, not trusting the label.

Scope runs to pure-logic modules (parsers, validators, scoring/ranking code)
and the test files that exercise them — mutating a whole integration suite is
slow and noisy, and the highest-value survivors are almost always in
deterministic logic. Pick a tool for the stack in use:

| Stack | Tool |
|---|---|
| Python | `mutmut` |
| JavaScript/TypeScript | Stryker Mutator (`@stryker-mutator/core`) |
| Java/Kotlin | PIT (`pitest`) |
| Rust | `cargo-mutants` |
| Go | `go-mutesting`, or gremlins |
| C#/.NET | Stryker.NET |

**A mutation tool's assumptions about your layout can conflict with a real
project's.** Some tools reserve a package or directory name, derive module
names from file paths in a way that disagrees with a custom import root, or
otherwise refuse to run in place. If a tool won't start against the real
tree, don't fight it there: copy the source, tests, and manifest into a
scratch directory, make the minimum rename or path adjustment the tool needs
(renaming *within* lines keeps line numbers intact, so results still map 1:1
onto the real files), confirm the scoped tests still pass in the copy, then
run the mutation tool there. Never commit the scratch copy.

Run it, then triage survivors into three groups:

- **message or log text** — wording no test should assert against;
- **equivalent mutants** — the behavior doesn't actually change (rounding
  within a test's tolerance, a clamp no valid input can reach);
- **real gaps** — send that test's file verdict back to `REWRITE` if it was
  marked KEEP.

Report both the raw kill rate and the effective rate over mutants that
actually change behavior (excludes message/log and equivalent mutants).
Record results in `mutation.json`.

## Phase 4: write up and validate

1. Record any real, untested behavior found along the way that the per-test
   schema can't express — a coverage gap with no existing test to flag, a
   fake that's too forgiving, a setting nothing wires up — as a `gaps.json`
   entry: `{"id": "G1", "severity": "high"|"medium"|"low", "gap": "one
   sentence", "linked_findings": <count>}`. Rank by severity.
2. Write `docs/audits/<date>-test-suite/README.md`, with every total computed
   from `files.json` — never typed by hand. Include: headline numbers,
   verdicts-by-file table, findings-by-rule table, any suggested file splits,
   and a short account of the evidence tools' results.
3. Final validation:

   ```bash
   python3 .claude/skills/testing-standards/scripts/validate_test_audit.py \
     docs/audits/<date>-test-suite/files.json --tests-file <list> --complete
   ```

   And confirm `{TEST_COMMAND}` still reproduces the Phase 0 baseline exactly
   — the audit must not have changed any test.

## Phase 5: hand off to remediation

Acting on verdicts is separate work from the audit itself. Turn the findings
into a phased remediation plan in `docs/plans/` (ordered by risk: anything
touching security, data loss, or a wrong write goes first), and list it on
whatever this repo uses to track active plans. Do not fix tests as part of
this command — the audit's value depends on it changing nothing.

## Escalation

- **A verdict depends on intent you can't infer from the code, tests, or
  docs.** Mark it `REWRITE` and state the open question in `rationale`. Never
  guess `KEEP` or `DELETE`.
- **A mutation run is taking too long.** Narrow the test selection to the
  files that exercise the module under test, or mutate one function/module
  at a time.

## Rollback

An audit changes only files under `docs/`. To back it out, delete the audit
folder. Remediation (Phase 5) is tracked and reverted in its own plan.
