---
name: dependabot-pr-consolidator
description: Automate triage and safe consolidation of open Dependabot pull requests in a Git repository using the GitHub CLI (gh). Categorizes each open Dependabot PR by SemVer risk (Major, Minor/Patch, Grouped, or Unknown), cross-checks runtime/base-image bumps against versions pinned elsewhere in the repo, bundles the safe Minor/Patch bumps onto one consolidated branch and PR, and closes the superseded originals only once that PR is verified - while always leaving Major version bumps and Dependabot's own grouped updates open for manual review. Use this skill whenever the user mentions Dependabot PRs, dependency bumps, consolidating or batching dependency updates, cleaning up a repo's open PR queue, or asks to triage, merge, or close a pile of automated dependency pull requests - even if they don't say "Dependabot" by name.
compatibility: Requires a local git checkout of the target repo, the GitHub CLI (gh) authenticated with repo write access, and Python 3 for the bundled categorization script. Designed for an agent runtime with bash tool access, such as Claude Code.
---

# Dependabot PR consolidator

## Purpose

Open Dependabot PRs pile up fast. Most are low-risk minor or patch bumps
that are safe to batch together. Some are major version bumps that need a
human to read the changelog. This skill sorts the pile, merges what is
safe into one PR, and leaves the rest open with a clear reason.

## Non-negotiable constraints

> [!IMPORTANT]
> Never combine or stack two or more Major version bumps into the
> consolidated PR. A Major bump always stays on its own, open, for manual
> review - even if it looks trivial.

> [!IMPORTANT]
> Never merge anything directly. This skill only creates the consolidated
> PR, enables GitHub's native auto-merge on it, or closes a superseded PR.
> Auto-merge still waits for the repo's required status checks and
> reviews before it merges anything - it does not bypass CI.

> [!IMPORTANT]
> This skill's job is consolidating dependency bumps, nothing more. If
> the consolidated PR's CI fails, do not push new commits to fix it -
> even a one-line formatting fix, even an issue you're confident is
> unrelated to the bump. Report the failure and let the user decide
> (Phase 7). Fixing unrelated repo issues was never in the plan the user
> confirmed in Phase 5, and doing it anyway is exactly the kind of scope
> creep this skill exists to avoid.

## Phase 0: Preflight

Run these checks before reading any PRs. Stop and report if a check
under "Must pass" fails. Checks under "Informational" never block -
record the result and carry it into the plan.

**Must pass:**

1. Confirm `gh auth status` succeeds. If it does not, tell the user to run
   `gh auth login` and stop here.
2. Confirm the local working tree is clean (`git status --porcelain`
   prints nothing). If it is not, stop and ask the user to commit or
   stash their changes first. This skill creates and switches branches,
   and it should never risk carrying the user's uncommitted work onto a
   branch that later gets deleted on conflict.

**Informational:**

3. Resolve the repo's default branch:
   ```bash
   DEFAULT_BRANCH=$(gh repo view --json defaultBranchRef -q .defaultBranchRef.name)
   ```
4. Check whether the repo allows auto-merge at all:
   ```bash
   gh repo view --json autoMergeAllowed -q .autoMergeAllowed
   ```
   The exact field name has varied across `gh` versions - if this
   command errors, don't fail preflight over it; just note that
   auto-merge availability is unknown and will be confirmed when Phase 6
   tries to enable it. If it returns `false`, carry that forward: the
   plan in Phase 4 should say up front that any consolidated PR will
   need a manual merge, instead of that surfacing as a surprise mid-run.

## Phase 1: Query open Dependabot PRs

```bash
gh pr list --author "app/dependabot" --state open \
  --json number,title,url,headRefName --limit 100
```

Use `--limit 100` explicitly. The default limit is 30, and a repo with a
larger backlog will silently look empty past that.

> [!NOTE]
> If this returns nothing but the user is sure PRs exist, retry with
> `--author "dependabot[bot]"` - the accepted author string has varied
> across `gh` versions and GitHub Apps configurations.

If the query returns an empty list, respond exactly:

```
No open Dependabot PRs found. Exiting.
```

Stop here in that case. Do not proceed to later phases.

## Phase 2: Categorize each PR by SemVer risk

Pipe the JSON from Phase 1 into the bundled script:

```bash
gh pr list --author "app/dependabot" --state open \
  --json number,title,url,headRefName --limit 100 \
  | python3 scripts/categorize_prs.py
```

Use the script rather than eyeballing version numbers or titles by hand.
It handles several cases that are easy to get wrong under time pressure:

- A pre-1.0 package (`0.x.y`) has no stability guarantee below `1.0`, so
  a change in the minor number is scored `Major`, not `Minor/Patch`.
- GitHub Actions are commonly tagged `v2`, `v3` rather than `2.0.0` - the
  script strips the leading `v` before comparing.
- A repo that pairs Dependabot with a Conventional-Commits release tool
  (release-please, semantic-release) often prefixes PR titles with
  `chore(deps): ` or similar. The script strips that prefix before
  matching, so it doesn't need to be normalized by hand mid-run.
- A requirement-range widening (`Update mcp requirement from <2,>=1.28.1
  to >=1.28.1,<3`) is a distinct Dependabot title shape with no single
  old/new version to compare - it is always `Unknown`, never guessed at.

`Grouped` PRs are Dependabot's own grouped-update PRs. Leave these alone
rather than decomposing them - Dependabot already tested that combination
of packages together, and picking it apart forfeits that guarantee.

`Unknown` PRs are anything the script could not confidently classify.
Route these to manual review. Never guess a risk level for a title or
version string the script did not recognize.

## Phase 3: Cross-reference check for runtime and base-image bumps

> [!WARNING]
> `Minor/Patch` from Phase 2 describes the *package's own* SemVer
> promise. It does not know whether this specific repo can tolerate the
> change. Do not treat it as a final answer for every kind of package.

This matters most for language runtimes and Docker base images - `python`,
`node`, `ruby`, or any package whose title contains `-slim`, `-alpine`, or
a bare version tag rather than an application dependency. These often
look like an ordinary minor bump while conflicting with a version pinned
somewhere else in the repo: a linter's `target-version`, a type checker's
configured version, a `.python-version` / `.nvmrc` / `.tool-versions`
file, or a CI workflow that hardcodes the version.

For each `Minor/Patch` PR that bumps a runtime or base image (not a
regular library), search the repo before trusting the classification:

```bash
grep -rn -E "python[_-]?version|target-version|pythonVersion|FROM python" \
  --include='*.toml' --include='*.md' --include='Dockerfile*' \
  --include='.python-version' .
```

(Adjust the pattern to the runtime in question - `node`/`.nvmrc`,
`ruby`/`.ruby-version`, and so on.) If any match pins a version that the
bump's target would violate, downgrade that PR's disposition to
`Unknown`, and name the conflicting file and setting in the reason (for
example: "conflicts with `pyproject.toml`'s `ruff target-version =
\"py312\"` and `pyright pythonVersion = \"3.12\"`").

This is a heuristic, not a guarantee - it only catches conflicts a grep
can find, not deeper semantic incompatibilities. When a bump changes a
language, runtime, or base image and you are not confident this check
was thorough, lean toward `Unknown` rather than `Minor/Patch`.

Ordinary application libraries (an npm package, a Python package that
isn't the interpreter itself, a GitHub Action) don't need this check -
their version numbers aren't referenced elsewhere in the repo the way a
pinned toolchain version is.

## Phase 4: Build the consolidation plan

From the categorized (and cross-checked) list, build a plan:

- **Include** every PR still classified `Minor/Patch` after Phase 3.
- **Leave open** every `Major`, `Grouped`, and `Unknown` PR, each with its
  reason.

Present this plan to the user as a short table or list before touching
any repository state - PR number, package, version change, category, and
what will happen to it. Include the Phase 0 auto-merge finding here too,
if it came back `false`. This is the point to catch anything that looks
off before Phase 5 starts making changes.

If no PR qualifies for `Minor/Patch`, say so plainly, list every PR under
"left open" with its reason, and stop. Do not create an empty
consolidated PR.

**If exactly one PR qualifies**, say so explicitly and flag that
consolidating it adds no real value: it just closes that PR and reopens
the identical change under a new branch and PR number, for no reduction
in PR count. Propose the simpler alternative instead - enable auto-merge
directly on that PR, with no new branch:

```bash
gh pr merge <NUM> --auto --squash
```

If repo-level auto-merge is unavailable (per Phase 0), say so and leave
the single PR open for a manual merge; a consolidation branch would add
process without adding any capability the original PR doesn't already
have. Skip Phases 6-7 entirely in the single-PR case either way.

**If two or more PRs qualify**, proceed to Phase 5 for the full
consolidation flow.

## Phase 5: Confirmation gate

> [!IMPORTANT]
> Do not proceed past this point until the user confirms. Phase 6
> creates a branch and a PR and attempts auto-merge; Phase 7 later closes
> other PRs - all real, visible changes to the repo's PR queue. Show the
> plan from Phase 4, then ask the user to reply **"consolidate"** to
> proceed, or tell you what to change.

Skip this gate only if the user's own request already made unattended
execution explicit (for example, "run this end to end, no need to check
in" or an equivalent instruction given before this skill started). When
in doubt, gate it.

## Phase 6: Build the consolidated branch and open the PR

```bash
TIMESTAMP=$(date +%s)
BRANCH="chore/consolidate-dependabot-updates-${TIMESTAMP}"
git fetch origin "$DEFAULT_BRANCH"
git switch -c "$BRANCH" "origin/$DEFAULT_BRANCH"
```

For each included PR, in ascending PR number order (deterministic, and
matches the order Dependabot opened them):

```bash
git fetch origin "pull/${NUM}/head"
BASE=$(git merge-base HEAD FETCH_HEAD)
git cherry-pick "${BASE}..FETCH_HEAD"
```

Cherry-picking the commit range (not just the tip commit) handles the
rare Dependabot PR with more than one commit, while still applying only
that PR's own changes.

**On any conflict**: abort immediately (`git cherry-pick --abort`),
switch back to `$DEFAULT_BRANCH`, delete the temporary branch
(`git branch -D "$BRANCH"`), and tell the user which package's PR
conflicted. Do not try to resolve it or skip that PR and continue -
the whole batch was presented to the user as one plan in Phase 4, so
partially applying it would ship something they did not approve. Nothing
has been pushed at this point, so the original PRs are untouched.

Push the branch and open the PR:

```bash
git push -u origin "$BRANCH"
PR_URL=$(gh pr create \
  --base "$DEFAULT_BRANCH" \
  --head "$BRANCH" \
  --title "chore: consolidate N safe Dependabot updates" \
  --body "$CONSOLIDATION_BODY")
NEW_PR_NUMBER=$(basename "$PR_URL")
```

Build `CONSOLIDATION_BODY` as a short list of the included PRs (number,
package, version change) so the consolidated PR is traceable back to the
originals it replaces.

Attempt auto-merge:

```bash
gh pr merge "$NEW_PR_NUMBER" --auto --squash
```

If this fails because the repo doesn't allow auto-merge, that's not a
fatal error - note it plainly (the consolidated PR still reduces N
originals to one PR to review) and carry the "needs a manual merge"
status into Phase 7 and the final report.

**Do not close any original PR in this phase**, regardless of whether
auto-merge was enabled. See Phase 7 for why.

## Phase 7: Verify before closing superseded PRs

> [!IMPORTANT]
> Close a superseded original only once the consolidated PR is confirmed
> good - not immediately after creating it. A PR that's closed here and
> then needs to be abandoned later (failed CI, a legitimate objection
> found in review) has no automatic way back: Dependabot doesn't reliably
> reopen a PR it wasn't the one to close, so the "safe" update is
> effectively dropped until someone notices and re-proposes it. Closing
> early trades a real safety net for a cosmetic PR-count reduction.

Check the consolidated PR's status:

```bash
gh pr checks "$NEW_PR_NUMBER"
```

- **CI is green, and no automated reviewer (Copilot or similar) has
  raised an unresolved objection**: close the superseded originals now.
  ```bash
  gh pr close "$NUM" --comment "Superseded by consolidated PR #${NEW_PR_NUMBER}."
  ```
  Only close PRs that actually made it into the consolidated branch in
  Phase 6. A PR left open in Phase 4 stays open - it was never
  superseded.
- **CI is still running**: don't block the session waiting on it. If the
  runtime supports scheduling a follow-up (a reminder, a scheduled
  task), use it to check back later; otherwise, report that closure is
  pending on CI and needs a follow-up check. Leave the originals open in
  the meantime.
- **CI fails**: do not push a fix, per the constraint at the top of this
  skill - not even for a change you're confident is unrelated to the
  bump. Report the failure plainly, note whether it looks related to the
  dependency bump itself or to something pre-existing, and leave the
  superseded originals open. Ask the user how they want to proceed
  (fix and retry, close the consolidated PR, or handle case by case).
- **An automated reviewer raises a substantive objection** (for example,
  a version now conflicts with something pinned elsewhere in the repo,
  echoing the check in Phase 3): treat this the same as a CI failure -
  don't push a fix unilaterally. Surface the finding to the user and get
  their decision before closing anything or taking further action on the
  consolidated PR.

## Edge cases

- **No Dependabot PRs found**: respond exactly `No open Dependabot PRs
  found. Exiting.` and stop (Phase 1).
- **Working tree not clean**: stop before Phase 1 and ask the user to
  commit or stash first (Phase 0).
- **GitHub CLI not authenticated**: instruct the user to run
  `gh auth login` and stop execution (Phase 0).
- **Repo-level auto-merge disabled**: not fatal. Note it in the plan
  (Phase 4) and again in the final report - the consolidated PR (or
  single qualifying PR) will need a manual merge (Phase 6).
- **Only one PR qualifies as Minor/Patch**: skip the consolidation
  branch; enable auto-merge directly on that PR instead (Phase 4).
- **Git conflict while cherry-picking**: abort, delete the temporary
  branch, and name the conflicting package (Phase 6). Nothing has been
  pushed or closed, so originals are untouched and safe to retry
  individually.
- **`gh pr create` or `gh pr merge` fails** for another reason (branch
  protection, missing required reviews): report the exact `gh` error.
  Do not close any original PRs - they are only superseded once the
  consolidated PR actually exists and has been verified (Phase 7).
- **CI fails on the consolidated PR, or an automated reviewer objects**:
  report it and ask the user - do not push additional commits to force
  it green (see the constraint at the top of this skill). Leave
  superseded originals open until the user decides (Phase 7).
- **A Minor/Patch bump is a language runtime or Docker base image**: run
  the Phase 3 cross-reference check before including it. A version
  pinned elsewhere in the repo (linter config, `.tool-versions`, docs)
  can make a textually "safe" bump unsafe for this specific project.

## Output format

On completion, report using this structure, including only the lines
that apply to this run:

```markdown
- **Consolidated PR Created:** #102 (chore/consolidate-dependabot-updates-1715000000) - *Auto-merge enabled*
- **PRs Closed:** #98 (setup-buildx-action), #99 (python-runtime)
- **PRs Left Open:** #97 (setup-qemu-action v2 to v3 - Major bump)
```

Other lines to use when they apply, in place of or alongside the above:

```markdown
- **Consolidated PR Created:** #10 (chore/consolidate-dependabot-updates-1788011404) - *Auto-merge unavailable (repo setting disabled); manual merge required*
- **PRs Pending Closure:** #4 (python 3.12-slim to 3.14-slim) - closes once #10's CI is green
- **Auto-merge Enabled Directly:** #4 (python 3.12-slim to 3.14-slim) - *no consolidation branch created; only one PR qualified*
- **Needs Your Decision:** #10 - CI failed on "Lint, format, test"; not auto-fixed (out of scope for this skill). Original PR #4 left open pending your call.
```

If no PRs qualified for consolidation (Phase 4), skip the first line and
report only what was left open and why.

## Tone

Professional, cautious, and exact. The person reading this is the
engineer who invoked the skill and will act on what it reports -
state what happened, not what might have happened.

## Bundled script

`scripts/categorize_prs.py` - reads a JSON array of PRs from stdin
(the shape `gh pr list --json number,title,url,headRefName` produces)
and prints the same array back with `package`, `old_version`,
`new_version`, `category`, and `reason` added to each entry. Pure
classification - it never touches git or the network, and it never sees
repo contents (that's Phase 3's job). Run it directly against synthetic
input to sanity-check its rules before trusting it against a real PR
queue.
