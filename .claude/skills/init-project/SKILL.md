---
name: init-project
description: Runs the one-time setup interview and scaffolding for a project freshly cloned from the stack-agnostic project template, before any app code, stack choice, or folders exist. Use whenever the user has just cloned this template and wants to get started — even if they only say "help me set this up" or "this is a fresh clone, get it going." Also use if CLAUDE.md's Project identity section still shows the placeholder comment, or docs/foundation.md doesn't exist. Interviews the user on identity, shape, stack, and constraints one question at a time, proposes a folder/tooling scaffolding plan for approval, then creates the folders, writes root tooling files, fills in CLAUDE.md, and writes docs/foundation.md. Strictly one-time — do not use for everyday coding-session startup on an already-initialized project, or to sync an existing project with template updates — those are separate, ongoing concerns.
---

# Init project

Use this skill once, right after a repo is cloned from the stack-agnostic project template. Re-running it later is safe — Phase 0 detects work that is already done and offers an update instead of a fresh run — but this skill is a one-time setup tool, not an ongoing tool.

## Role

Act as a senior technical lead running an intake session for a brand-new project. The repo has a `docs/` skeleton and workflow prompts, but no app code, no chosen stack, and no scaffolded folders. Find out what the user is building. Propose a concrete plan. Execute it once they approve it.

## Phase 0: scan

Check three things before you ask anything:

1. Does `CLAUDE.md`'s `## Project identity` section already have real content, not the HTML-comment placeholder? If it does, tell the user this repo looks already initialized. Confirm they want to re-run before you continue.
1. Does `docs/foundation.md` already exist?
1. Note the repo's directory name. It is a reasonable default project name, but ask rather than assume it.

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
- Which of the existing `.github/prompts/*.prompt.md` Config blocks need real values now — `TEST_COMMAND`, `LINT_COMMAND`, `SRC_ROOT`, `ADR_PATH`, and so on. Some, like `DOCS_ROOT`, are already correct as shipped.

Present this as a plan: folder list, one line per file to be created or modified, README contents summarized rather than pasted in full. Ask for approval.

> [!IMPORTANT]
> Gate — plan approval. Wait for the user to reply exactly `PLAN: APPROVED` before you start Phase 3. Fold in any adjustments they ask for first.

## Phase 3: scaffold

Once the user approves the plan:

1. Create each approved folder with its README. Match the depth and tone of this repo's existing docs READMEs.
1. Write the root tooling files from Phase 2.
1. Update the `Config` block in each `.github/prompts/*.prompt.md` file that had a placeholder, with the real values now known.
1. Update the root `README.md`: fill in `## Stack`, `## Quick Start`, and `## Project Structure` with the real content. Delete the `## Getting started` section — its job, pointing here, is done.

## Phase 4: update CLAUDE.md

Fill in every section of `CLAUDE.md` from the interview. Remove the HTML-comment instructions as you go, per the file's own "How to fill this in" note.

- **Project identity** — from the identity and problem answers.
- **Stack** — from the stack answers, as a concrete list, not placeholders.
- **Architecture** — top-level structure from the Phase 2 plan, how the pieces connect, and any pattern being enforced. For example, an adapter pattern for external services, if the constraints answers call for one.
- **Constraints (non-negotiable)** — verbatim from question 14, plus anything the answers structurally imply. For example, "never write to the DB from a read-only integration," if that is the shape of the project.
- **Code style** — from question 15, plus the language's defaults.
- **Scoring/ranking logic** — from question 16, or delete this section if it does not apply.
- **Current state** — `### Done`: "Repo scaffolded from template, foundation.md and CLAUDE.md written." `### In progress`: empty. `### Not started`: the obvious next build steps the interview implies, for example "first data model" or "first endpoint."
- **Open questions** — anything the user answered "not sure" or "you decide" during the interview.
- **Decision log** — one entry per stack or architecture choice made this session, in the file's existing `### ADR-NNN — Short title` format. This is fine even without a formal ADR file in `docs/ADRs/` yet. It is a lightweight log entry, not a requirement to also write a full ADR.
- Footer timestamp and session description.

## Phase 5: write docs/foundation.md

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
*This document is the source of truth for product intent. Architecture and technology decisions live in `docs/ADRs/`; this file is about why, not how.*
```

Keep it honest and specific to what the user actually said. Do not pad it with invented market research or generic startup language. If the interview did not produce enough for a section, say so explicitly — for example, "Success metric: not yet defined — revisit before first release" — rather than inventing content.

## Phase 6: wrap-up

1. Summarize what you created: folder list, files written, and confirmation that `CLAUDE.md` and `foundation.md` are updated.
1. Suggest a commit message: `chore: initialize project from template`.
1. Tell the user this skill has done its job. Running it again on this repo re-checks Phase 0 and offers an update — it does not start over. Point them to their session-start workflow for the next actual coding session, and to their template-sync workflow for ongoing drift checks as the project grows, once those exist.
