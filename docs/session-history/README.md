# Session History

This folder contains session snapshots — end-of-session summaries that capture
what was accomplished, what's in progress, and what comes next. They serve as the
memory bridge between development sessions, particularly for LLM-assisted
workflows.

A session snapshot written at the end of one session becomes the starting context
for the next. Without them, every session starts cold. The `session-start` and
`create-commit` prompts in `.github/prompts/` read from and write to this folder.

## What belongs here

- Session snapshot files (one per working session)
- Progress logs and milestone completion notes
- Mid-project status summaries

## What does not belong here

- Formal status reports for stakeholders (standalone docs or `docs/SOPs/`)
- Architecture decisions made during a session (those go in `docs/ARDs/`)
- Plans for future work (those go in `docs/plans/`)

## Naming convention

`SESSION_SNAPSHOT_YYYY-MM-DD.md` — one file per session, named by date.
If multiple sessions happen in one day: `SESSION_SNAPSHOT_YYYY-MM-DD-2.md`

## Recommended snapshot structure

Each snapshot should include:
- **Date and session goal**
- **What was accomplished** (bullet list)
- **Current state of in-progress work**
- **Blockers or open questions surfaced**
- **Suggested next steps**
- **Suggested commit message**
