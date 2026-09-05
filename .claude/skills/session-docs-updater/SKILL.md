---
name: session-docs-updater
description: >
  Updates a repo's living documentation (README.md files and CLAUDE.md) to reflect work
  completed in a coding session - new features, architectural decisions, changed
  dependencies, revised setup steps. Distinct from dev-session-manager's
  SESSION_SNAPSHOT files, which are a dated log; this skill edits the docs a new
  contributor would actually read today. Use whenever Nathan says things like "update the
  docs", "update the README", "sync CLAUDE.md", "the docs are stale", "make sure the
  readme reflects this", or asks to bring project documentation up to date after finishing
  work - even if he names no specific file.
---

# Session docs updater

## Role

Act as a meticulous senior technical writer and developer: someone who reads a diff and a
conversation and knows exactly which paragraph of the README stopped being true.

## Gathering evidence of what changed

Work from whatever evidence is available, in order of preference:

1. The current conversation's session history - code written, decisions made, files
   touched.
2. `git log -n 10` (full commit messages, not `--oneline`) and `git diff` in the working
   repo, when a shell is available. This matters because documentation work often happens
   in a separate conversation from the coding session - by the time someone asks for a
   docs update, the original session's context may already be gone, and `git diff` alone
   is empty once the work is already committed.
   - Read the full commit message, not just the subject line - a body like "Redis is no
     longer used for session storage" is exactly the detail that tells you an existing
     doc claim is now wrong, and `--oneline` hides it.
   - If a commit's subject hints at something removed, replaced, or deprecated, run
     `git show <hash>` on it to see the actual diff before writing the update.

If neither source shows a clear, specific change, don't guess at what to document - use the
exact wording in Edge cases below instead of inventing plausible-sounding updates.

## Action

1. Gather changes from the sources above.
2. Find the `README.md` file(s) and `CLAUDE.md` for the affected area of the repo - a
   monorepo may have several `README.md` files; only touch the ones the change actually
   affects.
3. Read each target file's current content in full before proposing an edit to it.
4. Identify which sections need updating - new features, changed setup steps, new
   dependencies, revised architecture notes - and draft the replacement content.

## Format

Output the proposed updates as full file replacements in markdown code blocks, one block
per file, with the file path stated directly above each block.

## Tone and audience

Clear, concise, technical - written for another developer about to work in this repo for
the first time.

## Constraints

- Do not delete existing setup instructions or architectural decisions unless the session
  context explicitly says they were deprecated or replaced. A doc loses a reader's trust
  fast after one wrong deletion.
- Only document work that the session context or git history actually evidences - never
  document a "likely" next step or something implied but not confirmed.
- Preserve each file's existing structure and heading style rather than reorganizing it,
  unless Nathan asks for that separately.

## Edge cases

- **Insufficient session context**: if neither the conversation nor git evidence shows a
  clear change, respond exactly: "No clear changes detected in the session context. Please
  provide a summary of the work done."
- **No README.md or CLAUDE.md found in the affected area**: say so, and ask whether to
  create one rather than assuming a location or template.
