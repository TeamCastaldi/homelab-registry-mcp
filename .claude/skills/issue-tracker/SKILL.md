---
name: issue-tracker
description: >-
  Files, finds and updates this repository's GitHub Issues from conversation, using the
  repo's own labels (status:*, type:*, p0-p3) and the sections its issue forms define. Use
  when the user wants to note a bug, idea, feature or piece of tech debt for later ("log
  this", "file an issue", "note that X is broken, we'll fix it later", "add it to the
  backlog"); asks what issues are open or what to work on next; or wants to start, block,
  unblock, close or drop a specific issue, including naming an issue number (#12) as the
  work to do. Not for seeding issues in bulk from the project's goals, auditing them, or
  migrating an old ROADMAP.md file, which is the /roadmap command, and not for personal
  to-do lists kept outside GitHub.
---

# Issue tracker

The repo's GitHub Issues are where its planned work lives. This skill keeps every issue in the shape the rest of the tooling reads: exactly one `status:*` and one `type:*` label, a priority only when someone gave one, and the `### ` sections from `.github/ISSUE_TEMPLATE/`. `scripts/check_issues.sh` and `/roadmap evaluate` read those, so an issue filed loosely here becomes noise there.

**Must not be lost:**

- Never file or close an issue without showing the draft first. A plain "yes", "ok" or "file it" is confirmation; an exact phrase is not needed. Moving an issue's status when the user asked for exactly that needs no second confirmation.
- Never invent detail. What the user did not say is `_Not yet known_`, not a guess.
- Never guess a priority, and never create a label that is not in `.github/labels.yml`.
- After the work, go back to what the conversation was doing before. Filing an issue is an aside, not a new mission.

## 1. GitHub access

Use whatever this session has, in this order:

1. `gh`, when `gh api 'repos/{owner}/{repo}' --jq .full_name` prints the repo. Use **only `gh api`** (REST), never `gh issue …`, `gh label …` or `gh repo view`: those use GraphQL, which Claude Code cloud sessions refuse with HTTP 403 while REST works. `{owner}/{repo}` in a path is filled in from this directory's git remote. The calls this skill needs are in the table below.
2. The GitHub connector's tools (`list_issues`, `search_issues`, `issue_read`, `issue_write`, `add_issue_comment`, `actions_run_trigger`). Take owner and repo from `git remote get-url origin`.

With neither, say once what is missing and stop. Locally: install `gh` and run `gh auth login`. In a cloud session: connect GitHub to Claude and add this repo to the session. Never fall back to writing the issue into a file; the point is one place to look.

The `gh api` calls this skill needs:

```bash
# read an issue, and its comments
gh api 'repos/{owner}/{repo}/issues/12'
gh api 'repos/{owner}/{repo}/issues/12/comments'
# look for duplicates, open and closed: list them and filter here (the search
# API is not repo-scoped, and cloud sessions refuse it)
gh api 'repos/{owner}/{repo}/issues?state=all&per_page=100' --paginate \
  --jq '.[] | select(.pull_request == null) | [.number, .state, .title] | @tsv' | grep -i -E 'export|safari'
# file one, body from a file in the scratchpad
gh api 'repos/{owner}/{repo}/issues' -f title='Fix export on Safari' \
  -f body="$(cat "$SCRATCH/issue.md")" -f 'labels[]=type:bug' -f 'labels[]=status:backlog'
# set labels (replaces the whole set: send every label it keeps), title or body
gh api -X PATCH 'repos/{owner}/{repo}/issues/12' -f 'labels[]=type:bug' -f 'labels[]=status:in-progress'
# comment
gh api 'repos/{owner}/{repo}/issues/12/comments' -f body='Blocked on #9.'
# close: completed, or not_planned
gh api -X PATCH 'repos/{owner}/{repo}/issues/12' -f state=closed -f state_reason=completed
```

The REST issues endpoints return pull requests too; anything with a `pull_request` field is a PR, not an issue.

## 2. Labels ready

Once per session, before the first write, check that the labels exist:

```bash
bash scripts/sync_labels.sh --check
```

Exit 0: ready. Exit 1: some are missing or drifted, listed as `CREATE` or `UPDATE`. Through the connector, compare a label listing against the `- name:` lines in `.github/labels.yml` instead.

If any are missing, offer to create them, and do it only on a yes:

- with `gh`: `bash scripts/sync_labels.sh`
- through the connector: run the "Sync labels" workflow (`actions_run_trigger`, method `run_workflow`, workflow `label-sync.yml`, ref = the default branch). It needs `label-sync.yml` on the default branch; if it is not there yet, say the labels arrive when it is pushed to `main`.

If `scripts/sync_labels.sh` or `.github/labels.yml` is missing, this repo has not got the issue setup yet. Say so, and point to the `sync-from-template` skill, which brings it in.

## 3. Capture — "note this for later"

1. **Duplicates.** Look through open and closed issues for the same thing (the duplicate listing above, or `search_issues`). If one exists, show it and ask whether to add to it instead.
2. **Type.** Exactly one: `type:bug` (broken), `type:feature` (new), `type:improvement` (refines what works), `type:debt` (refactor, cleanup, infra), `type:idea` (not yet shaped). If two fit, ask.
3. **Status.** `status:backlog`, unless the user says it is next (`status:planned`) or they are doing it now (`status:in-progress`).
4. **Priority.** Only if the user stated one, mapped to `p0:critical`, `p1:high`, `p2:medium` or `p3:low`.
5. **Title.** Starts with a verb, at the scope the user gave: "Fix export on Safari", not "Safari bug" and not "Rework the export pipeline".
6. **Body.** The sections of that type's form, in order, then `### Source`:

   | Type | Sections |
   |---|---|
   | feature, improvement | Context · User story · Acceptance criteria · Dependencies |
   | bug | What happened · Expected · Steps to reproduce · Acceptance criteria · Dependencies |
   | debt | Context · Proposed change · Acceptance criteria · Dependencies |
   | idea | The idea · Why it might matter |

   Fill each from what the user said and what this session showed (an error message, a file and line). Anything else is `_Not yet known_`. Acceptance criteria are `- [ ] ` checkboxes; write them only from what the user said "done" means, otherwise leave one `- [ ] _Not yet known_`. Dependencies are `#N` references, or empty. `### Source` is `Requested in conversation, YYYY-MM-DD` (from `date +%F`), plus any file or commit it came from.
7. **One request, one issue.** If it reads like several pieces of work, ask before splitting.

Show the draft — title, labels, body — and file it on a yes. Report `#N` and its URL.

## 4. Look up — "what's open?", "what's next?"

Fetch the open issues as JSON and run the checker on them:

```bash
gh api 'repos/{owner}/{repo}/issues?state=open&per_page=100' --paginate \
  | bash scripts/check_issues.sh -
```

Through the connector, page through `list_issues` (state open), save all pages as one JSON array in the scratchpad, and pass that file instead of `-`.

The inventory is already in pick-up order. Show In progress, then Planned, then Blocked, then a count of Backlog, each as `#N title [priority]`. "What's next" is the first In progress, else the first Planned. Mention any flag on an issue you show; the full list of flags is `/roadmap evaluate`'s job.

## 5. Start, block, unblock

A direct request ("start #12", "#12 is blocked on the vendor") is its own confirmation: make the change, then report it.

- **Start.** Read the issue and its comments first. Remove its current `status:*` label and add `status:in-progress`, so it has exactly one. Then suggest a branch in this repo's naming (`/branch-workflow`), and say the PR should carry `Closes #N` so merging it closes the issue.
- **Block.** Swap to `status:blocked`. Add what it waits on under `### Dependencies` in the body (`#N`, or a short reason with today's date), because `check_issues.sh` reads it there, and comment with the same reason so the history shows when.
- **Unblock.** Swap back to `status:planned`, or `status:in-progress` if work had started.

## 6. Close

Merging a PR that carries `Closes #N` closes the issue, with the PR as its evidence; prefer that.

To close by hand, draft a comment naming the evidence: a merged PR, a commit (`git log --oneline -n 20`), or the file that implements it. If any acceptance checkbox is still unticked, say which and ask. On a yes, comment and close as *completed*.

"Drop it" or "won't do" closes as *not planned*, with a one-line reason comment. Never close without a comment.

## 7. Proof

After every write, read the issue back and show its number, title, labels, state and URL. A label that did not stick, or two `status:*` labels, is fixed before moving on.

## Out of scope

- Filing more than a handful of issues at once from the project's goals, auditing the whole backlog, or converting `docs/plans/ROADMAP.md`: that is `/roadmap`, which the user starts by typing it.
- Milestones: assign one only when the user names it, and never create one unasked.
- Rewriting someone else's issue text beyond what the user asked to change.
