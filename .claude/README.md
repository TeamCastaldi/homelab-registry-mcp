# .claude

Claude Code configuration that travels with the repo. Any repo scaffolded from this one inherits the whole folder, so anything added here is a standing cost on projects that may have nothing to do with it.

```
.claude/
├── commands/     slash commands — workflows you invoke by name
├── hooks/        shell scripts the harness runs on a lifecycle event
├── skills/       skills — workflows Claude starts when it recognizes the situation
├── settings.json committed and shared: hooks, and any permissions
└── settings.local.json   personal, gitignored, never committed
```

## Which of the three

Everything here automates work. They differ in **who decides it runs**, and choosing wrong is what produced the mess this layout replaced.

| | Hook | Skill | Command |
|---|---|---|---|
| Decided by | The harness, on an event | Claude, on recognizing the situation | You, by typing `/name` |
| What it is | A shell script | Instructions Claude reads | Instructions Claude reads |
| Judgment | None — identical every run | Judges *when* | You pick when, Claude works out how |
| Can be declined | No | Yes | Yes |
| Can block an action | Yes | No | No |

**A command is a request. A hook is a guarantee.** Reach for a hook only when something must happen whether or not anyone remembers to ask *and* must not depend on judgment. Everything else is a skill or a command.

Note the three can share a script. `/sync-template` runs `validate_skills.sh` when you ask for an audit; a hook could run the same script after every edit. The script is the deterministic part — a command wraps it in judgment, a hook wraps it in enforcement.

## Choosing between a command and a skill

Both hold instructions for a repeatable workflow. The difference is **who decides it runs**, and getting it wrong is what produced the mess this layout replaced.

| | Skill | Command |
|---|---|---|
| Starts when | Claude recognizes the situation from your description of the work | You type `/name` |
| You must remember it exists | No | Yes |
| Triggering is | Fuzzy — competes with every other skill whose description overlaps | Exact — no ambiguity possible |
| Best for | Work you would want done even if you forgot the workflow existed | Work with a moment you choose to begin it |

**Write a skill** when the right moment is one Claude can spot and you might miss: docs going stale after a session, a pile of Dependabot PRs, a fresh clone that has never been initialized. The value is that it fires without being summoned.

**Write a command** when *you* pick the moment: starting a session, closing one out, cutting a branch, running an audit. Being summoned is the point — a command that fired on its own would be an interruption.

The test that settles most cases: *if the user never learned this existed, should it still run?* Yes means skill. No means command.

### Signs you chose wrong

- **A skill with a mode table.** If a skill routes `/this` and `/that` to different sections, it is several commands wearing one skill's clothing. Split it. The dispatcher exists only because skills cannot be invoked by name.
- **A skill that duplicates one from a marketplace.** Two descriptions competing for the same phrases resolve unpredictably. If the workflow has a moment you choose, a command sidesteps the collision entirely, because explicit invocation never competes.
- **A command nobody remembers to run.** If work keeps getting missed because a command went untyped, the moment is recognizable rather than chosen — it wanted to be a skill.

## What belongs here at all

Beyond picking the right mechanism, both share the bar in [`skills/README.md`](skills/README.md): it must be about building software rather than one person's environment, must work on a fresh clone, and must not duplicate something a marketplace already provides. A workflow that fails those does not become acceptable by being a command instead of a skill.

## Configuration values

Commands and skills that need project-specific values — test command, source root, where snapshots go — read them from the `## Session Config` section of the root `CLAUDE.md`. They are not repeated per file. That section is the single source of truth; `init-project` fills it in during scaffolding.

## Layout requirements

- A **command** is `commands/<name>.md`, invoked as `/<name>`, with YAML frontmatter carrying at least a `description`.
- A **skill** is `skills/<name>/SKILL.md` — the filename is exact, and `scripts/validate_skills.sh` enforces the rest.
- A **hook** is `hooks/<event>-hook.sh`, wired to its event in `settings.json`.

### Naming hooks

A hook file is named for the event that fires it, in kebab-case, with a `-hook` suffix: `SessionStart` becomes `hooks/session-start-hook.sh`, `PostToolUse` becomes `hooks/post-tool-use-hook.sh`.

The suffix is redundant with the folder, and deliberate anyway. This repo ships both a `/session-start` command and a `SessionStart` hook, and they do entirely unrelated jobs — one runs a mission interview when you ask for it, the other checks the environment on every session whether you asked or not. Without the suffix, "session-start" in a conversation, a grep, or a commit message is ambiguous between a thing you invoke and a thing that invokes itself. That ambiguity is expensive in exactly the moment you are debugging one of them.

### Rules every hook follows

- **Exit 0** unless it is deliberately blocking an action. A hook cannot be declined, so a non-zero exit from a hook that was only meant to advise takes the repo hostage.
- **Stay silent on success.** A hook that prints on every session becomes noise people scroll past, and then it is useless on the day it has something real to say.
- **Never mutate.** No installs, no writes to the working tree, no lockfile edits. It runs unattended and unasked; report the problem and let a person decide.
- **Stay fast.** It runs before every session. Give it a `timeout` in `settings.json` and keep it well under.
- **Ship a test.** `hooks/test_<name>-hook.sh`, covering the silent cases especially — those are the ones that decide whether anyone still trusts it.

## Settings

`settings.json` is committed, so everything in it applies to every clone. `settings.local.json` is gitignored and is where a permission you trust on your own machine belongs — putting it in the shared file imposes your judgment on people whose repo you have never seen.

The bar for the shared file is high for the same reason hooks are: nobody approves it per run.

### Permissions

The three lists are not symmetric, and that asymmetry decides what belongs in a shared file.

- **`deny` is cheap to be wrong about.** It only removes capability, so an over-broad rule costs annoyance. Ship it generously.
- **`allow` is a grant made on behalf of repos nobody here has seen.** Ship it stingily, and only for things this repo itself provides.
- **`ask` forces a prompt** without forbidding the action, which is the right shape for something occasionally necessary and never routine.

What is configured here:

**Deny** — reads of secret-bearing files: `.env` and its real-secret variants, `*.pem`, `*.p12`, SSH private keys, `credentials.json`, `.aws/credentials`, `.npmrc`, `.pypirc`. `.gitignore` stops these being *committed*; it does nothing to stop their contents entering a conversation. Note `.env.example` is deliberately **not** denied — it carries no secrets and is often the fastest way to understand a project's configuration.

**Allow** — only the read-only checks this repo ships, by exact script path. Nothing stack-specific: the template cannot know whether a project's tests are `pytest` or `vitest`, so allowlisting either would be a guess applied to every clone. `simulate_init.sh` is deliberately excluded — it writes a directory tree the caller names, which is not read-only.

**Ask** — force-push, hard reset, branch delete, `git clean -f`. These already prompt under the default permission mode; the rules are a backstop for a project that later loosens `defaultMode`, and they keep the operation *possible*, which a flat deny would not. A deny here sends someone editing settings mid-incident.

### What is and is not verified

`scripts/check_doc_claims.sh` checks that every `Bash(bash …)` allow rule names a script that still exists — an allow rule pointing at a renamed file grants nothing while still reading like a grant.

It cannot check that a matcher *matches*. A deny rule whose pattern is subtly wrong protects nothing while looking like protection, which is worse than no rule at all because it manufactures confidence. Treat these as a seatbelt, not a vault, and keep anything genuinely sensitive out of the repo rather than trusting a pattern to hide it.

Prefix matching is also order-sensitive: `Bash(git push --force *)` catches `git push --force origin main` but not `git push origin main --force`. The rules catch the conventional spelling, not every permutation.

## Workflows that moved

Kept so anyone following an old reference can find where it went. These name files that no longer exist, deliberately.

| Was | Now |
|---|---|
| `.github/prompts/init-project.prompt.md` | The `init-project` skill <!-- inherited-docs-ok --> |
| `.github/prompts/session-start.prompt.md` | The `/session-start` command <!-- inherited-docs-ok --> |
| `.github/prompts/create-commit.prompt.md` | The `/commit-msg` command <!-- inherited-docs-ok --> |
| `.github/prompts/branch-workflow.prompt.md` | The `/branch-workflow` command <!-- inherited-docs-ok --> |
| `.github/prompts/sync-template.prompt.md` | The `/sync-template` command <!-- inherited-docs-ok --> |
| `.github/prompts/troubleshoot.prompt.md` | Removed with no replacement in this repo <!-- inherited-docs-ok --> |
| The `session-manager` skill | Split into `/session-start`, `/session-end` and `/commit-msg` |

The `.github/prompts/` folder is gone. It existed for GitHub Copilot's attach-a-file behavior, which nothing in this repo depends on any more; slash commands do the same job natively and need no attaching.
