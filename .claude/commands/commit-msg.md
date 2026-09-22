---
description: Generate a Conventional Commit message from the staged diff plus session context.
---

# Commit message

**Role:** Semantic commit analyst. Correlate *what changed* (the diff) with *why it changed* (session intent) to produce a commit message that means something six months from now.

Read `SNAPSHOT_PATH` from the `## Session Config` section of `CLAUDE.md`.

## Phase 1: Context retrieval

1. **Get the diff** — run `git diff --cached`. If it is empty, tell the user to stage files first (`git add <files>`) and stop; there is nothing to describe.
2. **Get the intent** — find the most recent `SESSION_SNAPSHOT*.md` in `{SNAPSHOT_PATH}`. Also scan the changed files for `TODO` or `RESTART NOTE` comments.

## Phase 2: Change analysis

Work through the diff against the session context:

1. **Type** —
   - `feat` — new functionality (cross-check the snapshot's Accomplishments)
   - `fix` — bug fix (cross-check the snapshot's Technical Debt / Pending)
   - `refactor`, `docs`, `test`, `chore` — everything else
2. **Scope** — narrow to the specific module (`core`, `auth`, `api`, `docs`).
3. **Breaking change** — does this move or remove something its consumers depend on: a renamed path, a changed interface, a removed file another repo expects? If so, add a `BREAKING CHANGE:` footer.

## Phase 3: Synthesis

Draft to the [Conventional Commits v1.0](https://www.conventionalcommits.org/) standard:

```text
<type>(<scope>): <imperative summary — max 50 chars>

- <bullet: change tied to a specific file>
- <bullet: the 'why', grounded in session context>

[BREAKING CHANGE: <description> — if applicable]
[Ref: #IssueID — if applicable]
```

**Good:**

```text
feat(auth): enable TFA per session plan

- Updated config.ts to add TFA toggle flag
- Implements goal from SESSION_SNAPSHOT_2026-06-12: "Wire up TFA flow"
```

**Bad — says nothing the diff doesn't already say:**

```text
feat(auth): update config
```

Present the message and wait for the user to confirm or ask for revisions. Do not run `git commit` unless they ask.
