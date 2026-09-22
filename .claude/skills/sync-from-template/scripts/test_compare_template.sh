#!/usr/bin/env bash
# test_compare_template.sh
#
# Tests for compare_template.sh. Builds a throwaway git repo to stand in for the
# template remote (a local path clones fine) plus a plain directory for the
# project, then asserts on the STATUS lines the script emits.
#
# Usage:
#   test_compare_template.sh
#
# Exit status: 0 all passed, 1 at least one failure.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$HERE/compare_template.sh"

pass=0
fail=0

# Indent captured output so a failure's detail reads as a block under its
# heading. SC2001 suggests ${var//from/to}, which cannot anchor per line.
# shellcheck disable=SC2001
indent() { sed 's/^/    /' <<<"$1"; }

WORK="$(mktemp -d -t compare-template-test.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

# Stand-in template remote: a real repo on disk with one branch.
TEMPLATE="$WORK/template"
mkdir -p "$TEMPLATE/.claude/skills/demo"
printf 'same\n'      > "$TEMPLATE/.claude/skills/demo/SKILL.md"
printf 'template\n'  > "$TEMPLATE/.claude/skills/demo/changed.md"
printf 'brand new\n' > "$TEMPLATE/.claude/skills/demo/added.md"
# Root-level, and deliberately outside the synced path: the script must read it
# from the commit, since the sparse checkout does not guarantee it on disk.
printf '2.1.0\n'     > "$TEMPLATE/.template-version"
git -C "$TEMPLATE" init --quiet --initial-branch=main
git -C "$TEMPLATE" add -A
git -C "$TEMPLATE" \
  -c user.email=test@example.invalid -c user.name=test \
  commit --quiet -m "fixture"

# Project side: one file identical, one differing, one absent, one extra.
PROJECT="$WORK/project"
mkdir -p "$PROJECT/.claude/skills/demo"
printf 'same\n'    > "$PROJECT/.claude/skills/demo/SKILL.md"
printf 'project\n' > "$PROJECT/.claude/skills/demo/changed.md"
printf 'mine\n'    > "$PROJECT/.claude/skills/demo/local.md"
printf '1.4.0\n'   > "$PROJECT/.template-version"

OUT="$("$SUT" "$TEMPLATE" main "$PROJECT" .claude 2>&1)" || true

# check <name> <expected line>
check() {
  local name="$1" needle="$2"
  if grep -qF "$needle" <<<"$OUT"; then
    pass=$((pass + 1))
  else
    fail=$((fail + 1))
    echo "FAIL: $name"
    echo "  expected output to contain: $needle"
    echo "  got:"
    indent "$OUT"
  fi
}

check "identical file reports SAME"        "$(printf 'SAME\t.claude/skills/demo/SKILL.md')"
check "differing file reports CHANGED"     "$(printf 'CHANGED\t.claude/skills/demo/changed.md')"
check "template-only file reports NEW"     "$(printf 'NEW\t.claude/skills/demo/added.md')"
check "project-only file reports LOCAL_ONLY" "$(printf 'LOCAL_ONLY\t.claude/skills/demo/local.md')"
check "emits TEMPLATE_SHA" "TEMPLATE_SHA="
check "emits TEMP_CLONE"   "TEMP_CLONE="

# The version markers let the sync skill report a version gap, not just a diff.
# The template's marker lives outside the synced path, so reading it proves the
# script consults the commit rather than the sparse working tree.
check "reads the template's version"  "TEMPLATE_VERSION=2.1.0"
check "reads the project's version"   "PROJECT_VERSION=1.4.0"

# Clean up the clone the script deliberately leaves behind.
clone_path="$(sed -n 's/^TEMP_CLONE=//p' <<<"$OUT" | head -n1)"
[[ -n "$clone_path" && -d "$clone_path" ]] && rm -rf "$clone_path"

# An unversioned project reports "unknown" rather than failing — that is the
# state of every project scaffolded before the template carried a version.
rm -f "$PROJECT/.template-version"
OUT="$("$SUT" "$TEMPLATE" main "$PROJECT" .claude 2>&1)" || true
check "unversioned project reports unknown" "PROJECT_VERSION=unknown"
clone_path="$(sed -n 's/^TEMP_CLONE=//p' <<<"$OUT" | head -n1)"
[[ -n "$clone_path" && -d "$clone_path" ]] && rm -rf "$clone_path"

# Too few arguments is a usage error.
if "$SUT" only two 2>/dev/null; then
  fail=$((fail + 1))
  echo "FAIL: too few arguments should be a usage error"
else
  pass=$((pass + 1))
fi

echo "---"
echo "passed=$pass failed=$fail"
(( fail == 0 ))
