---
name: init-project
description: >-
  Runs the one-time setup for a project freshly cloned from this stack-agnostic template:
  interviews the user one question at a time on identity, shape, stack and constraints,
  proposes a scaffolding plan for approval, then creates folders and root tooling, re-points
  the inherited docs (CONTRIBUTING.md, SECURITY.md and the rest) at the real project, and
  fills in CLAUDE.md and docs/foundation.md. Use when the user has just cloned the template
  and wants to get started, even if they only say "help me set this up"; when CLAUDE.md's
  Project identity is still a placeholder or docs/foundation.md is missing; or when an
  already-initialized project's CONTRIBUTING.md, SECURITY.md or folder READMEs still
  describe "this template". Not for everyday session startup or for pulling later template
  updates.
---

# Init project

Use this skill once, right after a repo is cloned from the stack-agnostic project template. Re-running it later is safe — Phase 0 detects work that is already done and offers an update instead of a fresh run — but this skill is a one-time setup tool, not an ongoing tool.

## Progress

Copy this checklist into your reply and tick it off as you go. It sits at the top of this file so it stays within what Claude Code keeps of a skill after compaction, which a long interview can reach.

```
- [ ] Phase 0: scan, and settle which job this is
- [ ] Phase 1: interview, one question per message
- [ ] Phase 2: plan, then wait for PLAN: APPROVED
- [ ] Phase 3: scaffold, then the cloud settings (references/cloud-environment.md)
- [ ] Phase 4: re-point inherited docs (references/inherited-docs.md)
- [ ] Phase 5: fill in CLAUDE.md
- [ ] Phase 6: write docs/foundation.md (references/foundation-template.md)
- [ ] Phase 7: wrap up — bash scripts/check_scaffolded_project.sh must exit 0 before this skill is done
```

For the Phase 0 retrofit of an already-initialized project, the list is Phase 0, Phase 4, and that phase's verification.

## Role

Act as a senior technical lead running an intake session for a brand-new project. The repo has a `docs/` skeleton and the template's commands and skills, but no app code, no chosen stack, and no scaffolded folders. Find out what the user is building. Propose a concrete plan. Execute it once they approve it.

## Phase 0: scan

Check four things before you ask anything:

1. Does `CLAUDE.md`'s `## Project identity` section already have real content, not the HTML-comment placeholder? If it does, this repo is already initialized — say so, and do not start a fresh run without the triage below settling what is actually needed.
1. Does `docs/foundation.md` already exist?
1. Note the repo's directory name. It is a reasonable default project name, but ask rather than assume it.
1. Do the inherited docs still describe the template? Run [`scripts/check_inherited_docs.sh`](scripts/check_inherited_docs.sh), from the repo root:

   ```bash
   bash .claude/skills/init-project/scripts/check_inherited_docs.sh
   ```

   It exits 0 when clean and 1 on any hit. On a fresh clone it reports hits in every category — that is expected, and Phase 4 clears them.

Checks 1 and 4 together decide which job you are doing. Settle this before asking anything else:

- **Not yet initialized** → a normal full run. Continue to Phase 1.
- **Already initialized, and the sweep found hits** → this project was scaffolded before the skill re-pointed inherited docs, so its folders, CI, and `CLAUDE.md` are fine and only the shipped docs were left describing a different repository. Offer to run **Phase 4 alone**, from [`references/inherited-docs.md`](references/inherited-docs.md). It needs no interview and no plan gate — read the repo to answer what the interview would have asked, then go. Skip Phases 1, 2, 3, 5, and 6 entirely; do not re-scaffold a working project. Phase 3 is also where `docs/template/` is deleted and `SNAPSHOT_PATH` is reset, so this path never touches a project's own session history in `docs/session-history/`.
- **Already initialized, sweep clean** → there is nothing here to do. Say so and stop. Ongoing drift is `/sync-template`'s job, not this skill's.

Only if the user asks for a genuine re-run of the whole thing — rare, and usually a sign the project changed shape enough to warrant restarting — confirm that is what they mean before continuing to Phase 1.

## Phase 1: requirements interview

Ask one question at a time. Never ask more than one question in a single message. Never preview the next question. Post the question, then stop and wait for the reply.

This matters: a list invites the user to skim it and give shallow answers to all of it at once. One question at a time makes them actually think about the question in front of them. Do not batch questions, even if it feels slower.

The four groupings below organize your own thinking. Do not expose them to the user as a heading or a progress counter ("question 3 of 16") unless they ask where things stand.

If the user says "not sure" or "you decide" on any question, make a reasonable call. Note it as an open question for `docs/foundation.md` instead of blocking on it.

### Identity and problem

1. Project name (public-facing, if different from the repo name)?
2. In one or two sentences: what does this do, and who is it for?
3. What problem is it solving, and why does that problem matter? This is the seed of `docs/foundation.md` — the more real detail here, the better that document will be.
4. What does this project explicitly not do? Scope boundaries save more time later than scope definitions do.

### Shape

5. What kind of thing is this: an end-user product (web or mobile app), an internal tool, a CLI, a library or package, an API/MCP server with no UI, or an integration/plugin against another platform?
6. Does it need a persistent database? If yes, what kind — relational/PostgreSQL, document/Mongo, SQLite, or none yet/undecided?
7. Does it need a frontend or UI at all? If yes, what kind — web SPA, server-rendered, CLI-only, or none?
8. What other services does it talk to? Consider external APIs, queues, caches, auth providers, or other systems in the user's homelab or accounts.

### Stack

9. Primary language(s) and version?
10. Backend/application framework, if any? Answer "none" for a library or CLI.
11. Frontend framework, if applicable?
12. Test runner and linter/formatter of choice? Offer a sensible default per language if the user has no preference — for example, pytest plus ruff for Python, or vitest/jest plus eslint for TS/JS. Do not assume FastAPI, React, or Postgres; that was the old template default, not a rule.
13. Deployment target: Docker/homelab, a cloud provider, a published package, a sideloaded plugin, or something else?

### Constraints and conventions

14. Any non-negotiable constraints you must always respect in this repo? Consider security or compliance boundaries, things never to build, data never to touch, and out-of-scope features that adjacent projects tend to scope-creep into.
15. Naming or style conventions beyond the language's defaults, if the user has strong preferences?
16. Anything scoring, ranking, or weighting-related in the domain logic? Skip this question if it does not apply.

Skip a question outright if an earlier answer already made it moot — for example, skip 7 and 11 if question 5 established this is a CLI. Do not ask a moot question just to complete the list.

## Phase 2: scaffolding plan

Work out, from the answers:

- Which top-level folders this project actually needs. Do not scaffold `frontend/` for a CLI. Do not scaffold `db/` for a project with no persistence layer. Common candidates: `backend/` (or `src/`), `frontend/`, `db/`, `tests/`. Add others the stack calls for — a plugin's `server/` plus build pipeline, or an MCP server's tool-module layout.
- For each folder: a short structure sketch and what its README should say, written for the actual chosen stack, not generic boilerplate. Model the tone and depth on the existing `docs/*/README.md` files already in this repo — What belongs here, What doesn't, conventions — but for code folders instead of docs folders.
- Root-level tooling to add: a manifest file appropriate to the language (`pyproject.toml`, `package.json`, `go.mod`, and so on), a CI workflow (`.github/workflows/ci.yml`) that runs the chosen lint and test commands, a `.env.example` if the stack has configurable env vars, and a `dependabot.yml` block per package ecosystem introduced. Append to the existing GitHub Actions block — do not replace it.
- `.github/workflows/skills-ci.yml` and `requirements-dev.txt` already exist and are not the project's CI or its dependencies. They lint and test the scripts under `.claude/skills/`, which this project keeps, so both stay valid here and should be left alone — write the project's own checks as a separate `ci.yml` and its own dependencies into the language manifest. Only if the project strips `.claude/skills/` entirely do these two go with it. If the project also picks Python, keep its runtime dependencies in the manifest rather than merging them into `requirements-dev.txt`: that file is pinned to hold CI's linter ruleset steady, which is a different job from resolving an application's dependency tree.
- Which values in `CLAUDE.md`'s `## Session Config` table need real settings now — `TEST_COMMAND`, `LINT_COMMAND`, and `SRC_ROOT`. Some, like `DOCS_ROOT` and `ADR_PATH`, are already correct as shipped. `SNAPSHOT_PATH` is not: the template points it at `docs/template/`, its own records folder, which Phase 3 deletes, so it must be reset to `docs/session-history/`. This one table is what every command in `.claude/commands/` reads, so it is the only place these values are set.
- Which inherited docs Phase 4 will rewrite, as a plain file list. Read [`references/inherited-docs.md`](references/inherited-docs.md) now so the plan you present covers them — the user should approve the docs pass, not discover it. One of those calls needs an answer now: whether this project exposes an API (decides whether `docs/api/` is filled in or deleted).
- Which stack or architecture decisions from this interview become ADR files. Every one does — list their working titles now, so the user sees the `docs/ADRs/*.md` files by name before Phase 4 writes them, rather than discovering the folder filled in afterward.

Present this as a plan: folder list, one line per file to be created or modified, README contents summarized rather than pasted in full. Ask for approval.

> [!IMPORTANT]
> Gate — plan approval. Wait for the user to reply exactly `PLAN: APPROVED` before you start Phase 3. Fold in any adjustments they ask for first.

## Phase 3: scaffold

Once the user approves the plan:

1. Create each approved folder with its README. Match the depth and tone of this repo's existing docs READMEs.
1. Write the root tooling files from Phase 2.
1. Fill in `CLAUDE.md`'s `## Session Config` table with the real values now known, replacing every `{set by init-project ...}` placeholder. Also set `SNAPSHOT_PATH` to `docs/session-history/`: the template ships it pointing at `docs/template/`, which is deleted below, and a project's `/session-end` should write to its own folder.
1. Update the root `README.md`: fill in `## Stack`, `## Quick Start`, and `## Project Structure` with the real content. Delete the `## Getting started` section — its job, pointing here, is done.
1. Leave `.template-version` in place, unedited. It records which version of the template this project was scaffolded from, and the `sync-from-template` workflow reads it later to report how far behind the project has fallen and which changelog entries it missed. Deleting it as template residue costs that project its only provenance marker; it is the one inherited file that is *about* the relationship to the template and is meant to stay.
1. Delete the three files and one folder that belong to the template rather than to this project:
   - `CHANGELOG.md` — a log of template releases. If the project wants a changelog, it starts empty at its own 0.1.0.
   - `.github/workflows/template-ci.yml` — its first step asserts that `check_scaffolded_project.sh` *fails* on this repo, which stops being true the moment you finish. Leaving it turns the project's CI red.
   - `scripts/simulate_init.sh` — it builds an as-if-initialized fixture from the template, and has nothing to simulate once the real thing exists.
   - `docs/template/` — the template's own session snapshots and reviews. Delete the whole folder rather than a list of files: the template adds a snapshot every session, so no list of names could stay current. This is safe only because this is a first scaffold, where every file in it is the template's. Never delete `docs/session-history/` snapshots: once the project exists, those are its own.

   Keep `.github/workflows/skills-ci.yml` and `scripts/check_scaffolded_project.sh`: the first tests the skills this project keeps, and the second is how anyone later confirms the project still looks properly scaffolded. Keep the issue tracking as shipped, too — `.github/labels.yml`, `.github/ISSUE_TEMPLATE/`, `.github/workflows/label-sync.yml`, `scripts/sync_labels.sh`, `scripts/check_issues.sh`. It needs no project-specific edits.
1. Work out and present the cloud environment recommendation in [`references/cloud-environment.md`](references/cloud-environment.md), using the install and test commands just written into the manifest and CI workflow.

## Phase 4: re-point the inherited docs

Read [`references/inherited-docs.md`](references/inherited-docs.md) in full before touching any doc. It holds the triage rule (which docs to rewrite and which to leave), the table of files and what each must become, the two pointers that ship broken, and the verification that closes this phase. Do not report this phase complete until that file's verification is done.

## Phase 5: update CLAUDE.md

Fill in every section of `CLAUDE.md` from the interview. Remove the HTML-comment instructions as you go, per the file's own "How to fill this in" note.

- **Project identity** — from the identity and problem answers.
- **Stack** — from the stack answers, as a concrete list, not placeholders.
- **Architecture** — top-level structure from the Phase 2 plan, how the pieces connect, and any pattern being enforced. For example, an adapter pattern for external services, if the constraints answers call for one.
- **Constraints (non-negotiable)** — verbatim from question 14, plus anything the answers structurally imply. For example, "never write to the DB from a read-only integration," if that is the shape of the project.
- **Code style** — from question 15, plus the language's defaults.
- **Scoring/ranking logic** — from question 16, or delete this section if it does not apply.
- **Current state** — `### Done`: "Repo scaffolded from template, foundation.md and CLAUDE.md written." `### In progress`: empty. `### Not started`: the obvious next build steps the interview implies, for example "first data model" or "first endpoint."
- **Open questions** — anything the user answered "not sure" or "you decide" during the interview.
- **Decision log** — this section only ever links out: one line per ADR file Phase 4 wrote, `- [ADR-NNN: Short title](docs/ADRs/ADR-NNN-short-title.md) — one-line summary`. Never restate a decision's reasoning here — that content lives once, in the ADR file — and make sure this list and the ADRs README's `## Index` table name the exact same set of files.
- Footer timestamp and session description.

## Phase 6: write docs/foundation.md

Write a founding-brief document at `docs/foundation.md`. This is the project's north star — the document a new session, human or LLM, reads first to understand why the project exists, not just what it is.

Use the structure in [`references/foundation-template.md`](references/foundation-template.md).

The template's closing pointer to `docs/ADRs/` is never dead by the time this file is written — Phase 4 always seeds `docs/ADRs/` with a real file per decision made this session. This file is the first one a new session reads, so a dead cross-reference here would be the most expensive one in the repo.

Keep it honest and specific to what the user actually said. Do not pad it with invented market research or generic startup language. If the interview did not produce enough for a section, say so explicitly — for example, "Success metric: not yet defined — revisit before first release" — rather than inventing content.

This document is a founding brief, and later sessions should treat it as one: a record of intent at a moment in time, not a live status page. Give it the template's `**Status**` line so nobody mistakes it for current-state documentation and starts "correcting" it as the project moves.

## Phase 7: wrap-up

1. Summarize what you created: folder list, files written, and confirmation that `CLAUDE.md` and `foundation.md` are updated.
1. List the ADR files this session wrote in `docs/ADRs/`, and confirm that README's `## Index` table and CLAUDE.md's `## Decision log` both name the exact same set of files.
1. Restate the Phase 3 cloud environment recommendation (Network access, Environment variables, Setup script; see [`references/cloud-environment.md`](references/cloud-environment.md)) as the three ready-to-paste blocks, so it's not left buried mid-transcript — this is the thing the user is most likely to need again the moment they open the "Add cloud environment" dialog.
1. Make the issue tracker live: run the `issue-tracker` skill's "Labels ready" check (its section 2) and, if labels are missing, offer to create them now. With no GitHub remote yet, say the "Sync labels" workflow creates them on the first push to `main`. Then offer `/roadmap seed` to file this interview's goals as issues.
1. List the inherited docs Phase 4 rewrote, separately from the files you created. These are the ones the user is least likely to re-read on their own, so they are the ones worth naming — and if you deleted anything, `docs/api/` most likely, say so plainly rather than leaving them to notice.
1. Run the scaffolding verifier and report it clean:

   ```bash
   bash scripts/check_scaffolded_project.sh
   ```

   It must exit 0 before you call this skill done. Every problem it reports names a promise one of these phases made and did not keep, so fix the cause rather than explaining the output — and never by loosening the check. `bash scripts/check_doc_claims.sh` should be clean too.
1. Report the final state of the verification sweep, including any hit you deliberately left and why.
1. Flag anything you wrote but could not exercise — a CI workflow that has never run, a compose file that has never come up. Scaffolding is written from the interview, not from a working system, and the first person to run it should know which parts are still theoretical.
1. Suggest a commit message: `chore: initialize project from template`.
1. Tell the user this skill has done its job. Running it again re-checks Phase 0: on a repo whose docs are already re-pointed it will say there is nothing to do, and it does not start over. Point them to `/session-start` for the next actual coding session, and to `/sync-template` for ongoing drift checks as the project grows.
