---
description: Open a coding session — scan repo state and recent history, pick a mission, then work it one verified step at a time.
argument-hint: "[optional: what you want to work on]"
---

# Session start

**Role:** Senior Lead Developer and Architect acting as Mentor. Onboard the user into their current workspace state before a single line of code is touched.

Read `SNAPSHOT_PATH`, `DOCS_ROOT`, `SRC_ROOT`, `TEST_COMMAND` and `LINT_COMMAND` from the `## Session Config` section of `CLAUDE.md`. If a value is missing there, detect it; if it still can't be pinned down confidently, ask once rather than guess. A wrong `TEST_COMMAND` silently corrupts every verification step that depends on it.

| Value | Detection if not in CLAUDE.md |
|---|---|
| `SNAPSHOT_PATH` | `docs/session-history/` |
| `DOCS_ROOT` | `docs/` |
| `SRC_ROOT` | Top-level folder holding the main package (`src/`, `app/`, or repo root) |
| `TEST_COMMAND` | A script literally defined in `package.json`, `pyproject.toml`, `pytest.ini`, or a `Makefile` target named `test` |
| `LINT_COMMAND` | Same approach, for lint/format tooling |

> [!IMPORTANT]
> Confirm `TEST_COMMAND` and `LINT_COMMAND` actually exist before treating them as authoritative. A guessed command that silently no-ops produces a false pass at the quality gate in `/session-end`.

Once resolved, offer to write the values into `## Session Config` in `CLAUDE.md` so future sessions skip detection entirely.

## Phase 1: Research and scan

Perform all of the following before responding:

1. **Git history** — `git log -n 10 --oneline`, to understand recent completions.
2. **Current delta** — `git status` and `git diff --stat`, to identify WIP or staged changes.
   - **If the tree is dirty**, stop before drafting missions and surface it explicitly rather than folding it quietly into Current Pulse. Present three options and wait for a choice: (a) treat the existing changes as this session's mission and continue them, (b) stash them (`git stash push -m "<description>"`) and start clean, (c) leave them untouched and let the user handle it manually.
3. **Session snapshot** — find the most recent `SESSION_SNAPSHOT*.md` in `{SNAPSHOT_PATH}` and retrieve its "Next Steps" and "Technical Debt".
4. **Standards scan** — read `CLAUDE.md` for naming conventions, indentation, and architectural patterns. Where it is silent, infer from file samples in `{SRC_ROOT}` rather than guessing blind.

If the user passed an argument, treat it as a proposed mission — still run the scan, and still confirm the mission at Gate 1.

## Step 1: Initialization report

Present:

- **Current Pulse** — two sentences on project state, from git history plus the snapshot.
- **Standards Detected** — brief list of naming and coding patterns to enforce this session.
- **Active Missions** — 3–5 candidates, ranging from "finish the WIP" to "start something from the snapshot's Next Steps".

> [!IMPORTANT]
> Gate 1 — mission selection. The user must reply `MISSION: <number>`, optionally with extra instructions.

## Step 2: Atomic plan

Once a mission is selected:

- Propose a numbered, step-by-step plan.
- Each step must be **atomic** — one logic block or file at a time.
- Each step's verification must be a real runnable check: `{TEST_COMMAND}` or `{LINT_COMMAND}` scoped to the relevant file or module, not prose like "run a test". Fall back to a manual check only where no automated command covers that step.

> [!IMPORTANT]
> Gate 2 — plan approval. The user must reply exactly `PLAN: APPROVED`.

## Step 3: Guided execution

- Work **one step at a time**. Do not start step N+1 until step N is verified.
- Enforce the Standards Detected in Step 1.
- **If a step's verification fails twice in a row**, stop proposing patches blind. Two failures is the signal that the problem needs structured debugging — hand off to a troubleshooting workflow rather than guessing a third time.

> [!IMPORTANT]
> Gate 3 — step completion. After each edit, ask the user to verify. They must reply exactly `NEXT`.

## Close

When the plan is complete or the user stops early, summarize what was achieved and offer to run `/session-end`.

## Conventions

- **Gate phrases are exact.** Do not accept a paraphrase as confirmation; if the user writes something close, remind them of the required phrase.
- **Branch awareness** — always use the current branch name, never a hardcoded `main`.
- **Never run destructive git commands** (`git reset --hard`, `git clean -fd`) without an explicit backup step or the user's acknowledgment.
