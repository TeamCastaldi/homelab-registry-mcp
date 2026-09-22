#!/usr/bin/env bash
# test_session-start-hook.sh
#
# Tests for session-start-hook.sh. A hook runs unattended on every session, so
# the cases that matter most are the ones where it must stay SILENT — a hook
# that cries wolf is one nobody reads by the time it is right.
#
# Usage:
#   test_session-start-hook.sh
#
# Exit status: 0 all passed, 1 at least one failure.

# The fixtures below are markdown, not shell: backticks inside single quotes are
# code spans written verbatim into a CLAUDE.md, never command substitution.
# shellcheck disable=SC2016

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$HERE/session-start-hook.sh"

pass=0
fail=0

# Indent captured output so a failure's detail reads as a block under its
# heading. SC2001 suggests ${var//from/to}, which cannot anchor per line.
# shellcheck disable=SC2001
indent() { sed 's/^/    /' <<<"$1"; }

# config <dir> <test-command> — write a CLAUDE.md carrying a Session Config table.
config() {
  printf '# CLAUDE.md\n\n## Session Config\n\n| Value | Setting |\n|---|---|\n| `TEST_COMMAND` | %s |\n\n## Code style\n\nx\n' \
    "$2" > "$1/CLAUDE.md"
}

fixture() { mktemp -d -t session-hook-test.XXXXXX; }

# assert_says <name> <dir> <substring expected in output>
assert_says() {
  local name="$1" dir="$2" needle="$3" out
  out="$("$SUT" "$dir" 2>&1)"
  if grep -qF "$needle" <<<"$out"; then
    pass=$((pass + 1))
  else
    fail=$((fail + 1))
    echo "FAIL: $name"
    echo "  expected output containing: $needle"
    echo "  got:"
    indent "$out"
  fi
  rm -rf "$dir"
}

# assert_silent <name> <dir>
assert_silent() {
  local name="$1" dir="$2" out
  out="$("$SUT" "$dir" 2>&1)"
  if [[ -z "$out" ]]; then
    pass=$((pass + 1))
  else
    fail=$((fail + 1))
    echo "FAIL: $name — expected silence, got:"
    indent "$out"
  fi
  rm -rf "$dir"
}

# ------------------------------------------------------------- never blocks

d="$(fixture)"; config "$d" '`definitely-not-a-real-binary-xyz`'
if "$SUT" "$d" >/dev/null 2>&1; then
  pass=$((pass + 1))
else
  fail=$((fail + 1)); echo "FAIL: must exit 0 even when warning"
fi
rm -rf "$d"

d="$(fixture)"; rm -rf "$d"
if "$SUT" "$d" >/dev/null 2>&1; then
  pass=$((pass + 1))
else
  fail=$((fail + 1)); echo "FAIL: must exit 0 on a missing directory"
fi

# --------------------------------------------------------- uninitialized repo

# The template itself, pre-scaffolding. Nothing here is actionable.
d="$(fixture)"; config "$d" '{set by init-project — e.g. "pytest tests/ -v"}'
printf '{}\n' > "$d/package.json"
assert_silent "stays quiet on an un-initialized repo, even with a manifest" "$d"

# --------------------------------------------------------------- node modules

d="$(fixture)"; config "$d" '`npm test`'; printf '{}\n' > "$d/package.json"
assert_says "warns when package.json has no node_modules" "$d" "node_modules/ is not"

d="$(fixture)"; config "$d" '`npm test`'; printf '{}\n' > "$d/package.json"; mkdir "$d/node_modules"
assert_silent "quiet once node_modules exists" "$d"

d="$(fixture)"; config "$d" '`bash -c true`'
assert_silent "quiet with no package.json at all" "$d"

# -------------------------------------------------------------- test command

d="$(fixture)"; config "$d" '`definitely-not-a-real-binary-xyz tests/ -v`'
assert_says "warns when the test binary is not on PATH" "$d" "not on PATH"

d="$(fixture)"; config "$d" '`bash tests/run.sh`'
assert_silent "quiet when the test binary resolves" "$d"

# Wrappers resolve regardless of whether project dependencies exist, so a PATH
# check on them proves nothing and would fire constantly.
for wrapper in npm npx yarn pnpm python3 poetry uv make go cargo bundle; do
  d="$(fixture)"; config "$d" "\`$wrapper test\`"
  assert_silent "skips the '$wrapper' wrapper rather than false-positive" "$d"
done

d="$(fixture)"; printf '# CLAUDE.md\n\nNo session config here.\n' > "$d/CLAUDE.md"
assert_silent "quiet when CLAUDE.md has no Session Config section" "$d"

d="$(fixture)"
assert_silent "quiet when there is no CLAUDE.md at all" "$d"

# ---------------------------------------------------------------------- summary

echo "---"
echo "passed=$pass failed=$fail"
(( fail == 0 ))
