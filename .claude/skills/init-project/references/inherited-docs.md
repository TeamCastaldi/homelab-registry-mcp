# Phase 4: re-point the inherited docs

Read in full from `init-project`'s Phase 4, and on its own for the Phase 0 retrofit of an already-initialized project.

The template ships documentation that describes *the template*. The moment this repo becomes a project, those files are not merely stale — they are false, and nothing in the normal course of work will flush them out. Nobody re-reads `SECURITY.md`. They surface months later in a doc audit, after a contributor has already followed one and been misled.

They are also the cheapest thing in this entire skill to get right, because the answers are all in front of you right now. Do it here, not later.

## The triage rule

Sort every doc in the repo into one of two kinds before you touch anything. This distinction decides the whole phase:

- **Repo-claiming docs** assert something about *this specific repository* — what it is, what it ships, whether it is deployed, what its CI runs, what its folders are called. Every one of these is wrong on day one. Rewrite them.
- **Timeless guides** describe what belongs in a folder and what doesn't. They were written to be true of any project, and they still are. Leave them alone.

Do not freshen a timeless guide just because it looks untouched. An unmodified file is not evidence of a stale one, and churning these buries the real changes in the diff.

## Rewrite these

| File | What it claims as shipped | What it has to become |
|---|---|---|
| `CONTRIBUTING.md` | "contributing to this template"; "No CI gate on the template itself — it ships no app code, so there's nothing to lint or test at this level"; lists application code as *out* of scope | This project's real workflow: the actual test and lint commands a contributor runs before pushing, the CI gate written in Phase 3, and application code as the main thing in scope. Keep the line saying planned work lives in GitHub Issues, and its link to `docs/SOPs/SOP-issue-tracking.md` |
| `SECURITY.md` | "a project template, not a deployed application"; names a `requirements.txt` the project may not have; claims Dependabot watches pip, npm, and Actions | What this project actually is and whether it is deployed; its real manifest file; the exact ecosystems now in `.github/dependabot.yml` |
| `scripts/README.md` | Sends application code to `backend/` | The real source root chosen in Phase 2 — `src/`, `app/`, or whatever it is. `backend/` was a guess the template had no way to make |
| `docs/api/README.md` | Instructs the reader to note the generated-docs URL, then never does | The real URL if the framework serves one — check it rather than assuming, since `/docs` and `/redoc` are FastAPI's, not everyone's. If this project exposes no API, delete the folder |
| `.github/PULL_REQUEST_TEMPLATE.md` | Checklist defers to a `TEST_COMMAND` defined in a prompt file | The real commands, written out |
| `.github/dependabot.yml` | Comment describes a workflow file that scaffolds ecosystem blocks | Nothing, once Phase 3 has added the real blocks — delete the stale comment |
| `.claude/commands/sync-template.md` | Lists audit items generically enough to be true anywhere | Mostly correct as shipped — check that every path and script it names resolves in this project, since it is the workflow that catches drift from here on |

Leave `CODE_OF_CONDUCT.md`, `.claude/README.md`, `.claude/skills/README.md`, the folder READMEs under `docs/` — `SOPs/`, `plans/`, `specs/`, `session-history/` — and `docs/SOPs/SOP-issue-tracking.md` untouched. They are timeless guides. (The sweep never reports on `.claude/skills/` at all, so that README's mentions of the template will not surface as hits — leave it anyway: it describes what belongs in a skills folder and how a skill must be laid out to load, which stays true here.) `docs/ADRs/README.md` is the one exception: its guide text (what belongs here, naming convention, status values) is timeless too, but its `## Index` table is not — see "Pointers into empty folders" below.

## Two pointers that ship broken

**References to files that no longer exist.** Workflows move — this template's were `.prompt.md` files before they became commands and skills — and the docs naming them are updated late or not at all. The template's own copies were repaired once, so a fresh clone should be clean here, but a project that synced from an older template, or one whose own workflows have since moved, will not be. Do not assume either way: the sweep below is what tells you. Repoint each stale reference at whatever replaced it, or cut the sentence.

Where a reference is *deliberately* historical — a migration table that has to name the old file to be useful — keep it and mark the line `inherited-docs-ok`, which the sweep skips. `.claude/README.md` carries exactly such a table, under "Workflows that moved". Marking is for a mention you have read and judged correct, never a way to quiet one you have not looked at.

**Pointers into empty folders.** The root README sends a reader to `docs/ADRs/` for architecture decisions. Make sure that pointer resolves: write one real, unique file there — `docs/ADRs/ADR-NNN-short-description.md`, per the naming convention and status values in `docs/ADRs/README.md` — for every stack or architecture decision this session made, not just the single most significant one. Add a row to that README's `## Index` table for each file as you write it. `CLAUDE.md`'s own `## Decision log` (Phase 5) never hosts a decision's content itself; it only links to the files written here, so there is exactly one place the actual reasoning lives. A reader who follows a cross-reference into an empty directory learns nothing and stops trusting every other pointer in the repo — and a decision log split across two competing homes teaches the same distrust.

## Verify before moving on

Run the Phase 0 sweep again:

```bash
bash .claude/skills/init-project/scripts/check_inherited_docs.sh
```

Then run the two checks that resolve claims against reality, which the sweep's text matching cannot:

```bash
bash scripts/check_doc_claims.sh          # ecosystems, manifests and commands the docs name
bash scripts/check_scaffolded_project.sh  # every post-condition these phases promise
```

`check_scaffolded_project.sh` is the contract for this whole skill: placeholders gone, `docs/foundation.md` written, the ADR files indexed and linked, no template-only file or folder left behind (`CHANGELOG.md`, `docs/template/`, a `SNAPSHOT_PATH` still pointing there). Expect it to fail until Phases 5 and 6 are done — it is the Phase 7 gate, not a Phase 4 one. Run it here anyway to see what remains.

It checks three things: language still describing this repo as a template, links resolving to paths that do not exist, and references to `.prompt.md` files, which no longer exist anywhere in this layout.

Every hit must be either fixed or, if it is a deliberate historical mention — a decision-log entry recording that the repo was scaffolded from a template is the usual one — something you can name out loud as such. When the mention is permanent, mark its line `inherited-docs-ok` so the sweep stays a clean/dirty signal rather than a list of known-good noise that everyone learns to scroll past. Do not report this phase complete on an unexplained hit, and do not describe the sweep as clean while it still exits 1.

The sweep is a backstop, not the standard. It reads text; it cannot tell you that `CONTRIBUTING.md` documents a test command that does not exist, or that `SECURITY.md` lists ecosystems Dependabot is not actually watching. Confirm those against the files Phase 3 wrote.
