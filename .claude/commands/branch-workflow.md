---
description: Interactive menu for starting a new unit of work on a branch, with a context-aware starter checklist per work type.
argument-hint: "[optional: what you're working on]"
---

# Branch Workflow

Read `TEST_COMMAND`, `DOCS_ROOT`, `ADR_PATH` and `SNAPSHOT_PATH` from the `## Session Config` section of `CLAUDE.md`. If that section is missing, say so and ask rather than guessing — a wrong test command turns every checklist below into a false reassurance.

**Role:** Senior Development Lead facilitating feature-branch development.

## Phase 1: Context scan

Before presenting the menu:

1. Run `git status` — verify a clean working tree.
2. Run `git branch` — confirm the current branch.
3. Run `git pull origin main` — ensure up to date.
4. Find the most recent snapshot in `{SNAPSHOT_PATH}` for project context.

If the user passed an argument, treat it as their answer to "what are you working on" and skip straight to inferring the work type — still confirm the type before creating a branch.

## Phase 2: Work type menu

```
🌿 BRANCH WORKFLOW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. 🎯 FEATURE    — New functionality or enhancement
2. 🐛 FIX        — Bug fix or correction
3. 📚 DOCS       — Documentation updates
4. ♻️  REFACTOR  — Code cleanup, no behavior change
5. 🧪 TEST       — Test additions or improvements
6. 🔬 EXPERIMENT — Exploratory or spike work

Current branch: {branch}
Status: {clean/dirty}
Latest commit: {last commit}

Reply with: WORK: <number>
```

## Phase 3: Branch creation and starter checklist

Branch names are snake_case after the prefix.

### 1. FEATURE (`feature/`)

Ask: "What feature are you building?"
Branch: `git checkout -b feature/{snake_case_name}`

```
✅ Branch created: feature/{name}

FEATURE STARTER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
□ Define requirements
□ Identify files to create/modify
□ Plan test coverage

STEPS:
1. Create/modify files
2. Implement core logic
3. Add unit tests
4. Add integration tests if needed
5. Update relevant docs (spec, ADR if applicable)
6. Run: {TEST_COMMAND}
7. Commit: feat: {description}

Describe the feature in detail.
```

### 2. FIX (`fix/`)

Ask: "What bug are you fixing?"
Branch: `git checkout -b fix/{snake_case_name}`

```
✅ Branch created: fix/{name}

BUG FIX STARTER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
□ Reproduce the bug
□ Write failing test first (TDD)
□ Identify root cause
□ Implement fix
□ Verify test passes
□ Check for regressions

Run: {TEST_COMMAND}
Commit: fix: {description}

Describe the bug and any error messages.
```

### 3. DOCS (`docs/`)

Ask: "What documentation are you updating?"
Branch: `git checkout -b docs/{snake_case_name}`

```
✅ Branch created: docs/{name}

DOCS STARTER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TYPES:
1. README updates
2. Technical specs → {DOCS_ROOT}specs/
3. API docs → {DOCS_ROOT}api/
4. Architecture decisions → {ADR_PATH}
5. SOPs / runbooks → {DOCS_ROOT}SOPs/

□ Content is accurate
□ Examples are tested/working
□ Links are valid

Commit: docs: {description}

What are you documenting?
```

An architecture decision gets its own file in `{ADR_PATH}`, a row in that folder's README Index, and a link from `CLAUDE.md`'s Decision log — never the decision's reasoning pasted into `CLAUDE.md` itself.

### 4. REFACTOR (`refactor/`)

Ask: "What are you refactoring?"
Branch: `git checkout -b refactor/{snake_case_name}`

```
✅ Branch created: refactor/{name}

REFACTOR STARTER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PRINCIPLES:
- No behavior changes
- Tests pass throughout
- Atomic, incremental steps

□ Tests passing before starting: {TEST_COMMAND}
□ Tests still passing after each change
□ No new functionality introduced

Commit: refactor: {description}

What are you refactoring and why?
```

### 5. TEST (`test/`)

Ask: "What are you testing?"
Branch: `git checkout -b test/{snake_case_name}`

```
✅ Branch created: test/{name}

TEST STARTER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
□ Identify untested behavior
□ Write tests before fixing gaps
□ Aim for meaningful coverage not % coverage

Run: {TEST_COMMAND}
Commit: test: {description}

What behavior are you testing?
```

### 6. EXPERIMENT (`experiment/`)

Ask: "What are you exploring?"
Branch: `git checkout -b experiment/{snake_case_name}`

```
✅ Branch created: experiment/{name}

EXPERIMENT STARTER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
This branch is a sandbox. Rules are relaxed.
Nothing here merges to main without review.

□ Define what question you're trying to answer
□ Document findings in a note or snapshot
□ Decide: promote to feature branch or discard

What are you exploring?
```
