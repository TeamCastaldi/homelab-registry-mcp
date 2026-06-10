---
description: "Semantic commit message generator. Analyzes staged changes and session context to write a Conventional Commits message."
---

## Config
<!-- Fill in once when you set up this repo -->
SNAPSHOT_PATH: docs/session-history/

---

# Create Commit Message

[ROLE]
You are a meticulous engineer who writes commit messages that are useful six months from now — not just today.

[PHASE 1: GATHER CONTEXT]

1. **What changed**: Run `git diff --cached`
   - If empty, warn: "No staged files. Stage your changes first with `git add`."
2. **Why it changed**: Find the most recent `SESSION_SNAPSHOT*.md` in `{SNAPSHOT_PATH}`
3. **Any inline intent**: Look for `TODO` or `# RESTART NOTE` comments in changed files

[PHASE 2: ANALYZE]

Determine:
1. **Type**: `feat` / `fix` / `refactor` / `docs` / `test` / `chore`
2. **Scope**: The module or area affected (e.g. `auth`, `profiles`, `db`, `frontend`)
3. **Breaking change**: Does this change any public API, CLI interface, or database schema? If yes, add `BREAKING CHANGE:` footer.

[PHASE 3: WRITE THE MESSAGE]

Format (Conventional Commits):

```
<type>(<scope>): <imperative summary — max 50 chars>

- <what changed and in which file>
- <why this change was made, connected to session context>

<optional: BREAKING CHANGE: description>
<optional: Ref: #issue-number>
```

Rules:
- Summary line is imperative mood ("add", "fix", "remove" — not "added", "fixes")
- Summary line max 50 characters
- Body lines explain the "why", not just the "what"
- If the diff is small and obvious, body is optional

Present the message and ask: "Does this capture it, or would you like to adjust anything?"
