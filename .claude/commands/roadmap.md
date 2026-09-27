---
description: Manage the project roadmap — create it from the repo's stated goals, evaluate it against the code, add items, and tick them off, without inventing scope.
argument-hint: "[init | evaluate | add <item> | complete <item>]"
---

# Roadmap

**Role:** Project steward keeping the roadmap honest. The roadmap records what this project has committed to and what the repo shows is done — never what would be nice to build.

Read `ROADMAP_PATH`, `DOCS_ROOT`, `ADR_PATH`, `SNAPSHOT_PATH` and `SRC_ROOT` from the `## Session Config` section of `CLAUDE.md`. If `ROADMAP_PATH` is missing, use `docs/plans/ROADMAP.md` and say so once — a project whose table predates that row should still work. If any other value is missing, say so and ask rather than guessing; `init` reads from those paths, and a wrong one silently drops a source.

## Grounding rules

The failure this command exists to prevent is a roadmap that drifts into invention — items nobody asked for, scope extrapolated from a one-line goal, boxes ticked on a hunch. Every operation below follows these rules, and they win over any instinct to be helpful.

1. **Every item cites a source.** Only three kinds count:
   - A repo path that states a goal or a need — `docs/foundation.md`, `README.md`, `CLAUDE.md`, a file in `{DOCS_ROOT}plans/` or `{DOCS_ROOT}specs/`, an ADR in `{ADR_PATH}`, a snapshot in `{SNAPSHOT_PATH}`, or a source file carrying a `TODO`/`FIXME`.
   - An open issue, as `#N`.
   - The user asking for it directly in this conversation, as `requested YYYY-MM-DD`.

   An item with no source is not written. Say "No source found for: <item>" instead.
2. **Restate, never extrapolate.** An item paraphrases its source at the source's own scope. "Export results to CSV" becomes one item — not CSV, JSON and Parquet behind a plugin system.
3. **"Done" needs pointable evidence** — a file that implements it, a commit, a closed issue, a passing test. "Probably done" is reported as a question, never ticked.
4. **The script's output beats your reading.** When `check_roadmap.sh` reports on a line, start from what it says. If you disagree, say why and cite the file.
5. **Preserve formatting.** `add` and `complete` change one line. Never reflow, re-sort, renumber, rename a heading, normalize bullets, or fix an unrelated typo in passing. If the existing file uses a different format — a table, another bullet, another date style — follow the file, not the default below.

## The check script

Run [`scripts/check_roadmap.sh`](../../scripts/check_roadmap.sh) rather than reading the file and forming an opinion:

```bash
bash scripts/check_roadmap.sh . {ROADMAP_PATH}
```

It prints one line per checklist item — `OPEN`, `BLOCKED` or `DONE`, with line number and section — and flags `NO_SOURCE`, `BROKEN_SOURCE`, `MAYBE_DONE` (unticked, but every backticked path in it exists) and `DONE_MISSING` (ticked, but a path it names is gone). Exit 0 is no flags, 1 is at least one flag, 2 is no roadmap at that path. Its header block documents the rest.

- **Script not present** — the project has this command but not the script it runs, usually because it was synced before the sync carried scripts. Say so once, recommend running `/sync-from-template`, which offers `scripts/check_roadmap.sh` and its test alongside `.claude/`, and continue with a careful read-through — labelling every finding `unverified — no check_roadmap.sh`.
- **Zero items reported, file not empty** — the roadmap is not a markdown checklist. Read it directly and follow its structure for `add` and `complete`; label `evaluate` findings unverified.

## Default format

Used only when `init` creates the file. An existing roadmap keeps its own format.

```markdown
# Roadmap

What this project has committed to building. Every item cites where it came from; `/roadmap evaluate` checks those citations against the repo.

## Now

- [ ] Add TFA toggle in `src/auth/tfa.py` — source: docs/foundation.md

## Next

- [ ] Wire CI deploy step — source: #14 — blocked: needs ADR-003

## Later
```

One item per line, in this order:

- `- [ ] ` then an imperative summary of the work.
- The deliverable path in backticks — **only** when the source names it. Never guess a path; an invented one turns into a false `MAYBE_DONE` or `DONE_MISSING` later.
- ` — source: <ref>`, several separated by commas.
- ` — blocked: <reason>`, only when the blocker is pointable.
- ` — done YYYY-MM-DD (<short sha>)`, added by `complete`.

Sections: **Now** is in progress or next up, **Next** is committed but not started, **Later** is a stated goal with no near-term commitment. An empty section stays empty — never pad it.

## Phase 1: Context scan

Before anything else:

1. Run `git status`. If `{ROADMAP_PATH}` has uncommitted changes, stop and ask — someone is mid-edit, and writing over them loses work.
2. Check whether `{ROADMAP_PATH}` exists, and if so run the check script.
3. Note the current branch.

If the user passed an argument, route straight to that operation. Otherwise show the menu:

```
🗺️  ROADMAP
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. 🌱 INIT      — Create the roadmap from the repo's stated goals
2. 🔍 EVALUATE  — Check the roadmap against the code and issues
3. ➕ ADD       — Add an item, or update one
4. ✅ COMPLETE  — Tick an item off

Roadmap: {ROADMAP_PATH} ({found / not found})
Items:   {OPEN} open · {BLOCKED} blocked · {DONE} done

Reply with: ROADMAP: <number>
```

`init` on an existing roadmap never overwrites it — offer `evaluate` instead. `evaluate`, `add` or `complete` with no roadmap offers `init`.

## 1. INIT

### Gather

Read each source in full — skimming is how a stated goal gets missed and an unstated one gets invented:

1. `docs/foundation.md`
2. `CLAUDE.md` — Project identity, Current state, Open questions
3. `README.md`
4. `{DOCS_ROOT}plans/` and `{DOCS_ROOT}specs/`
5. `{ADR_PATH}` — Accepted ADRs that commit to building something
6. The most recent snapshot in `{SNAPSHOT_PATH}` — Next Steps and Technical Debt
7. Open issues, through whatever GitHub tooling this session has. If none is available, say the issues were not read rather than implying there are none.
8. `grep -rn "TODO\|FIXME" {SRC_ROOT}` — only comments phrased as work still to do

HTML comments, `{…}` placeholders and "fill this in" guidance are not goals. If every source is empty or a placeholder, respond exactly: "No stated goals found. Fill in CLAUDE.md's Project identity and Current state, or add a docs/foundation.md, then run /roadmap init again." — do not draft a roadmap from the repo's folder names.

Work already listed under Done in `CLAUDE.md` stays out of the roadmap. It is recorded there, and a second copy is one more place for the two to disagree.

### Draft

Map each goal to one item, placed by the section rules above — `CLAUDE.md`'s In progress to **Now**, its Not started to **Next**, goals with no stated timing to **Later**.

```
ROADMAP DRAFT — {ROADMAP_PATH}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Sources:  [each source — ✅ used / ➖ nothing actionable / ⚠️ not available]
Items:    {n} ({now} now · {next} next · {later} later)
Left out: [anything considered and dropped, and why]
```

Then the full file, in the default format.

> [!IMPORTANT]
> Gate — write approval. The user must reply exactly `ROADMAP: WRITE`.

### Verify

Write the file, creating its parent folder only if missing, then run the check script. `NO_SOURCE` and `BROKEN_SOURCE` must both be 0 — fix any before reporting done. A `MAYBE_DONE` means the deliverable file already exists, not that the work is finished — an In progress item's file usually does. Open it: if it implements the item, take the item out, since finished work belongs in `CLAUDE.md`'s Done; if it is a stub or partial, keep the item and say so.

## 2. EVALUATE

Read-only until the user approves a fix.

### Gather evidence

Run the check script, then work each line it reports:

| Script says | Look for |
|---|---|
| `MAYBE_DONE` | Open the deliverable — a stub or empty file is not done. `git log --oneline -- <path>` for the commit that landed it. |
| `OPEN` naming no path | `git log --oneline --grep=<key term>` and cited `#N` issues now closed. Tests covering the behaviour. |
| `BLOCKED` | Whether the blocker still holds — the issue still open, the ADR still Draft, the dependency still absent. |
| `OPEN` that may be blocked | Only a pointable blocker counts: a Draft ADR it depends on, an Open question in `CLAUDE.md` naming it, a cited issue labelled blocked. |
| `DONE_MISSING` | `git log --oneline --diff-filter=DR -- <path>` — renamed, deleted, or never landed. |
| `BROKEN_SOURCE` | `git log --oneline --diff-filter=DR -- <path>` — did the goal move, or was it dropped? |
| `NO_SOURCE` | Search the INIT sources for one. If none exists, propose removing the item or recording `requested YYYY-MM-DD` — the user decides which. |

Then compare the other way: goals **stated in the INIT sources** that have no roadmap item. Report them; do not add them.

### Report

```
ROADMAP EVALUATION — {ROADMAP_PATH}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Inventory: {OPEN} open · {BLOCKED} blocked · {DONE} done

✅ Looks done, not ticked:    [line — item — evidence: file / commit / closed #N]
⛔ Blocked:                   [line — item — blocker, and where it is recorded]
⚠️  Drift:                    [line — item — flag — what you found]
➕ Stated, not on roadmap:    [goal — source]
❔ Unverified:                [what you could not check, and why]
```

Every finding carries its evidence. If there is nothing to report, say the roadmap matches the repo and stop.

### Fix

For each finding, show the exact one-line change it implies — a `complete`, a `blocked:` added or removed, a corrected source, a new item through `add`.

> [!IMPORTANT]
> Gate — fix approval. The user must reply `ROADMAP: APPLY <n>` or `ROADMAP: APPLY ALL`.

Apply each through the rules of the operation it belongs to, including that operation's verification.

## 3. ADD

Input is the item text, and optionally a section and a source.

1. **Source.** Verify one the user gave — the path exists, the issue exists. If none was given, search the INIT sources for the goal and cite it. If nothing states it, ask where it comes from; the user asking for it now is a valid source, recorded as `requested <today>`. Never fabricate one.
2. **Duplicate check.** Search the inventory for the same work. If an item already covers it, this is an **update**: show the existing line and its replacement instead of adding a second.
3. **Section.** The user's choice; otherwise infer it from the source, and ask when it is ambiguous. Use the file's existing headings — never create one unless the user asks.
4. **Format.** Copy the style of the item above it in that section exactly: bullet character, indentation, separator, date format. Insert after the section's last item.
5. **Scope.** One requested item is one line. Do not split it into sub-tasks or add related items alongside. If it looks like several deliverables, ask.

An update may change an item's wording at the same scope, move it between sections, add or clear `blocked:`, or add a source. Anything else is a new item.

Preview as a diff:

```diff
 ## Next

 - [ ] Wire CI deploy step — source: #14 — blocked: needs ADR-003
+- [ ] Export results to CSV — source: requested 2026-09-26
```

> [!IMPORTANT]
> Gate — edit approval. The user must reply exactly `ROADMAP: ADD` for a new item or `ROADMAP: UPDATE` for a changed one.

**Verify:** the check script lists the new line with no `NO_SOURCE` or `BROKEN_SOURCE` on it, and `git diff --stat {ROADMAP_PATH}` shows one insertion — or one insertion and one deletion per line an update touched.

## 4. COMPLETE

Input is part of an item's text, or its line number.

1. **Match.** Resolve it against the inventory. No match — say so and show the closest items. More than one — list them with line numbers and ask. Never guess.
2. **Evidence.** Find one pointable piece: the deliverable file, the commit (`git log --oneline -n 20 -- <path>`, or a SHA the user gives, checked with `git cat-file -e <sha>`), a closed issue. If there is none, say so and ask — the user may still tick it, and the log then reads `done YYYY-MM-DD` with no SHA.
3. **Edit.** On that line only: `[ ]` becomes `[x]`, and ` — done YYYY-MM-DD (<short sha>)` is appended, using `date +%F` and `git rev-parse --short`. If the file already logs completions another way, follow it; if the user asks for no log, toggle the box only. The item stays in its section — moving done work is a reformat.

Preview as a diff:

```diff
-- [ ] Add TFA toggle in `src/auth/tfa.py` — source: docs/foundation.md
+- [x] Add TFA toggle in `src/auth/tfa.py` — source: docs/foundation.md — done 2026-09-26 (f73e0d6)
```

> [!IMPORTANT]
> Gate — completion approval. The user must reply exactly `ROADMAP: COMPLETE`.

**Verify:** the check script reports the line as `DONE` with no `DONE_MISSING`, and `git diff --stat {ROADMAP_PATH}` shows one insertion and one deletion.

## Conventions

- **Gate phrases are exact.** Do not accept a paraphrase as confirmation.
- **Dates are real.** Today's date from `date +%F`, never one inferred from context.
- **`git diff` is the proof.** After every write, show `git diff {ROADMAP_PATH}`. A line changed that the operation did not name is a bug — revert that hunk before going on.
- **Never commit.** Suggest a message instead — `docs(roadmap): <what changed>` — or hand off to `/commit-msg`.
- **Branch awareness** — work on the current branch, never a hardcoded `main`.
