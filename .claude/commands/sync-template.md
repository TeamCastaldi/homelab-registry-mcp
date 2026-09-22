---
description: Audit this repo for drift between its folder structure, READMEs and tooling, then fix what has fallen out of sync.
---

# Sync structure

Read `DOCS_ROOT` and `ADR_PATH` from the `## Session Config` section of `CLAUDE.md`.

**Role:** A meticulous technical project manager doing a consistency audit. Find places where this repo's structure, documentation and tooling have fallen out of sync with each other, and fix them before they mislead the next person who reads them.

Run this after any structural change:

- Adding, removing or renaming a folder
- Adding or updating a command in `.claude/commands/` or a skill in `.claude/skills/`
- Changing tooling defaults (language manifest, `ci.yml`, `dependabot.yml`)
- Updating the language manifest's dependency list
- Any change a README or the root `CLAUDE.md` should reflect

## Phase 1: Structural audit

Several of these have a script that answers them definitively. Run the script rather than reading files and forming an opinion — the scripts exist because eyeballing missed these exact cases before.

1. **Folder vs README** — Does every folder have a README? Does each describe the folder it actually lives in?
2. **Command and skill index** — Does [`.claude/README.md`](../README.md) list every command in `commands/` and skill in `skills/`? Are any listed that no longer exist? Its "Workflows that moved" table deliberately names files that are gone, and those lines are marked `inherited-docs-ok` — that is not drift. Extend the table when a workflow moves; never delete it.
3. **Skill layout** — Does `bash scripts/validate_skills.sh` exit 0?
4. **Documentation claims** — Does `bash scripts/check_doc_claims.sh` exit 0? It catches a doc naming an ecosystem, manifest or script that does not exist here.
5. **Stale references** — Does `bash .claude/skills/init-project/scripts/check_inherited_docs.sh` exit 0?
6. **Root README** — Does `## Project Structure` match the actual folder layout?
7. **CLAUDE.md** — Does `## Stack` reflect the real dependencies in the language manifest? Does `## Session Config` hold real values rather than placeholders? Does `## Current state` need updating?
8. **ADRs** — Does `{ADR_PATH}README.md`'s Index list exactly the files in that folder, with current Status values? Does `CLAUDE.md`'s Decision log link the same set, with no decision's reasoning duplicated into `CLAUDE.md` itself?
9. **Language manifest** — Does it match the actual project name?
10. **CI workflow** — Does it reference the real lint and test commands and dependency install paths?
11. **Scaffolding invariants** — Does `bash scripts/check_scaffolded_project.sh` exit 0? On a repo that has been through `init-project`, it should.
12. **CONTRIBUTING.md** — Does it reference any command or path that has changed?

## Phase 2: Report

```
SYNC AUDIT REPORT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ In sync:     [list items that are consistent]
⚠️  Drift found: [list each discrepancy with file and line]
➕ Missing:     [list anything that should exist but doesn't]
```

If no drift is found, confirm all clear and note that this is a good moment to commit.

## Phase 3: Fix

For each drift item:

1. Show the specific change needed — file path plus what to update.
2. Ask for confirmation before changing anything: `SYNC: FIX <item>` or `SYNC: FIX ALL`.

Gate phrases are exact. Do not accept a paraphrase as confirmation.

## Phase 4: Commit

Once fixes are applied, suggest (don't run) a commit:

```text
chore: sync docs and structure after [what changed]
```
