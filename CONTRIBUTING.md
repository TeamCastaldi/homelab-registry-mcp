# Contributing

Thanks for your interest in contributing to `homelab-registry-mcp`. This document
covers the expected workflow.

## Setup

```bash
uv sync                 # install/sync dependencies (run after every pull)
uv run registry-mcp     # start the server (stdio by default)
uv run pytest           # run the test suite
```

See [CLAUDE.md](CLAUDE.md) for the full command list, architecture, environment
variables, and conventions.

## Workflow

No issue required. If you spot something wrong or missing, go straight to a branch.

### 1. Branch

Use the branch-workflow prompt (`.github/prompts/branch-workflow.prompt.md`) or
follow the naming convention directly:

```
feature/short-description
fix/short-description
docs/short-description
refactor/short-description
test/short-description
experiment/short-description
```

Keep branch names short and descriptive.

### 2. Make your changes

Work atomically — one logical change per commit. Use the create-commit prompt
(`.github/prompts/create-commit.prompt.md`) or follow Conventional Commits format
directly:

```
feat(discovery): add network probe source
fix(proposal): gate low-confidence patches as rejected
docs(readme): update quick start steps
refactor(registry): simplify reconcile matching
```

Conventional Commits format is **expected**, not optional.

### 3. Open a PR

CI runs `ruff check`, `ruff format --check`, `pytest -q`, and `ansible-lint`
(against `ansible/`) on every push, and it must pass. Run the same checks
locally before opening a PR:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
ANSIBLE_ROLES_PATH=ansible/roles ansible-lint ansible/roles ansible/playbooks
```

Fill in the PR template.

### 4. Merge

Squash or merge commit, your call.

---

## Keeping docs in sync

When you change the project structure, add a tool, or update a tooling default,
run the sync-template prompt (`.github/prompts/sync-template.prompt.md`) to check
for drift between the code and its documentation — and update [CLAUDE.md](CLAUDE.md)
when architecture, conventions, or current status change.

## Project conventions to respect

These are load-bearing — see [CLAUDE.md](CLAUDE.md) for the full list:

- **Curated fields are sacred**: `display_name`, `category`, `tags`, and `notes`
  set by humans are never overwritten by discovery.
- **Never hard-delete discovered services**: mark them `stale` after the miss
  threshold.
- **Upstream APIs are read-only**: Traefik, Authentik, and Docker are never
  modified.
- **The write path writes to Git only**: the proposal layer opens PRs; it never
  merges them and never writes the filesystem.
- **No LLM calls in the detection layer**: `reconcile.py` and discovery sources
  stay deterministic; reasoning lives in `dspy/` and is wired in via injected
  callables.
- **Register new MCP tools in `server.py`** — FastMCP does not auto-discover them.
- **Naming**: kebab-case for MCP tool names, snake_case for Python, PascalCase
  for classes.

## What's out of scope

- Changes that modify upstream Traefik / Authentik / Docker state.
- A write path that merges PRs or edits the filesystem directly.
- Stack changes without discussion (e.g. swapping SQLite/SQLModel or the MCP
  framework).
