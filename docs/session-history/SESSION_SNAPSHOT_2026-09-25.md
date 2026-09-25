## Session Goals

Run the "Test Suite Evaluator" mission: audit every test file as a strict QA
reviewer. For each file, name the logic its tests guard, flag tautologies,
mock-only assertions and redundancy, and give a KEEP / REWRITE / DELETE verdict
as one JSON object. Then save the findings as a plan and update the living docs.
No application code was to change.

## Accomplishments

**Audit complete** (all 40 files, 845 tests):
- Every suspected flaw was proven, not just read. About 160 mutation probes ran
  in a throwaway worktree, escalating to the full suite whenever the targeted
  tests survived. Each file also got at least one labelled control probe; all
  46 were caught, so the tests that pass do bite.
- 20 files KEEP, 20 REWRITE, none redundant as a whole. Only 13 tests (1.5%) are
  dead weight. The suite isn't bloated; it under-asserts.
- Four patterns explain most full-suite survivors:
  - fakes more forgiving than the real service
  - `"error" in result` checks satisfied by something else
  - read tools tested only when empty
  - settings-to-code wiring left unasserted
- Among the breakages the whole suite misses:
  - A Dockhand or Authentik client sending POST, which breaks the read-only
    invariant.
  - A dropped `superuser_full_list`.
  - A wrong event-retention cutoff, or a skipped startup purge.
  - Normalization PRs opened under the security label.
  - `find_open` ignoring finding type.
  - The webhook swapping tags, or dispatching every alert as a CVE.
  - `close_pr` never closing.
  - No Gitea auth header.
  - `ensure_unlocked` leaving its temp key file behind.
  - A skipped adoption-draft purge.

**Deliverables:**
- `docs/plans/2026-09-test-suite-audit.md` contains:
  - the method and headline findings
  - the verdict table for all 40 files
  - a five-tier remediation plan: strict fakes, event retention, proposal
    routing and secret paths; hollow-test rewrites; the 13 deletions; wiring
    and coverage gaps; the `detect_format` bug
  - the survivor table (Appendix A)
  - the probe runner (Appendix B)
- `docs/plans/2026-09-test-suite-audit-verdicts.jsonl`: the 40 per-file verdicts
  in the mission's JSON schema, schema-validated.
- Doc updates:
  - README indexes the plan.
  - CLAUDE.md has a Current Status entry. Its `dspy/signatures.py` comment now
    lists all 9 signatures, closing the gap flagged in the last snapshot. Its
    Testing section no longer claims an in-memory SQLite fixture (the shared
    `settings` fixture uses a file under `tmp_path`).
  - `tests/README.md` points at the fakes that break its own "no more
    forgiving" rule.
- `uv.lock` self-version synced to 1.10.0 (8e245ab, already pushed).
- Gate: 845 passed; `ruff check` and `ruff format --check` clean.

## Technical Debt / Pending

- **Unfixed application defect.** `gitcrypt.detect_format` tests
  `path.suffix == ".env"`, but `Path(".env").suffix` is empty. A file named
  `.env` is therefore parsed only when the uppercase-`KEY=` heuristic matches,
  so `secrets_decrypt` returns a `.env` with lowercase keys as raw text. The
  suffix-branch test hides this because it passes via the heuristic. Tier 5 of
  the plan has the fix: also match `path.name == ".env"`.
- **The remediation plan hasn't been started.** Tier 1 closes the survivors that
  matter most: the read-only invariant, audit-log retention, security-label
  routing and the secret paths.
- **Two webhook dispatch tests make a real outbound call** to
  `https://git.test`. They pass because the call fails, which breaks the suite's
  hermetic rule.
- This session didn't touch the items pending in
  `SESSION_SNAPSHOT_2026-09-22-3.md`.

## Next Steps

- Fix `gitcrypt.detect_format` first: it's small and real. Replace its suffix
  test with one using lowercase keys, so the test fails without the fix.
- Then work through Tier 1 of `docs/plans/2026-09-test-suite-audit.md` in order.
  For each item, run its Appendix A mutation with the Appendix B runner before
  and after adding the test: it should survive before and be killed after.
- The 13 Tier 3 deletions can ride along with any PR that touches those files.
