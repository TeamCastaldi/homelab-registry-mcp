---
description: Seed the repo's GitHub Issues from its stated goals, audit the open issues against the code, or migrate an old ROADMAP.md file into issues — without inventing scope.
disable-model-invocation: true
argument-hint: "[seed | evaluate | migrate]"
---

# Roadmap

**Role:** Project steward keeping the roadmap honest. The roadmap is this repo's open GitHub Issues: what the project has committed to, and what the repo shows is done — never what would be nice to build.

This command is for the deliberate, whole-backlog jobs. Filing, finding, starting, blocking and closing one issue at a time is the `issue-tracker` skill's, in ordinary conversation; this command applies its rules (`.claude/skills/issue-tracker/SKILL.md`) whenever it writes an issue.

Read `DOCS_ROOT`, `ADR_PATH`, `SNAPSHOT_PATH` and `SRC_ROOT` from the `## Session Config` section of `CLAUDE.md`. If any is missing, say so and ask rather than guessing; `seed` reads from those paths, and a wrong one silently drops a source. The label vocabulary is `.github/labels.yml` — never use a label it does not define.

## Grounding rules

The failure this command exists to prevent is a backlog that drifts into invention — issues nobody asked for, scope extrapolated from a one-line goal, issues closed on a hunch. Every operation below follows these rules, and they win over any instinct to be helpful.

1. **Every issue this command creates cites a source** in its `### Source` section. Only three kinds count:
   - A repo path that states a goal or a need — `docs/foundation.md`, `README.md`, `CLAUDE.md`, a file in `{DOCS_ROOT}plans/` or `{DOCS_ROOT}specs/`, an ADR in `{ADR_PATH}`, a snapshot in `{SNAPSHOT_PATH}`, or a source file carrying a `TODO`/`FIXME`.
   - An existing issue, as `#N`.
   - The user asking for it directly in this conversation, as `Requested in conversation, YYYY-MM-DD`.

   An item with no source is not filed. Say "No source found for: <item>" instead.
2. **Restate, never extrapolate.** An issue paraphrases its source at the source's own scope. "Export results to CSV" becomes one issue — not CSV, JSON and Parquet behind a plugin system. Its acceptance criteria come from the source too, or read `_Not yet known_`.
3. **Priority only when stated.** A source that says "first" or "must" may set one; otherwise leave it off.
4. **"Done" needs pointable evidence** — a merged PR, a commit, a file that implements it, a passing test. "Probably done" is reported as a question, never closed.
5. **The script's output beats your reading.** When `check_issues.sh` reports on an issue, start from what it says. If you disagree, say why and cite the issue.

## GitHub access and the check script

Use the access the `issue-tracker` skill describes (its section 1): `gh`, through `gh api` REST calls only, else the GitHub connector. With neither, stop and say so — there is no offline roadmap to fall back to. Confirm the labels exist the way its section 2 does before any write.

Read the open issues through [`scripts/check_issues.sh`](../../scripts/check_issues.sh):

```bash
gh api 'repos/{owner}/{repo}/issues?state=open&per_page=100' --paginate \
  | bash scripts/check_issues.sh -
```

Through the connector, save every page of `list_issues` (state open) as one JSON array in the scratchpad and pass that file instead of `-`. It prints an inventory in pick-up order — `IN_PROGRESS`, `PLANNED`, `BLOCKED`, `BACKLOG`, `UNSORTED` — then flags: `NO_STATUS`, `MULTI_STATUS`, `NO_TYPE`, `MULTI_TYPE`, `MULTI_PRIORITY`, `NO_PRIORITY`, `UNKNOWN_LABEL`, `NO_CRITERIA`, `CRITERIA_MET` and `BLOCKED_NO_REASON`. Exit 0 is no flags, 1 is at least one, 2 is unreadable input. Its header block documents each.

If the script is missing, the project has this command but not the tooling behind it. Say so once, recommend the `sync-from-template` skill, which brings in `scripts/check_issues.sh`, `scripts/sync_labels.sh`, `.github/labels.yml` and the issue forms, and stop.

## Phase 1: Context scan

1. Confirm GitHub access and the repo, and check the labels.
2. Run the check script.
3. Check whether `docs/plans/ROADMAP.md` exists, or `CLAUDE.md`'s Session Config still has a `ROADMAP_PATH` row.

If the user passed an argument, route straight to that operation. Otherwise show the menu:

```
🗺️  ROADMAP
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. 🌱 SEED      — File issues for the repo's stated goals
2. 🔍 EVALUATE  — Check the open issues against the code
3. 📦 MIGRATE   — Turn an old ROADMAP.md into issues     (only if one exists)

Repo:   {owner/repo}
Issues: {IN_PROGRESS} in progress · {PLANNED} planned · {BLOCKED} blocked · {BACKLOG} backlog · {FLAGS} flagged

Reply with: ROADMAP: <number>
```

For one issue at a time — "add this", "close #12" — point the user at plain conversation instead; the `issue-tracker` skill handles it.

## 1. SEED

### Gather

Read each source in full — skimming is how a stated goal gets missed and an unstated one gets invented:

1. `docs/foundation.md`
2. `CLAUDE.md` — Project identity, Current state, Open questions
3. `README.md`
4. `{DOCS_ROOT}plans/` and `{DOCS_ROOT}specs/`
5. `{ADR_PATH}` — Accepted ADRs that commit to building something
6. The most recent snapshot in `{SNAPSHOT_PATH}` — Next Steps and Technical Debt
7. `grep -rn "TODO\|FIXME" {SRC_ROOT}` — only comments phrased as work still to do

HTML comments, `{…}` placeholders and "fill this in" guidance are not goals. If every source is empty or a placeholder, respond exactly: "No stated goals found. Fill in CLAUDE.md's Project identity and Current state, or add a docs/foundation.md, then run /roadmap seed again." — do not draft issues from the repo's folder names.

Work already listed under Done in `CLAUDE.md` is not filed.

### Deduplicate

Search open **and closed** issues for each goal. An open match is already on the roadmap; a closed one was done or dropped. Neither is filed again — list them under "Already tracked".

### Draft

One issue per goal, in the `issue-tracker` skill's format (its section 3): verb-first title, one `type:*` label, that type's sections, `### Source`. Status follows the source: `CLAUDE.md`'s In progress → `status:in-progress`, its Not started → `status:planned`, a goal with no stated timing → `status:backlog`.

```
ROADMAP SEED — {owner/repo}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Sources:          [each source — ✅ used / ➖ nothing actionable / ⚠️ not available]
To file:          {n} ({in progress} in progress · {planned} planned · {backlog} backlog)
Already tracked:  [goal — #N, open or closed]
Left out:         [anything considered and dropped, and why]
```

Then every draft in full: title, labels, body.

> [!IMPORTANT]
> Gate — create approval. The user must reply exactly `ROADMAP: CREATE`.

### Verify

File them, then run the check script again. Every new issue appears in the inventory with no `NO_STATUS`, `NO_TYPE` or `UNKNOWN_LABEL` flag. List each as `#N title` with its URL.

## 2. EVALUATE

Read-only until the user approves a fix.

### Gather evidence

Run the check script, then work each line it reports:

| Script says | Look for |
|---|---|
| `CRITERIA_MET` | A merged PR or commit that did it: `git log --oneline --grep=<key term>`, PRs mentioning `#N`. Ticked boxes are a claim; the code is the evidence. |
| `IN_PROGRESS` | When it last moved — its latest comment, commit or linked PR. Weeks of silence is a question for the user, not a status change. |
| `BLOCKED` with `deps:` | Whether each `#N` it waits on is closed now. All closed means it can be unblocked. |
| `BLOCKED_NO_REASON` | Its comments for the reason. Propose writing it under `### Dependencies`. |
| `NO_STATUS`, `NO_TYPE`, `MULTI_*` | The issue's text, to propose the one label it should have. |
| `NO_PRIORITY` | Leave the choice to the user; list these together. |
| `NO_CRITERIA` | The issue and its source, for what "done" means. Propose criteria only from what they state. |
| `UNKNOWN_LABEL` | The label in `.github/labels.yml` it was meant to be. |

Then compare the other way: goals **stated in the SEED sources** that have no issue, open or closed. Report them; do not file them.

### Report

```
ROADMAP EVALUATION — {owner/repo}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Inventory: {IN_PROGRESS} in progress · {PLANNED} planned · {BLOCKED} blocked · {BACKLOG} backlog

✅ Looks done, still open:   [#N — title — evidence: PR / commit / file]
🔓 Can be unblocked:         [#N — what it waited on, now closed]
⚠️  Drift:                    [#N — flag — what you found]
➕ Stated, not filed:        [goal — source]
❔ Unverified:                [what you could not check, and why]
```

Every finding carries its evidence. If there is nothing to report, say the issues match the repo and stop.

### Fix

For each finding, show the exact change it implies — a label swap, a close with its evidence comment, a section edit, a new issue.

> [!IMPORTANT]
> Gate — fix approval. The user must reply `ROADMAP: APPLY <n>` or `ROADMAP: APPLY ALL`.

Apply each through the `issue-tracker` skill's rules, including reading the issue back afterwards.

## 3. MIGRATE

Offered only when `docs/plans/ROADMAP.md` exists or `CLAUDE.md` still has a `ROADMAP_PATH` row (use the path it names). It turns a checklist roadmap from an earlier version of this tooling into issues, once.

1. **Read the file.** Items are checklist lines, `- [ ] text — source: … — blocked: …`, under `## Now`, `## Next` and `## Later`, or whatever headings the file uses.
2. **Map each unticked item** to a draft issue:
   - Now and Next → `status:planned`; Later → `status:backlog`.
   - `— blocked: <reason>` → `status:blocked`, with the reason under `### Dependencies`.
   - Its `source:` becomes `### Source`, unchanged.
   - Its type is drafted from the wording and marked as a guess, for the user to correct at the gate.
   - An item whose source is `#N` already is an issue: propose labels for `#N` instead of a new issue.
   - Ticked items are done; they stay in git history and are not filed.
3. **Deduplicate** against open and closed issues, as in SEED.

Show the drafts and the label changes in full.

> [!IMPORTANT]
> Gate — migration approval. The user must reply exactly `ROADMAP: MIGRATE`.

File them and verify as SEED does. Then offer — as a separate step the user approves in plain words — to `git rm` the roadmap file and delete the `ROADMAP_PATH` row from `CLAUDE.md`, so there are not two roadmaps to disagree.

## Conventions

- **Gate phrases are exact.** Do not accept a paraphrase as confirmation.
- **Dates are real.** Today's date from `date +%F`, never one inferred from context.
- **Read back is the proof.** After every write, fetch the issue and show its labels and state. A write that did not stick is fixed before going on.
- **Never commit.** MIGRATE's file removal is suggested as a commit message — `docs(roadmap): move the roadmap into GitHub Issues` — or handed to `/commit-msg`.
- **Branch awareness** — work on the current branch, never a hardcoded `main`.
