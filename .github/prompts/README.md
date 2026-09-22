# GitHub Copilot Prompt Files

Reusable prompt files for structured development workflows with GitHub Copilot (and compatible LLM assistants). These files live here because VS Code reads `.github/prompts/` as the workspace prompt library.

As of the project-template 2.0.0 sync, most of this repo's Copilot prompt workflows moved to `.claude/commands/` (slash commands) and `.claude/skills/` (see that template's `CHANGELOG.md` `## [2.0.0]` entry). `session-start.prompt.md`, `branch-workflow.prompt.md`, `create-commit.prompt.md`, `sync-template.prompt.md`, and `troubleshoot.prompt.md` were removed here as a result — their jobs are now `/session-start`, `/branch-workflow`, `/commit-msg`, `/sync-template`, and the `troubleshooting` skill, respectively. Only `ansible-planned-rollout.prompt.md` remains: it has no Claude Code equivalent yet.

## Configuration

Each prompt file has a `## Config` section at the top. Fill these in once when you set up the repo — the prompts reference them throughout.

## Available Prompts

### `ansible-planned-rollout.prompt.md`
Gated Ansible capability-planning workflow. Scans real vs. projected Ansible capability (what ships today against what ADR-001 originally projected and ADR-012 later withdrew), interviews the user for design decisions, saves a plan to `docs/plans/`, executes it as atomic Conventional Commits, opens a PR, resolves feedback, and hands back a manual test checklist.

## How to use

1. Open Copilot Chat in VS Code
2. Attach the prompt file
3. Type the usage command shown in each prompt
4. Follow the gated workflow

## Adding new prompts

Naming convention: `{purpose}.prompt.md`

Include at the top:
- `description:` one line explaining what it does
- `## Config` section with project-specific values to fill in
