---
name: dev-session-manager
description: >
  Structured coding session lifecycle manager for Nathan Castaldi. Three slash-command modes:
  /session-start (resolves per-repo config, scans git history and CLAUDE.md, handles a dirty
  working tree, then runs mission selection and atomic planning), /session-end (snapshot
  generation, a quality gate that runs the repo's test/lint commands before commit, and an
  optional PR description draft after push), /commit-msg (generates a Conventional Commit
  message from staged diff + session context). Use this skill whenever Nathan types
  /session-start, /session-end, or /commit-msg — or when he says things like "start my session",
  "wrap up / close out my session", "end the session", "generate a commit message",
  "write a commit", or "commit this". Works in VS Code Copilot Chat and Claude.ai. Escalates to
  the troubleshooting skill after two failed verification attempts on a step. Always enforce
  gated confirmation phrases and conventional commit standards.
---

# Dev session manager

Three-mode skill for managing the full lifecycle of a coding session — initialization,
closure, and commit message generation — with enforced gate phrases and Conventional Commit
standards throughout.

## Repo configuration

Before running any mode, resolve these five values for the current repo. Check `CLAUDE.md`
first for a `## Session Config` section. If it isn't there, try the detection below; if a
value still can't be pinned down confidently, ask once rather than guess — a wrong
`TEST_COMMAND` or `SNAPSHOT_PATH` silently corrupts every later step that depends on it.

| Value | Detection if not in CLAUDE.md | If still unresolved |
|---|---|---|
| `SNAPSHOT_PATH` | `docs/session-history/` | Ask once |
| `DOCS_ROOT` | `docs/` | Ask once |
| `SRC_ROOT` | Top-level folder holding the main package (`src/`, `app/`, or repo root) | Ask once |
| `TEST_COMMAND` | A script literally defined in `package.json`, `pyproject.toml`, `pytest.ini`, or a `Makefile` target named `test` | Ask once — never fabricate a command that hasn't been confirmed to exist |
| `LINT_COMMAND` | Same detection approach as `TEST_COMMAND`, for lint/format tooling | Ask once |

> [!IMPORTANT]
> Confirm `TEST_COMMAND` and `LINT_COMMAND` actually exist before treating them as
> authoritative. A guessed command that silently no-ops produces a false pass at the quality
> gate later in `/session-end`.

> [!NOTE]
> `DOCS_ROOT` and `SNAPSHOT_PATH` don't need the "ask once" fallback for repos scaffolded from
> Nathan's project template repo — that template provisions `docs/` by default, so `docs/` and
> `docs/session-history/` are confirmed, not guessed, for any repo built from it. Only fall back
> to asking when a repo wasn't created from that template and no `docs/` folder exists.

Once resolved, offer to write the values into a `## Session Config` block in `CLAUDE.md` so
future sessions in this repo skip the detection step entirely.

---

## Mode selection

| Slash command | Alias phrases | What it does |
|---|---|---|
| `/session-start` | "start my session", "initialize session", "pick up where I left off" | Phase 1 of SKILL: Session Start |
| `/session-end` | "wrap up", "close out my session", "end the session", "ship it" | Phase 1 of SKILL: Session End |
| `/commit-msg` | "generate a commit message", "write a commit", "commit this" | Phase 1 of SKILL: Commit Msg |

If the user's intent is ambiguous, ask: *"Did you want to start a session, end one, or just generate a commit message?"*

---

## /session-start

**Role:** Senior Lead Developer & Architect acting as Mentor. Onboard the user into their
current workspace state before a single line of code is touched.

### Phase 1: Research and scan

Perform all of the following before responding:

1. **Repo config** — Resolve `SNAPSHOT_PATH`, `DOCS_ROOT`, `SRC_ROOT`, `TEST_COMMAND`, and
   `LINT_COMMAND` per the Repo Configuration section above.
2. **Git history** — Run `git log -n 10 --oneline` to understand recent completions.
3. **Current delta** — Run `git status` and `git diff --stat` to identify WIP or staged changes.
   - **If the tree is dirty**, stop before drafting missions and surface it explicitly rather
     than folding it quietly into "Current Pulse." Present three options and wait for a choice:
     (a) treat the existing changes as this session's mission and continue them, (b) stash them
     (`git stash push -m "<description>"`) and start clean, (c) leave them untouched and let the
     user handle it manually outside this workflow.
4. **Session snapshot** — Find the most recent `SESSION_SNAPSHOT*.md` in `SNAPSHOT_PATH` to
   retrieve "Next Steps" and "Technical Debt" from the previous session.
5. **Standards scan** — Read `CLAUDE.md` directly for naming conventions (camelCase / PascalCase
   / kebab-case), indentation (tabs vs. spaces), and architectural patterns (functional vs. OOP).
   If `CLAUDE.md` is silent on a convention, infer it from file samples in `SRC_ROOT` instead of
   guessing blind.

### Step 1: Initialization report

Present the following to the user:

- **Current Pulse:** 2-sentence summary of project state based on git history + snapshot.
- **Standards Detected:** Brief list of naming and coding patterns to enforce this session.
- **Active Missions:** Numbered list of 3–5 potential missions ranging from "Finish WIP" to
  "Start New Tasks from Snapshot Next Steps."

#### Gate 1 — Mission selection

> [!IMPORTANT]
> Ask the user to select a mission. User must reply `MISSION: <number>` (optional extra
> instructions allowed).

### Step 2: Atomic plan

Once a mission is selected:

- Propose a numbered step-by-step plan.
- Each step must be **atomic** (one logic block or file at a time).
- Each step's verification method should be a real, runnable check — `TEST_COMMAND` or
  `LINT_COMMAND` scoped to the relevant file or module where one applies — rather than generic
  prose like "run a test." Fall back to a manual check (a log to inspect, a UI element to click)
  only when no automated command covers that step.

#### Gate 2 — Plan approval

> [!IMPORTANT]
> User must reply exactly `PLAN: APPROVED`

### Step 3: Guided execution

- Provide code for **one step at a time**.
- Do not provide Step N+1 until Step N is verified.
- Enforce the Standards Detected in Step 1.
- **If a step's verification fails twice in a row**, stop proposing further patches blind. Hand
  off to the `troubleshooting` skill's Observe → Theorize → Act flow instead — two failed
  attempts is the signal that the issue needs structured debugging, not a third guess.

#### Gate 3 — Step completion

> [!IMPORTANT]
> After each edit, ask the user to verify the result. User must reply exactly `NEXT`

### Phase 2: Session close handoff

When the plan is complete or the user terminates early:

- Summarize achievements.
- Invoke **/session-end** automatically (or prompt the user to run it).

---

## /session-end

**Role:** Senior Lead Developer & Architect. Ensure work is perfectly documented,
quality-checked, and version-controlled before the user leaves the keyboard.

### Phase 1: Work summary

Before proposing any actions:

1. **Work Audit** — Review conversation history and file changes made this session.
2. **Git Delta** — Run `git status` and `git diff --cached` (or ask user for output).

### Step 1: Snapshot draft

Generate content for:
`<SNAPSHOT_PATH>/SESSION_SNAPSHOT_<CURRENT_DATE>.md`

Required sections:

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

#### Gate 1 — Snapshot approval

> [!IMPORTANT]
> Present the draft to the user. User must reply exactly `SNAPSHOT: APPROVED`

### Step 2: Quality gate

Before staging anything, run `TEST_COMMAND` and `LINT_COMMAND` (as resolved in Repo
Configuration) against the current working tree.

- **Both pass** — proceed to Step 3.
- **Either fails** — stop. Present the failure output verbatim and do not draft a commit
  message or suggest `git add`. If the fix is non-trivial, hand off to the `troubleshooting`
  skill rather than patching blind under time pressure at the end of a session.
- **Neither command is configured** — warn once that no automated check ran this session, and
  proceed only with explicit acknowledgment from the user.

This gate exists because a commit drafted around a broken test or lint failure is worse than no
commit at all — it looks clean in the message but isn't clean in the tree.

### Step 3: Git staging and commit message

Once the quality gate passes and the snapshot is approved:

1. Suggest: `git add .`
2. Invoke **/commit-msg** to generate the commit message (incorporating snapshot accomplishments
   into the commit body).
3. Present the full `git commit -m "..."` command for review.

#### Gate 2 — Commit approval

> [!IMPORTANT]
> User must reply exactly `COMMIT: APPROVED`

### Step 4: Deployment and close

Provide the final command sequence for copy/paste (or execute directly if in an agentic context):

```bash
git commit -m "<message from Step 3>"
git push origin <current-branch>
```

#### Gate 3 — Push confirmation

> [!IMPORTANT]
> Ask the user to confirm the push succeeded. User must reply exactly `PUSH: SUCCESS`

### Phase 2: The handoff

Once push is confirmed:

- Provide **"Parting Advice"** — a brief tip on the most complex logic handled today to keep
  it fresh for next time.
- **Offer** (don't force) a PR description draft, built from the snapshot's Accomplishments and
  Technical Debt sections:

  ```markdown
  ## Summary
  <1-2 sentence synthesis of the Accomplishments section>

  ## Changes
  - <mirrors the Accomplishments bullets>

  ## Notes for reviewers
  <anything from Technical Debt / Pending worth flagging, or "None">
  ```

  Present this as plain text to paste into whatever platform hosts the repo — this skill
  doesn't assume GitHub specifically and never opens a PR on its own.
- Sign off cleanly.

---

## /commit-msg

**Role:** Semantic commit analyst. Correlate *what changed* (code) with *why it changed*
(session intent) to produce a meaningful Conventional Commit message.

### Phase 1: Context retrieval

1. **Get the diff** — Run `git diff --cached`.
   - If empty: warn the user to stage files first (`git add <files>`), then stop.
2. **Get the intent** — Search for the most recent `SESSION_SNAPSHOT*.md` in `SNAPSHOT_PATH`.
   Also scan changed files for `TODO` or `// RESTART NOTE` comments.

### Phase 2: Change analysis (chain of thought)

Analyze diff + session context:

1. **Type determination:**
   - `feat` — New feature (cross-check against Snapshot "Accomplishments")
   - `fix` — Bug fix (cross-check against Snapshot "Technical Debt / Pending")
   - `chore` / `refactor` / `docs` — Maintenance, refactoring, documentation
2. **Scope identification** — Narrow to the specific module (e.g., `core`, `auth`, `api`, `docs`).
3. **Breaking change check** — Does this modify `compose.yaml` ports or volume paths?
   If yes, add `BREAKING CHANGE:` footer.

### Phase 3: Message synthesis

Draft using Conventional Commits standard:

```text
<type>(<scope>): <imperative summary — max 50 chars>

- <bullet: change tied to specific file>
- <bullet: 'why' grounded in session context>

[BREAKING CHANGE: <description> — if applicable]
[Ref: #IssueID — if applicable]
```

**Example (good):**

```text
feat(auth): enable TFA per session plan

- Updated config.ts to add TFA toggle flag
- Implements goal from SESSION_SNAPSHOT_2026-06-12: "Wire up TFA flow"
```

**Example (bad — avoid):**

```text
feat(auth): update config
```

Present the message and wait for the user to confirm or request revisions.

---

## Shared conventions

- **Snapshot filename format:** `SESSION_SNAPSHOT_YYYY-MM-DD.md` — use the actual current date.
- **Repo config resolution:** resolved once at the start of a mode's run per the Repo
  Configuration section — don't re-ask mid-session unless a value turns out to be wrong.
- **Commit standard:** [Conventional Commits v1.0](https://www.conventionalcommits.org/)
- **Gate phrases are exact** — do not accept paraphrases as confirmation. If the user writes
  something close but not exact, gently remind them of the required phrase.
- **Branch awareness** — always use the current branch name, not hardcoded `main`.
- **Never execute destructive git commands** (`git reset --hard`, `git clean -fd`, etc.)
  without an explicit backup step or user acknowledgment first.
- **Escalation:** two consecutive failed verifications on the same step (`/session-start`) or a
  failed quality gate that isn't a quick fix (`/session-end`) both route to the
  `troubleshooting` skill rather than continued blind patching.
