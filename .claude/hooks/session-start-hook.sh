#!/usr/bin/env bash
# session-start-hook.sh
#
# Runs when a Claude Code session opens, via the SessionStart entry in
# .claude/settings.json. Answers one question before any work starts: can this
# session actually run the project's tests?
#
# That is the failure this exists to prevent — a session begins, work proceeds
# for twenty minutes, and the test command turns out to have never been
# installed. The cost of knowing at second zero is one line of output.
#
# What it deliberately does NOT do:
#
#   - Install anything. A hook cannot be declined, so it must not mutate the
#     working tree, touch a lockfile, or spend a minute of every session on a
#     package manager. It reports; you decide.
#   - Fail the session. It always exits 0. A setup warning is not a reason to
#     stop someone working.
#   - Say anything when all is well. A hook that prints on every session
#     becomes noise that everyone learns to scroll past.
#
# Stack-agnostic by reading the project's own config rather than guessing:
# TEST_COMMAND comes from the `## Session Config` table in CLAUDE.md, so the
# check adapts to whatever stack init-project scaffolded.
#
# Usage:
#   session-start-hook.sh [project_root]    # defaults to the repo root
#
# Exit status: always 0.

set -uo pipefail

if [[ -n "${1:-}" ]]; then
  ROOT="$1"
else
  ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi

[[ -d "$ROOT" ]] || exit 0

CLAUDE_MD="$ROOT/CLAUDE.md"

note() { printf 'session-start-hook: %s\n' "$1"; }

# A repo that has not been through init-project still carries placeholders.
# Nothing here is actionable yet, and nagging about it every session would make
# the hook worth ignoring by the time it has something real to say.
if [[ -f "$CLAUDE_MD" ]] && grep -q '{set by init-project' "$CLAUDE_MD"; then
  exit 0
fi

# ---------------------------------------------------------------- node modules
# Cheap and unambiguous: a manifest with no installed tree.
if [[ -f "$ROOT/package.json" && ! -d "$ROOT/node_modules" ]]; then
  note "package.json is present but node_modules/ is not — run your install step before the tests will pass."
fi

# --------------------------------------------------------------- test command
# Pull TEST_COMMAND out of the Session Config table and check that the binary it
# starts with is actually on PATH.
#
# Wrappers are skipped on purpose. `npm test` starts with `npm`, which resolves
# whether or not the project's dependencies exist, so a PATH check on it proves
# nothing — the node_modules check above covers that case instead. Warning on a
# wrapper would be a false positive, and false positives are how a hook earns
# its way into being ignored.
if [[ -f "$CLAUDE_MD" ]]; then
  test_command="$(
    awk '
      /^## Session Config/ { inside = 1; next }
      inside && /^## / { exit }
      inside && /TEST_COMMAND/ { print; exit }
    ' "$CLAUDE_MD"
  )"

  # Strip the markdown table scaffolding, then take the first bare word.
  binary="$(
    printf '%s\n' "$test_command" \
      | sed -e 's/.*TEST_COMMAND[^|]*|//' -e 's/|.*$//' -e 's/`//g' \
      | awk '{print $1}'
  )"

  case "$binary" in
    ''|npm|npx|yarn|pnpm|bun|python|python3|uv|uvx|poetry|pipenv|make|go|cargo|dotnet|mvn|gradle|bundle|rake)
      : # wrapper or nothing to check
      ;;
    *)
      if ! command -v "$binary" >/dev/null 2>&1; then
        note "TEST_COMMAND starts with '$binary', which is not on PATH — dependencies may not be installed."
      fi
      ;;
  esac
fi

exit 0
