---
description: Close out a coding session — write the snapshot, run the test and lint gate, then commit and push behind explicit confirmations.
---

# Session end

**Role:** Senior Lead Developer and Architect. Ensure the work is documented, quality-checked and committed before the user leaves the keyboard.

Read `SNAPSHOT_PATH`, `TEST_COMMAND` and `LINT_COMMAND` from the `## Session Config` section of `CLAUDE.md`. Confirm the two commands actually exist before trusting them — a command that silently no-ops turns the gate below into a rubber stamp.

## Phase 1: Work summary

Before proposing any action:

1. **Work audit** — review the conversation and the file changes made this session.
2. **Git delta** — run `git status` and `git diff --cached`.

## Step 1: Snapshot draft

Draft `{SNAPSHOT_PATH}/SESSION_SNAPSHOT_<YYYY-MM-DD>.md`, using the actual current date:

```markdown
## Session Goals
What we set out to do.

## Accomplishments
- Bulleted list of logic changes, new files, fixed bugs.

## Technical Debt / Pending
What was left unfinished or requires refactoring.

## Next Steps
Clear instructions for the next /session-start.
```

> [!IMPORTANT]
> Gate 1 — snapshot approval. Present the draft. The user must reply exactly `SNAPSHOT: APPROVED`.

## Step 2: Quality gate

Before staging anything, run `{TEST_COMMAND}` and `{LINT_COMMAND}` against the working tree.

- **Both pass** — continue to Step 3.
- **Either fails** — stop. Present the failure output verbatim. Do not draft a commit message and do not suggest `git add`. If the fix is non-trivial, hand off to a troubleshooting workflow rather than patching blind under end-of-session time pressure.
- **Neither is configured** — warn once that no automated check ran this session, and continue only with the user's explicit acknowledgment.

This gate exists because a commit drafted around a failing test is worse than no commit: it reads clean in the message and isn't clean in the tree.

## Step 3: Staging and commit message

Once the gate passes and the snapshot is approved:

1. Suggest `git add .` — or specific paths, if anything in the tree shouldn't be committed.
2. Generate the commit message with `/commit-msg`, folding the snapshot's Accomplishments into the body.
3. Present the full `git commit -m "..."` for review.

> [!IMPORTANT]
> Gate 2 — commit approval. The user must reply exactly `COMMIT: APPROVED`.

## Step 4: Push

```bash
git commit -m "<message from Step 3>"
git push origin <current-branch>
```

> [!IMPORTANT]
> Gate 3 — push confirmation. Ask the user to confirm the push succeeded. They must reply exactly `PUSH: SUCCESS`.

## Phase 2: Handoff

Once the push is confirmed:

- Give **parting advice** — a brief note on the most complex logic handled today, to keep it fresh for next time.
- **Offer** (don't force) a PR description drafted from the snapshot:

  ```markdown
  ## Summary
  <1-2 sentence synthesis of the Accomplishments section>

  ## Changes
  - <mirrors the Accomplishments bullets>

  ## Notes for reviewers
  <anything from Technical Debt / Pending worth flagging, or "None">
  ```

  Present it as plain text to paste wherever the repo is hosted. Never open a PR unprompted.
- Sign off cleanly.

## Conventions

- **Snapshot filename** — `SESSION_SNAPSHOT_YYYY-MM-DD.md`, with the real current date.
- **Gate phrases are exact.** Do not accept a paraphrase as confirmation.
- **Branch awareness** — push the current branch, never a hardcoded `main`.
- **Never run destructive git commands** (`git reset --hard`, `git clean -fd`) without an explicit backup step or the user's acknowledgment.
