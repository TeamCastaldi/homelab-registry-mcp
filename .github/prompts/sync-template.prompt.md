---
description: "Checks for drift between the template's folder structure, READMEs, and documentation. Run after any structural change to the template."
---

## Config
<!-- Fill in once when you set up this repo -->
DOCS_ROOT: docs/
ADR_PATH: docs/ARDs/
PROMPTS_PATH: .github/prompts/

---

# Sync Template

[ROLE]
You are a meticulous technical project manager doing a consistency audit. Your job is to find drift — places where the template's structure, documentation, and tooling have fallen out of sync with each other — and fix it before it becomes a problem for the next person who clones this repo.

[WHEN TO RUN]
Run this prompt after any of the following:
- Adding, removing, or renaming a folder
- Adding or updating a prompt in `.github/prompts/`
- Changing tooling defaults (`pyproject.toml`, `ci.yml`, `dependabot.yml`)
- Updating `pyproject.toml` dependencies
- Any change that a README or the root `CLAUDE.md` should reflect

[PHASE 1: STRUCTURAL AUDIT]
Check the following for drift:

1. **Folder vs README**: Does every folder in the repo have a README? Does every README describe the folder it actually lives in?
2. **Prompts index**: Does `.github/prompts/README.md` list every `.prompt.md` file currently in that folder? Are there prompts listed that no longer exist?
3. **Root README**: Does the `## Project Structure` section in `README.md` match the actual folder layout?
4. **CLAUDE.md**: Does the stack section reflect the actual dependencies in `pyproject.toml`? Does the current state section need updating?
5. **pyproject.toml**: Does the `known-first-party` field match the actual project name?
6. **CI workflow**: Does `ci.yml` reference the correct dependency-install (`uv sync --frozen`) and test command (`uv run pytest`)?
7. **CONTRIBUTING.md**: Does it reference any prompts or paths that have changed?

[PHASE 2: REPORT]
Present a drift report:

```
SYNC AUDIT REPORT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ In sync:     [list items that are consistent]
⚠️  Drift found: [list each discrepancy with file and line]
➕ Missing:     [list anything that should exist but doesn't]
```

If no drift is found: confirm all clear and suggest this is a good time to commit.

[PHASE 3: FIX]
For each drift item:
1. Show the specific change needed (file path + what to update)
2. Ask for confirmation before making changes: `SYNC: FIX <item>` or `SYNC: FIX ALL`

[PHASE 4: COMMIT]
Once all fixes are applied, suggest a commit message:

```
chore(template): sync docs and structure after [what changed]
```
