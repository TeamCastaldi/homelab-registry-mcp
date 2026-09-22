#!/usr/bin/env bash
# test_check_inherited_docs.sh
#
# Tests for check_inherited_docs.sh. Each case builds a throwaway fixture repo,
# runs the sweep against it, and asserts on the tab-separated output.
#
# Usage:
#   test_check_inherited_docs.sh
#
# Exit status: 0 all passed, 1 at least one failure.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$HERE/check_inherited_docs.sh"

pass=0
fail=0

# Indent captured output so a failure's detail reads as a block under its
# heading. SC2001 suggests ${var//from/to}, which cannot anchor per line.
# shellcheck disable=SC2001
indent() { sed 's/^/    /' <<<"$1"; }

fixture() {
  local dir
  dir="$(mktemp -d -t inherited-docs-test.XXXXXX)"
  mkdir -p "$dir/.github/prompts"
  printf '%s' "$dir"
}

# assert_hit <name> <fixture> <expected CHECK> <expected substring in output>
assert_hit() {
  local name="$1" dir="$2" check="$3" needle="$4" out
  out="$("$SUT" "$dir" 2>&1)" || true
  if grep -q "^$check	" <<<"$out" && grep -qF "$needle" <<<"$out"; then
    pass=$((pass + 1))
  else
    fail=$((fail + 1))
    echo "FAIL: $name"
    echo "  expected a $check hit containing: $needle"
    echo "  got:"
    indent "$out"
  fi
  rm -rf "$dir"
}

# assert_no_hit <name> <fixture> <CHECK that must not appear>
assert_no_hit() {
  local name="$1" dir="$2" check="$3" out
  out="$("$SUT" "$dir" 2>&1)" || true
  if grep -q "^$check	" <<<"$out"; then
    fail=$((fail + 1))
    echo "FAIL: $name"
    echo "  expected no $check hit, got:"
    indent "$out"
  else
    pass=$((pass + 1))
  fi
  rm -rf "$dir"
}

# assert_exit <name> <fixture> <expected status>
assert_exit() {
  local name="$1" dir="$2" want="$3" got
  "$SUT" "$dir" >/dev/null 2>&1
  got=$?
  if [[ "$got" == "$want" ]]; then
    pass=$((pass + 1))
  else
    fail=$((fail + 1))
    echo "FAIL: $name — expected exit $want, got $got"
  fi
  rm -rf "$dir"
}

# ---------------------------------------------------------------- exit status

d="$(fixture)"; echo "# A project" > "$d/README.md"
assert_exit "clean repo exits 0" "$d" 0

d="$(fixture)"; echo "This is a project template." > "$d/README.md"
assert_exit "dirty repo exits 1" "$d" 1

d="$(mktemp -d)"
assert_exit "repo with no markdown exits 2" "$d" 2

# ----------------------------------------------------------- template language

d="$(fixture)"; echo "Contributing to this template is easy." > "$d/CONTRIBUTING.md"
assert_hit "detects 'this template'" "$d" TEMPLATE_LANGUAGE "CONTRIBUTING.md:1"

d="$(fixture)"; echo "A stack-agnostic starting point." > "$d/README.md"
assert_hit "detects 'stack-agnostic'" "$d" TEMPLATE_LANGUAGE "README.md:1"

d="$(fixture)"; echo "# {PROJECT_NAME}" > "$d/README.md"
assert_hit "detects unfilled {PROJECT_NAME}" "$d" TEMPLATE_LANGUAGE "README.md:1"

d="$(fixture)"; echo "Scaffolded from this template. <!-- inherited-docs-ok -->" > "$d/README.md"
assert_no_hit "inherited-docs-ok suppresses template language" "$d" TEMPLATE_LANGUAGE

d="$(fixture)"; mkdir -p "$d/.claude/skills/demo"
echo "This template ships skills." > "$d/.claude/skills/demo/SKILL.md"
echo "# A project" > "$d/README.md"
assert_no_hit "skips files under .claude/skills" "$d" TEMPLATE_LANGUAGE

# ----------------------------------------------------------------- broken links

d="$(fixture)"; echo "See [the guide](docs/nope.md)." > "$d/README.md"
assert_hit "detects a link to a missing path" "$d" BROKEN_LINK "docs/nope.md"

d="$(fixture)"; mkdir -p "$d/docs"; echo "x" > "$d/docs/real.md"
echo "See [the guide](docs/real.md)." > "$d/README.md"
assert_no_hit "accepts a link to an existing path" "$d" BROKEN_LINK

d="$(fixture)"; echo "See [docs](https://example.com/nope.md)." > "$d/README.md"
assert_no_hit "ignores external links" "$d" BROKEN_LINK

d="$(fixture)"; mkdir -p "$d/docs"; echo "x" > "$d/docs/real.md"
echo "See [the guide](docs/real.md#a-heading)." > "$d/README.md"
assert_no_hit "strips #anchor before resolving" "$d" BROKEN_LINK

d="$(fixture)"; echo "Format: [ADR-NNN: Title](docs/ADRs/ADR-NNN-x.md) <!-- inherited-docs-ok -->" > "$d/README.md"
assert_no_hit "inherited-docs-ok suppresses a broken link" "$d" BROKEN_LINK

# --------------------------------------------------------------- missing prompts

d="$(fixture)"; echo "Run gone.prompt.md for that." > "$d/README.md"
assert_hit "detects a reference to a removed prompt" "$d" MISSING_PROMPT "gone.prompt.md"

d="$(fixture)"; echo "x" > "$d/.github/prompts/here.prompt.md"
echo "Run here.prompt.md for that." > "$d/README.md"
assert_no_hit "accepts a reference to an existing prompt" "$d" MISSING_PROMPT

d="$(fixture)"; echo "Was gone.prompt.md <!-- inherited-docs-ok -->" > "$d/README.md"
assert_no_hit "inherited-docs-ok suppresses a missing prompt" "$d" MISSING_PROMPT

# ---------------------------------------------------------------------- summary

echo "---"
echo "passed=$pass failed=$fail"
(( fail == 0 ))
