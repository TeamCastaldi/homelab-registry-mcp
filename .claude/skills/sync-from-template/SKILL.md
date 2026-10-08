---
name: sync-from-template
description: >-
  Pulls newer commands, skills and the scripts they run from the upstream project-template
  repo into a project scaffolded from it, showing a diff per file and waiting for
  confirmation before overwriting anything. Also turns the Migration steps of any Major
  changelog entry in the version gap into proposed deletions and edits. Use when the user
  asks to sync, pull or update tooling, commands or skills from the template, says the
  template has newer tooling, asks to catch up on or migrate to a newer template version, or
  asks whether this repo is behind project-template. Not for auditing a repo's own
  consistency between folders, READMEs and CLAUDE.md; that is the /sync-template command.
---

# Sync from template

## What this does

Reaches from the current repo (a project created from `project-template`) back to
the template repo, and pulls its current `.claude` folder — commands and
skills — in. Every file that differs is shown as a diff and held for confirmation before
it touches anything on disk. Nothing is overwritten silently.

Some of those commands and skills depend on files that live outside
`.claude/` — `/sync-template` runs `scripts/validate_skills.sh`, and the
`issue-tracker` skill needs `.github/labels.yml`, the issue forms and the label
workflow. The template lists exactly those files in
[`tooling_paths.txt`](tooling_paths.txt), and they are offered alongside
`.claude/` under the same diff-and-confirm rules. Nothing else outside
`.claude/` is ever touched.

It also reads the changelog gap between this project's `.template-version`
and the template's current one. A breaking (Major) release can require more
than new files — deleting something, or editing a file the normal sync
doesn't touch — and those get proposed as their own batch, held for their
own confirmation, same as everything else here.

## Not to be confused with `/sync-template`

That command audits a repo's own internal consistency without reaching outside
it; this skill reaches from a downstream project back to the template and pulls
files in.

## One-time setup: the config file

The first time this runs in a repo, look for `.claude/sync-from-template.yaml`.
If it does not exist, this is a first run: propose the defaults below (this is
the template's own repo, so the URL and branch are already known), let the user
confirm or override, and create the file.

```yaml
# .claude/sync-from-template.yaml
# Fill in once, when this repo is set up. Committed to the repo so it travels
# with clones and doesn't live only in one machine's git config.
template_repo_url: https://github.com/TeamCastaldi/project-template.git
template_ref: main
sync_paths:
  - .claude
```

Proposing the default above on first run is fine -- it's a known, confirmed
value, not a guess. If the user ever points this skill at a different template
repo, or the default above stops being accurate, don't invent a replacement
URL; stop and ask.

## Workflow

### 1. Fetch and compare

Run [`scripts/compare_template.sh`](scripts/compare_template.sh) from the
repo root, with the values from the config file:

```bash
bash .claude/skills/sync-from-template/scripts/compare_template.sh "$TEMPLATE_REPO_URL" "$TEMPLATE_REF" "$PROJECT_ROOT" "${SYNC_PATHS[@]}"
```

This does an ephemeral sparse clone of the template (shallow, blob-filtered,
scoped to `sync_paths` only) into a temp directory, then reports how every
file under those paths compares to the local repo. It never touches the local
repo itself -- it only reads and reports. The output it prints is described
below; read the script itself only if what it prints does not match that.

The script's output gives you four buckets per file: `NEW`, `CHANGED`, `SAME`,
`LOCAL_ONLY`. It also prints `TEMP_CLONE=<path>` (where the fetched template
copy lives) and `TEMPLATE_SHA=<short sha>` (the commit you're comparing
against). Keep both of these -- you need them for the rest of the workflow.

It also prints `TOOLING_PATHS=`: the files outside `sync_paths` that the
template's `tooling_paths.txt` added to the comparison, or `none`. They land in
the same four buckets, and are diffed and copied from `$TEMP_CLONE` exactly like
a `.claude/` file. The list is read from the *template*, so it is always the
template's current idea of what its commands need -- never add a path to
`sync_paths` to get a script; add it to the template's list instead, and every
downstream project picks it up.

**No `TOOLING_PATHS=` line at all** means this project's own copy of
`compare_template.sh` predates the list. This sync will offer the newer script
as `CHANGED`; once it has been pulled, run step 1 again before reporting done,
so the tooling files are offered in the same session rather than silently
waiting for the next one. Say that is what you are doing.

It also prints `TEMPLATE_VERSION` and `PROJECT_VERSION`, read from each side's
`.template-version`. These turn the report from a raw file diff into a
statement of how far behind this project is:

- **Both known and different** -- name the gap (`1.4.0 -> 2.1.0`) and read
  `CHANGELOG.md` from `$TEMP_CLONE` for the entries between them (it's a
  root-level file, present even though `sync_paths` only lists `.claude`).
  A **Major** entry means a downstream project is expected to act by hand, so
  surface those before showing any file diffs: they explain *why* files
  changed, which is the thing a file-by-file diff cannot tell anyone. See
  "Migration steps" below for turning these into proposed actions rather than
  just prose to read.
- **Both known and equal** -- say so. Any `CHANGED` file is then a local edit,
  not an upstream update, and that is worth pointing out rather than offering to
  overwrite.
- **`PROJECT_VERSION=unknown`** -- this project predates template versioning.
  Offer to write the current `TEMPLATE_VERSION` into `.template-version` as part
  of this sync, so the next run can report a real gap.
- **`TEMPLATE_VERSION=unknown`** -- the pinned ref predates versioning. Fall back
  to the SHA and say that is what you are comparing against.

### 2. Report

Present the comparison grouped by status, in this format:

```text
TEMPLATE SYNC REPORT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Comparing against project-template @ <template_ref> (commit <sha>)
Version: <PROJECT_VERSION> -> <TEMPLATE_VERSION>

🔧 Migration steps: [see below -- parsed from Major entries, or "none"]
🧰 Tooling:          [TOOLING_PATHS, or "none" -- their status is in the rows below]
✅ Up to date:       [files marked SAME]
🔄 Changed upstream: [files marked CHANGED]
➕ New in template:  [files marked NEW]
❓ Local-only:       [files marked LOCAL_ONLY -- never auto-removed]
```

Drop the version line if both sides are `unknown`. If there are no Major
entries between the two versions, the migration row just says "none" -- don't
drop the row entirely, since its absence reads differently than a confirmed
"nothing to do."

If every file is `SAME` and there are no migration steps, say so plainly and
stop -- there's nothing to confirm or apply.

### Migration steps

For each Major changelog entry between `PROJECT_VERSION` and
`TEMPLATE_VERSION`, look for a `### Migration steps` heading (a sibling of
`### Added` / `### Changed`) and read its bullets. A Major entry with no such
heading is not silently skipped -- show its prose as a manual note ("no
automated action; read this and act by hand") and move on. Never infer a
`DELETE` or `EDIT` action from prose that isn't in that exact list; guessing
at a deletion is exactly the failure mode this convention exists to avoid.

Concatenate the lists from every Major entry in the gap, in version order.
For each action, check the project's current state before proposing anything:

- `DELETE <path>` -- if the path doesn't exist in the project, it's already
  done; don't list it as pending.
- `EDIT <path>: <target state>` -- read the file and judge whether the
  described state already holds. If it does, it's already done. If not, work
  out the specific change and have it ready to show.

Present the result:

```text
MIGRATION STEPS (<PROJECT_VERSION> -> <TEMPLATE_VERSION>)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ Already done: [actions whose target state already holds]

⏳ Pending:
  1. DELETE <path>            (<n> files, if a directory)
  2. EDIT <path> -- <one-line summary of the change>
     [the actual diff or content to insert, shown in full]

Reply SYNC: MIGRATE to apply everything under Pending.
```

`SYNC: MIGRATE` is a single confirmation for the whole pending batch, not
per-item -- but every pending item is shown in full above it first, so that
one reply is an informed one, not a blind one. Never split it into individual
prompts; that's what `SYNC: PULL <path>` is for on the file side, and the two
mechanisms stay separate on purpose (a migration action can delete something;
a file pull never does).

To apply a pending `DELETE`, remove the path. To apply a pending `EDIT`, make
the change you showed -- for the CLAUDE.md Session Config case, that means
copying the table out of `$TEMP_CLONE/CLAUDE.md` verbatim rather than
retyping it.

### 3. Confirm and apply, one file at a time

For each `CHANGED` file, show a real diff before asking for anything:

```bash
diff -u "$PROJECT_ROOT/$rel" "$TEMP_CLONE/$rel"
```

For each `NEW` file, show its content (it's new, there's nothing to diff
against).

Wait for one of these before touching disk:

- `SYNC: PULL <path>` -- apply that one file
- `SYNC: PULL ALL` -- apply every `CHANGED` and `NEW` file reported

To apply a file, copy it from the temp clone over the local path, creating
parent directories if the file is new:

```bash
mkdir -p "$(dirname "$PROJECT_ROOT/$rel")"
cp "$TEMP_CLONE/$rel" "$PROJECT_ROOT/$rel"
```

`LOCAL_ONLY` files are report-only. Never delete, move, or modify them,
regardless of what confirmation phrase the user gives -- there is no phrase
that authorizes touching them. If a file only exists locally, that's either
intentional local customization or something the template dropped; either
way it needs a human to look at it deliberately, not this skill deciding on
its behalf.

### 4. Stamp the version

Once at least one file has been applied, at least one migration step has been
applied, or every pending item was already-done, write the template version
this sync brought the project up to:

```bash
echo "$TEMPLATE_VERSION" > "$PROJECT_ROOT/.template-version"
```

Do this only when `TEMPLATE_VERSION` is not `unknown`. A project that
declined every change is still on its old version, and a marker claiming
otherwise makes the next sync report a gap that does not exist -- worse than
having no marker at all.

If the user applied only some of the `CHANGED` files, or only some of the
pending migration steps, say so and ask before stamping: the version is a
claim about the whole synced tree, and a partial apply does not support it.

### 5. Labels, if this brought in issue tracking

If `.github/labels.yml` or `.github/workflows/label-sync.yml` was just applied,
the repo's GitHub labels may not exist yet. Run the `issue-tracker` skill's
"Labels ready" check (its section 2) and, if labels are missing, offer to
create them now. If they can't be created from this session, say the "Sync
labels" workflow creates them once the sync commit reaches `main`.

### 6. Clean up

Once the user is done applying changes (or decides not to apply any), remove
the temp clone:

```bash
rm -rf "$TEMP_CLONE"
```

This is safe to run without asking first -- `$TEMP_CLONE` is a directory this
skill created a few minutes ago under `mktemp -d`, not anything of the user's.

### 7. Suggest a commit

Once at least one file was applied, suggest (don't run) a commit:

```text
chore(tooling): sync .claude and tooling from project-template@<short-sha>
```

When a version was stamped, name it instead -- it means more to a reader six
months out than a short SHA does:

```text
chore(tooling): sync tooling from project-template 1.4.0 -> 2.1.0
```

## Boundaries

- Never invent `template_repo_url` -- ask if the config file doesn't have it.
- Never overwrite a `CHANGED` or apply a `NEW` file without an explicit
  `SYNC: PULL` confirmation for it.
- Never touch a `LOCAL_ONLY` file. Ever. That's out of scope for this skill,
  not just gated behind a confirmation phrase.
- Never pull a path outside `sync_paths` unless the template's
  `tooling_paths.txt` names it. A script sitting next to a listed one is not
  listed -- `scripts/` also holds files that only belong upstream, and
  offering those would undo what `init-project` deliberately removed.
- Never delete or edit anything as a "migration step" unless it's named,
  verbatim, in a `### Migration steps` list in `CHANGELOG.md`. A Major entry
  without that heading is shown as text, never turned into an action.
- Never apply a pending migration action without `SYNC: MIGRATE`.
- Never leave a temp clone behind after the sync is done or abandoned.
