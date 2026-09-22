---
name: init-project
description: Runs the one-time setup interview and scaffolding for a project freshly cloned from the stack-agnostic project template, before any app code, stack choice, or folders exist. Use whenever the user has just cloned this template and wants to get started — even if they only say "help me set this up" or "this is a fresh clone, get it going." Also use if CLAUDE.md's Project identity section still shows the placeholder comment, or docs/foundation.md doesn't exist. Interviews the user on identity, shape, stack, and constraints one question at a time, proposes a folder/tooling scaffolding plan for approval, then creates the folders, writes root tooling files, re-points the docs the template ships (CONTRIBUTING.md, SECURITY.md, and the rest) away from describing the template and at the real project, fills in CLAUDE.md, and writes docs/foundation.md. Also use on an already-initialized project when its CONTRIBUTING.md, SECURITY.md, or folder READMEs still describe "this template" rather than the project — that repo was scaffolded before this skill re-pointed inherited docs, and Phase 4 retrofits it on its own without the interview. Otherwise strictly one-time — do not use for everyday coding-session startup, or to sync an existing project with template updates; those are separate, ongoing concerns.
---

# Init project

Use this skill once, right after a repo is cloned from the stack-agnostic project template. Re-running it later is safe — Phase 0 detects work that is already done and offers an update instead of a fresh run — but this skill is a one-time setup tool, not an ongoing tool.

## Role

Act as a senior technical lead running an intake session for a brand-new project. The repo has a `docs/` skeleton and workflow prompts, but no app code, no chosen stack, and no scaffolded folders. Find out what the user is building. Propose a concrete plan. Execute it once they approve it.

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
- **Already initialized, and the sweep found hits** → this project was scaffolded before the skill re-pointed inherited docs, so its folders, CI, and `CLAUDE.md` are fine and only the shipped docs were left describing a different repository. Offer to run **Phase 4 alone**. It needs no interview and no plan gate — read the repo to answer what the interview would have asked, then go. Skip Phases 1, 2, 3, 5, and 6 entirely; do not re-scaffold a working project.
- **Already initialized, sweep clean** → there is nothing here to do. Say so and stop. Ongoing drift is the template-sync workflow's job, not this skill's.

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
- Which values in `CLAUDE.md`'s `## Session Config` table need real settings now — `TEST_COMMAND`, `LINT_COMMAND`, and `SRC_ROOT`. Some, like `DOCS_ROOT` and `ADR_PATH`, are already correct as shipped. This one table is what every command in `.claude/commands/` reads, so it is the only place these values are set.
- Which inherited docs Phase 4 will rewrite, as a plain file list. Read that phase now so the plan you present covers them — the user should approve the docs pass, not discover it. One of those calls needs an answer now: whether this project exposes an API (decides whether `docs/api/` is filled in or deleted).
- Which stack or architecture decisions from this interview become ADR files. Every one does — list their working titles now, so the user sees the `docs/ADRs/*.md` files by name before Phase 4 writes them, rather than discovering the folder filled in afterward.

Present this as a plan: folder list, one line per file to be created or modified, README contents summarized rather than pasted in full. Ask for approval.

> [!IMPORTANT]
> Gate — plan approval. Wait for the user to reply exactly `PLAN: APPROVED` before you start Phase 3. Fold in any adjustments they ask for first.

## Phase 3: scaffold

Once the user approves the plan:

1. Create each approved folder with its README. Match the depth and tone of this repo's existing docs READMEs.
1. Write the root tooling files from Phase 2.
1. Fill in `CLAUDE.md`'s `## Session Config` table with the real values now known, replacing every `{set by init-project ...}` placeholder.
1. Update the root `README.md`: fill in `## Stack`, `## Quick Start`, and `## Project Structure` with the real content. Delete the `## Getting started` section — its job, pointing here, is done.
1. Leave `.template-version` in place, unedited. It records which version of the template this project was scaffolded from, and the `sync-from-template` workflow reads it later to report how far behind the project has fallen and which changelog entries it missed. Deleting it as template residue costs that project its only provenance marker; it is the one inherited file that is *about* the relationship to the template and is meant to stay.
1. Delete the three files that belong to the template rather than to this project:
   - `CHANGELOG.md` — a log of template releases. If the project wants a changelog, it starts empty at its own 0.1.0.
   - `.github/workflows/template-ci.yml` — its first step asserts that `check_scaffolded_project.sh` *fails* on this repo, which stops being true the moment you finish. Leaving it turns the project's CI red.
   - `scripts/simulate_init.sh` — it builds an as-if-initialized fixture from the template, and has nothing to simulate once the real thing exists.

   Keep `.github/workflows/skills-ci.yml` and `scripts/check_scaffolded_project.sh`: the first tests the skills this project keeps, and the second is how anyone later confirms the project still looks properly scaffolded.
1. Work out and present the cloud environment recommendation below, using the install and test commands just written into the manifest and CI workflow.

### Recommend the cloud environment settings

If this project will run in Claude Code on the web, someone has to fill in the "Add cloud environment" dialog — Network access, Environment variables, Setup script — before the first session can do anything. Left to guesswork, that turns into a slow back-and-forth: a setup script that fails because Network access was left at `None`, an API key pasted into Environment variables because nothing said not to, a script that silently resolves the wrong Python and reports success anyway. Everything needed to get this right the first time was just decided in this phase, so hand it to the user now, as three copy-pasteable blocks with a one-line reason each — not as a description they have to translate themselves.

**Network access**

- Default: **Trusted**. It covers the standard package registries (PyPI, npm, crates.io, RubyGems, the Go module proxy) and GitHub, which is what the setup script below needs to actually install anything.
- Recommend **Custom** only if the stack pulls from somewhere Trusted doesn't reach — a private registry, an internal index, a specific Docker Hub image — and name the exact host(s), not "everything."
- Recommend **None** only when there is truly no install step. Say plainly that it will break the setup script the moment a dependency needs fetching.

**Environment variables**

- Default: leave empty. This field is visible to anyone with access to the environment; it's for non-secret config (`NODE_ENV=development`), not credentials — the dialog says so, and this skill doesn't override it.
- Never suggest putting an API key, token, or password here.
- Only propose an entry if the interview surfaced a real, non-secret variable the setup script or session genuinely needs, and say which one and why.

**Setup script**

- Use the exact install command from the manifest and CI workflow this phase just wrote — not a fresh guess at how to install dependencies.
- Make it self-verifying: end it with one command that fails loudly if the install is broken (`python -c "import <pkg>"`, `npm ls`, `go build ./...`), so a bad script errors at session start instead of surfacing later as an unrelated-looking failure.
- Prefer explicit interpreters (`python3 -m pip install …`, not a bare `pip`) — the base image's default `python`/`pip` on `PATH` isn't guaranteed, and picking the wrong one fails silently rather than erroring.
- This script is bash, not a permissions request — there is no Docker-in-Docker or privileged-mode toggle in this dialog. If the project genuinely needs a Docker daemon inside the session (e.g. testcontainers), flag that to the user as a separate, explicit note rather than implying the setup script can grant it.

## Phase 4: re-point the inherited docs

The template ships documentation that describes *the template*. The moment this repo becomes a project, those files are not merely stale — they are false, and nothing in the normal course of work will flush them out. Nobody re-reads `SECURITY.md`. They surface months later in a doc audit, after a contributor has already followed one and been misled.

They are also the cheapest thing in this entire skill to get right, because the answers are all in front of you right now. Do it here, not later.

### The triage rule

Sort every doc in the repo into one of two kinds before you touch anything. This distinction decides the whole phase:

- **Repo-claiming docs** assert something about *this specific repository* — what it is, what it ships, whether it is deployed, what its CI runs, what its folders are called. Every one of these is wrong on day one. Rewrite them.
- **Timeless guides** describe what belongs in a folder and what doesn't. They were written to be true of any project, and they still are. Leave them alone.

Do not freshen a timeless guide just because it looks untouched. An unmodified file is not evidence of a stale one, and churning these buries the real changes in the diff.

### Rewrite these

| File | What it claims as shipped | What it has to become |
|---|---|---|
| `CONTRIBUTING.md` | "contributing to this template"; "No CI gate on the template itself — it ships no app code, so there's nothing to lint or test at this level"; lists application code as *out* of scope | This project's real workflow: the actual test and lint commands a contributor runs before pushing, the CI gate written in Phase 3, and application code as the main thing in scope |
| `SECURITY.md` | "a project template, not a deployed application"; names a `requirements.txt` the project may not have; claims Dependabot watches pip, npm, and Actions | What this project actually is and whether it is deployed; its real manifest file; the exact ecosystems now in `.github/dependabot.yml` |
| `scripts/README.md` | Sends application code to `backend/` | The real source root chosen in Phase 2 — `src/`, `app/`, or whatever it is. `backend/` was a guess the template had no way to make |
| `docs/api/README.md` | Instructs the reader to note the generated-docs URL, then never does | The real URL if the framework serves one — check it rather than assuming, since `/docs` and `/redoc` are FastAPI's, not everyone's. If this project exposes no API, delete the folder |
| `.github/PULL_REQUEST_TEMPLATE.md` | Checklist defers to a `TEST_COMMAND` defined in a prompt file | The real commands, written out |
| `.github/dependabot.yml` | Comment describes a workflow file that scaffolds ecosystem blocks | Nothing, once Phase 3 has added the real blocks — delete the stale comment |
| `.claude/commands/sync-template.md` | Lists audit items generically enough to be true anywhere | Mostly correct as shipped — check that every path and script it names resolves in this project, since it is the workflow that catches drift from here on |

Leave `CODE_OF_CONDUCT.md`, `.claude/README.md`, `.claude/skills/README.md`, and the folder READMEs under `docs/` — `SOPs/`, `plans/`, `specs/`, `session-history/` — untouched. They are timeless guides. (The sweep never reports on `.claude/skills/` at all, so that README's mentions of the template will not surface as hits — leave it anyway: it describes what belongs in a skills folder and how a skill must be laid out to load, which stays true here.) `docs/ADRs/README.md` is the one exception: its guide text (what belongs here, naming convention, status values) is timeless too, but its `## Index` table is not — see "Pointers into empty folders" below.

### Two pointers that ship broken

**References to files that no longer exist.** Workflows move — this template's were `.prompt.md` files before they became commands and skills — and the docs naming them are updated late or not at all. The template's own copies were repaired once, so a fresh clone should be clean here, but a project that synced from an older template, or one whose own workflows have since moved, will not be. Do not assume either way: the sweep below is what tells you. Repoint each stale reference at whatever replaced it, or cut the sentence.

Where a reference is *deliberately* historical — a migration table that has to name the old file to be useful — keep it and mark the line `inherited-docs-ok`, which the sweep skips. `.claude/README.md` carries exactly such a table, under "Workflows that moved". Marking is for a mention you have read and judged correct, never a way to quiet one you have not looked at.

**Pointers into empty folders.** The root README sends a reader to `docs/ADRs/` for architecture decisions. Make sure that pointer resolves: write one real, unique file there — `docs/ADRs/ADR-NNN-short-description.md`, per the naming convention and status values in `docs/ADRs/README.md` — for every stack or architecture decision this session made, not just the single most significant one. Add a row to that README's `## Index` table for each file as you write it. `CLAUDE.md`'s own `## Decision log` (Phase 5) never hosts a decision's content itself; it only links to the files written here, so there is exactly one place the actual reasoning lives. A reader who follows a cross-reference into an empty directory learns nothing and stops trusting every other pointer in the repo — and a decision log split across two competing homes teaches the same distrust.

### Verify before moving on

Run the Phase 0 sweep again:

```bash
bash .claude/skills/init-project/scripts/check_inherited_docs.sh
```

Then run the two checks that resolve claims against reality, which the sweep's text matching cannot:

```bash
bash scripts/check_doc_claims.sh          # ecosystems, manifests and commands the docs name
bash scripts/check_scaffolded_project.sh  # every post-condition these phases promise
```

`check_scaffolded_project.sh` is the contract for this whole skill: placeholders gone, `docs/foundation.md` written, the ADR files indexed and linked, no template-only file left behind. Expect it to fail until Phases 5 and 6 are done — it is the Phase 7 gate, not a Phase 4 one. Run it here anyway to see what remains.

It checks three things: language still describing this repo as a template, links resolving to paths that do not exist, and references to `.prompt.md` files, which no longer exist anywhere in this layout.

Every hit must be either fixed or, if it is a deliberate historical mention — a decision-log entry recording that the repo was scaffolded from a template is the usual one — something you can name out loud as such. When the mention is permanent, mark its line `inherited-docs-ok` so the sweep stays a clean/dirty signal rather than a list of known-good noise that everyone learns to scroll past. Do not report this phase complete on an unexplained hit, and do not describe the sweep as clean while it still exits 1.

The sweep is a backstop, not the standard. It reads text; it cannot tell you that `CONTRIBUTING.md` documents a test command that does not exist, or that `SECURITY.md` lists ecosystems Dependabot is not actually watching. Confirm those against the files Phase 3 wrote.

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

Use this structure:

```markdown
# {Project Name} — Foundation
**Status**: Draft v0.1
**Date**: {today}

---

## The Problem
{From question 3 — expand to real paragraphs, grounded in what the user actually said, not invented detail}

## The Solution
{From questions 2 and 5-8 — what gets built and how it addresses the problem}

## The User
{Who this is for, as specifically as the interview supports}

## What We Are Not Building
{From question 4 — explicit scope boundaries}

## Success Metric
{Ask, if not already covered: what does "this is working" look like in one concrete, observable sentence?}

## Open Questions
{Anything deferred during the interview}

---
*This document is the source of truth for product intent. Architecture and technology decisions live in docs/ADRs/; this file is about why, not how.*
```

That pointer is never dead by the time this file is written — Phase 4 always seeds `docs/ADRs/` with a real file per decision made this session. This file is the first one a new session reads, so a dead cross-reference here would be the most expensive one in the repo.

Keep it honest and specific to what the user actually said. Do not pad it with invented market research or generic startup language. If the interview did not produce enough for a section, say so explicitly — for example, "Success metric: not yet defined — revisit before first release" — rather than inventing content.

This document is a founding brief, and later sessions should treat it as one: a record of intent at a moment in time, not a live status page. Give it the `**Status**` line above so nobody mistakes it for current-state documentation and starts "correcting" it as the project moves.

## Phase 7: wrap-up

1. Summarize what you created: folder list, files written, and confirmation that `CLAUDE.md` and `foundation.md` are updated.
1. List the ADR files this session wrote in `docs/ADRs/`, and confirm that README's `## Index` table and CLAUDE.md's `## Decision log` both name the exact same set of files.
1. Restate the Phase 3 cloud environment recommendation (Network access, Environment variables, Setup script) as the three ready-to-paste blocks, so it's not left buried mid-transcript — this is the thing the user is most likely to need again the moment they open the "Add cloud environment" dialog.
1. List the inherited docs Phase 4 rewrote, separately from the files you created. These are the ones the user is least likely to re-read on their own, so they are the ones worth naming — and if you deleted anything, `docs/api/` most likely, say so plainly rather than leaving them to notice.
1. Run the scaffolding verifier and report it clean:

   ```bash
   bash scripts/check_scaffolded_project.sh
   ```

   It must exit 0 before you call this skill done. Every problem it reports names a promise one of these phases made and did not keep, so fix the cause rather than explaining the output — and never by loosening the check. `bash scripts/check_doc_claims.sh` should be clean too.
1. Report the final state of the verification sweep, including any hit you deliberately left and why.
1. Flag anything you wrote but could not exercise — a CI workflow that has never run, a compose file that has never come up. Scaffolding is written from the interview, not from a working system, and the first person to run it should know which parts are still theoretical.
1. Suggest a commit message: `chore: initialize project from template`.
1. Tell the user this skill has done its job. Running it again re-checks Phase 0: on a repo whose docs are already re-pointed it will say there is nothing to do, and it does not start over. Point them to their session-start workflow for the next actual coding session, and to their template-sync workflow for ongoing drift checks as the project grows, once those exist.

## Why this skill owns the docs pass

Three workflows touch documentation, and the boundaries are worth keeping clean:

- **This skill, once, at birth.** Inherited docs were never true of this project. They were wrong at clone time, and no later workflow is designed to notice, because nothing *changed* to draw attention to them — an audit for drift compares docs against the work done since, and finds nothing to compare here.
- **The template-sync workflow, ongoing.** Structural drift as folders, prompts, and tooling move around after init.
- **The docs-updater workflow, per session.** Doc claims that stopped being true because of work just completed.

A doc that was false from the first commit falls through both of the ongoing checks. That is precisely why it has to be caught here.
