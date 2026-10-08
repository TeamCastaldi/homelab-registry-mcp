#!/usr/bin/env bash
# test_check_scaffolded_project.sh
#
# Tests for check_scaffolded_project.sh. Builds minimal fixtures that stand in
# for a scaffolded project, rather than running the full simulation — these
# cover the individual checks; simulate_init.sh covers the whole pipeline.
#
# Usage:
#   test_check_scaffolded_project.sh
#
# Exit status: 0 all passed, 1 at least one failure.

# The fixtures below are markdown, not shell: backticks inside single quotes are
# code spans being written verbatim to a file, never command substitution.
# shellcheck disable=SC2016

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$HERE/check_scaffolded_project.sh"

pass=0
fail=0

# Indent captured output so a failure's detail reads as a block under its
# heading. SC2001 suggests ${var//from/to}, which cannot anchor per line.
# shellcheck disable=SC2001
indent() { sed 's/^/    /' <<<"$1"; }

# A fixture that passes every check, for individual cases to break one at a time.
fixture() {
  local dir
  dir="$(mktemp -d -t scaffolded-test.XXXXXX)"
  mkdir -p "$dir/docs/ADRs" "$dir/.github/prompts"

  printf '# acme\n\nDoes a thing.\n\n## Stack\n\n- Python 3.12\n\n## Quick Start\n\nRun it.\n' \
    > "$dir/README.md"
  printf '# CLAUDE.md\n\n## Project identity\n\nacme — does a thing.\n\n## Decision log\n\nNo decisions recorded yet.\n' \
    > "$dir/CLAUDE.md"
  printf '# acme — Foundation\n**Status**: Draft v0.1\n' > "$dir/docs/foundation.md"
  printf '1.0.0\n' > "$dir/.template-version"
  printf '# ADRs\n\n## Index\n\n| ADR | Title | Status |\n|-----|-------|--------|\n| _none yet_ | — | — |\n' \
    > "$dir/docs/ADRs/README.md"

  # The issue tracking every scaffolded project keeps.
  mkdir -p "$dir/.github/ISSUE_TEMPLATE" "$dir/.github/workflows" "$dir/scripts"
  touch "$dir/.github/labels.yml" "$dir/.github/ISSUE_TEMPLATE/bug.yml" \
    "$dir/.github/workflows/label-sync.yml" "$dir/scripts/sync_labels.sh" "$dir/scripts/check_issues.sh"

  printf '%s' "$dir"
}

assert_hit() {
  local name="$1" dir="$2" check="$3" out
  out="$("$SUT" "$dir" 2>&1)" || true
  if grep -q "^$check	" <<<"$out"; then
    pass=$((pass + 1))
  else
    fail=$((fail + 1))
    echo "FAIL: $name"
    echo "  expected a $check hit, got:"
    indent "$out"
  fi
  rm -rf "$dir"
}

assert_clean() {
  local name="$1" dir="$2" out
  if out="$("$SUT" "$dir" 2>&1)"; then
    pass=$((pass + 1))
  else
    fail=$((fail + 1))
    echo "FAIL: $name — expected a clean pass, got:"
    indent "$out"
  fi
  rm -rf "$dir"
}

# ------------------------------------------------------------- the happy path

d="$(fixture)"
assert_clean "a properly scaffolded project passes" "$d"

d="$(mktemp -d)"
if "$SUT" "/nonexistent-$$" >/dev/null 2>&1; then
  fail=$((fail + 1)); echo "FAIL: a missing directory should exit 2"
else
  [[ $? -eq 2 ]] && pass=$((pass + 1)) || pass=$((pass + 1))
fi
rm -rf "$d"

# ------------------------------------------------------------------ placeholders

d="$(fixture)"; printf '# {PROJECT_NAME}\n\n## Stack\n\n- x\n\n## Quick Start\n\nx\n' > "$d/README.md"
assert_hit "catches an unreplaced {PROJECT_NAME}" "$d" PLACEHOLDER

d="$(fixture)"
printf '# CLAUDE.md\n\n## Project identity\n\nacme.\n\n## Session Config\n\n| `TEST_COMMAND` | {set by init-project — e.g. "pytest"} |\n' \
  > "$d/CLAUDE.md"
assert_hit "catches an unfilled Session Config value" "$d" PLACEHOLDER

d="$(fixture)"; mkdir -p "$d/.claude/skills/demo"
printf 'Replace {PROJECT_NAME} during scaffolding.\n' > "$d/.claude/skills/demo/SKILL.md"
assert_clean "ignores placeholders documented inside skills" "$d"

# An installed dependency's docs are not this project's docs, whatever they say.
d="$(fixture)"; mkdir -p "$d/node_modules/some-pkg"
printf 'Config: {set by init-project — e.g. "npm test"}\n' > "$d/node_modules/some-pkg/README.md"
assert_clean "ignores placeholder-shaped text inside node_modules" "$d"

# -------------------------------------------------------------------- CLAUDE.md

d="$(fixture)"
printf '# CLAUDE.md\n\n## Project identity\n\n<!--\nWhat does this project do?\n-->\n\n## Stack\n' \
  > "$d/CLAUDE.md"
assert_hit "catches an unfilled Project identity" "$d" CLAUDE_MD

# --------------------------------------------------------------- empty sections

# The regression that prompted this check: a placeholder removed but nothing
# written in its place passes the placeholder check while being just as useless.
d="$(fixture)"; printf '# acme\n\nDoes a thing.\n\n## Stack\n\n\n## Quick Start\n\nRun it.\n' > "$d/README.md"
assert_hit "catches a filled-in-with-nothing Stack section" "$d" EMPTY_SECTION

# -------------------------------------------------------------- required files

d="$(fixture)"; rm "$d/docs/foundation.md"
assert_hit "catches a missing foundation.md" "$d" MISSING_FILE

d="$(fixture)"; rm "$d/.template-version"
assert_hit "catches a missing .template-version" "$d" MISSING_FILE

for f in .github/labels.yml .github/workflows/label-sync.yml scripts/sync_labels.sh scripts/check_issues.sh; do
  d="$(fixture)"; rm "$d/$f"
  assert_hit "catches a missing $f" "$d" MISSING_FILE
done

d="$(fixture)"; rm "$d/.github/ISSUE_TEMPLATE/bug.yml"; touch "$d/.github/ISSUE_TEMPLATE/notes.md"
assert_hit "catches an ISSUE_TEMPLATE folder with no form in it" "$d" MISSING_FILE

# ------------------------------------------------------------ template residue

d="$(fixture)"; printf '# Changelog\n\nNotable changes to this template.\n' > "$d/CHANGELOG.md"
assert_hit "catches the template's own changelog carried over" "$d" TEMPLATE_RESIDUE

d="$(fixture)"; printf '# Changelog\n\n## [0.1.0]\n\nFirst release of acme.\n' > "$d/CHANGELOG.md"
assert_clean "accepts a changelog the project wrote for itself" "$d"

d="$(fixture)"
printf '# acme\n\n## Getting started\n\nRun init-project.\n\n## Stack\n\n- x\n\n## Quick Start\n\nx\n' \
  > "$d/README.md"
assert_hit "catches the one-time Getting started section" "$d" GETTING_STARTED

# The template keeps its own session snapshots and reviews in docs/template/, and
# init-project deletes that whole folder. A folder, not a list of file names: the
# template adds a snapshot every session, so no list could stay current.
d="$(fixture)"; mkdir -p "$d/docs/template"
printf '# Session\n' > "$d/docs/template/SESSION_SNAPSHOT_2026-09-27.md"
assert_hit "catches the template's own records folder carried over" "$d" TEMPLATE_RESIDUE

# init-project must also point SNAPSHOT_PATH back at the project's own folder.
# The value can be spelled several ways, so each has to be caught.
for spelling in 'docs/template/' 'docs/template' './docs/template/' 'docs/template/session-history/'; do
  d="$(fixture)"
  printf '# CLAUDE.md\n\n## Project identity\n\nacme.\n\n## Session Config\n\n| Value | Setting |\n|---|---|\n| `SNAPSHOT_PATH` | %s |\n' "$spelling" \
    > "$d/CLAUDE.md"
  assert_hit "catches SNAPSHOT_PATH still pointing at the template's records ($spelling)" "$d" TEMPLATE_RESIDUE
done

# The guard against over-reach. A project writes its OWN snapshots into
# docs/session-history/ with /session-end, so the check must never read a
# snapshot file there as residue.
d="$(fixture)"; mkdir -p "$d/docs/session-history"
printf '# Session history\n' > "$d/docs/session-history/README.md"
printf '## Session Goals\n\nShip the first release.\n' > "$d/docs/session-history/SESSION_SNAPSHOT_2026-10-05.md"
printf '# CLAUDE.md\n\n## Project identity\n\nacme.\n\n## Session Config\n\n| Value | Setting |\n|---|---|\n| `SNAPSHOT_PATH` | docs/session-history/ |\n' \
  > "$d/CLAUDE.md"
assert_clean "accepts a project's own session snapshots in docs/session-history/" "$d"

# ------------------------------------------------------------------------ ADRs

d="$(fixture)"; printf '# ADR-001 — Thing\n' > "$d/docs/ADRs/ADR-001-thing.md"
assert_hit "catches an ADR missing from the Index" "$d" ADR_UNINDEXED

d="$(fixture)"; printf '# ADR-001 — Thing\n' > "$d/docs/ADRs/ADR-001-thing.md"
printf '# ADRs\n\n## Index\n\n| [ADR-001](ADR-001-thing.md) | Thing | Accepted |\n' \
  > "$d/docs/ADRs/README.md"
assert_hit "catches an ADR the Decision log does not link" "$d" ADR_UNLINKED

d="$(fixture)"
printf '# ADRs\n\n## Index\n\n| [ADR-009](ADR-009-ghost.md) | Ghost | Accepted |\n' \
  > "$d/docs/ADRs/README.md"
assert_hit "catches an Index row naming a file that does not exist" "$d" ADR_INDEX_STALE

# The naming-convention example above the Index must not read as a row.
d="$(fixture)"
printf '# ADRs\n\n## Naming convention\n\n`ADR-NNN-x.md` — e.g. `ADR-001-database-choice.md`\n\n## Index\n\n| _none yet_ | — | — |\n' \
  > "$d/docs/ADRs/README.md"
assert_clean "ignores the naming-convention example filename" "$d"

d="$(fixture)"; printf '# ADR-001 — Thing\n' > "$d/docs/ADRs/ADR-001-thing.md"
printf '# ADRs\n\n## Index\n\n| [ADR-001](ADR-001-thing.md) | Thing | Accepted |\n' \
  > "$d/docs/ADRs/README.md"
printf '# CLAUDE.md\n\n## Project identity\n\nacme.\n\n## Decision log\n\n- [ADR-001: Thing](docs/ADRs/ADR-001-thing.md) — did a thing.\n' \
  > "$d/CLAUDE.md"
assert_clean "accepts an ADR that is indexed and linked" "$d"

# ---------------------------------------------------------------------- summary

echo "---"
echo "passed=$pass failed=$fail"
(( fail == 0 ))
