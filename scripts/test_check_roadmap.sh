#!/usr/bin/env bash
# test_check_roadmap.sh
#
# Tests for check_roadmap.sh. Each case builds a throwaway fixture repo with a
# roadmap at the default path and asserts on the tab-separated output.
#
# Usage:
#   test_check_roadmap.sh
#
# Exit status: 0 all passed, 1 at least one failure.

# The fixtures below are markdown, not shell: backticks inside single quotes are
# code spans and fenced blocks being written verbatim to a file, never command
# substitution.
# shellcheck disable=SC2016

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$HERE/check_roadmap.sh"

pass=0
fail=0

# Indent captured output so a failure's detail reads as a block under its
# heading. SC2001 suggests ${var//from/to}, which cannot anchor per line.
# shellcheck disable=SC2001
indent() { sed 's/^/    /' <<<"$1"; }

fixture() {
  local dir
  dir="$(mktemp -d -t roadmap-test.XXXXXX)"
  mkdir -p "$dir/docs/plans" "$dir/src"
  printf '%s' "$dir"
}

# roadmap <dir> <body> — write body under a `## Now` heading at the default path.
roadmap() { printf '# Roadmap\n\n## Now\n%s\n' "$2" > "$1/docs/plans/ROADMAP.md"; }

# A hit is one output line that starts with the check AND contains the needle.
# Checking both on the same line matters: an item can carry several flags, and a
# needle matched on a neighbouring line would pass for the wrong reason.
has_hit() { awk -F'\t' -v c="$2" -v n="$3" '$1 == c && index($0, n) { found = 1 } END { exit !found }' <<<"$1"; }

assert_hit() {
  local name="$1" dir="$2" check="$3" needle="$4" out
  out="$("$SUT" "$dir" 2>&1)" || true
  if has_hit "$out" "$check" "$needle"; then
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

assert_exit() {
  local name="$1" want="$2" got
  shift 2
  "$SUT" "$@" >/dev/null 2>&1
  got=$?
  if [[ "$got" == "$want" ]]; then
    pass=$((pass + 1))
  else
    fail=$((fail + 1))
    echo "FAIL: $name — expected exit $want, got $got"
  fi
}

# -------------------------------------------------------------------- inventory

d="$(fixture)"; roadmap "$d" '- [ ] Plain item — source: #3'
assert_hit "unchecked item is OPEN, with its line number" "$d" OPEN "ROADMAP.md:4	"

d="$(fixture)"; roadmap "$d" '- [ ] Plain item — source: #3'
assert_hit "inventory detail carries the section" "$d" OPEN "[Now] Plain item"

d="$(fixture)"; roadmap "$d" '- [x] Shipped — source: #3'
assert_hit "checked item is DONE" "$d" DONE "Shipped"

d="$(fixture)"; roadmap "$d" '- [X] Shipped — source: #3'
assert_hit "capital X counts as checked" "$d" DONE "Shipped"

d="$(fixture)"; roadmap "$d" '- [ ] Deploy — source: #3 — blocked: needs ADR-003'
assert_hit "blocked: marks an unchecked item BLOCKED" "$d" BLOCKED "Deploy"

d="$(fixture)"; roadmap "$d" '- [ ] Deploy — source: #3 — blocked: needs ADR-003'
assert_no_hit "a BLOCKED item is not also OPEN" "$d" OPEN

d="$(fixture)"; roadmap "$d" '* [ ] Star — source: #1
  + [ ] Plus, indented — source: #2'
assert_hit "star bullets are items" "$d" OPEN "Star"

d="$(fixture)"; roadmap "$d" '* [ ] Star — source: #1
  + [ ] Plus, indented — source: #2'
assert_hit "indented plus bullets are items" "$d" OPEN "Plus, indented"

d="$(fixture)"; roadmap "$d" '```markdown
- [ ] Example in a fence — source: #1
```'
assert_no_hit "items inside a fenced block are ignored" "$d" OPEN

d="$(fixture)"; roadmap "$d" '- plain bullet, no box
- [] malformed box'
assert_no_hit "a line without a well-formed box is not an item" "$d" OPEN

# --------------------------------------------------------------------- sources

d="$(fixture)"; roadmap "$d" '- [ ] Uncited work'
assert_hit "flags an item with no source" "$d" NO_SOURCE "ROADMAP.md:4"

d="$(fixture)"; roadmap "$d" '- [ ] Uncited — source:'
assert_hit "an empty source: is no source" "$d" NO_SOURCE "ROADMAP.md:4"

d="$(fixture)"; roadmap "$d" '- [ ] Resource: not a citation'
assert_hit "resource: is not mistaken for source:" "$d" NO_SOURCE "ROADMAP.md:4"

d="$(fixture)"; roadmap "$d" '- [ ] Cited — source: docs/foundation.md'
assert_hit "flags a cited path that does not exist" "$d" BROKEN_SOURCE "docs/foundation.md"

d="$(fixture)"; roadmap "$d" '- [ ] Cited — source: docs/foundation.md#goals'
touch "$d/docs/foundation.md"
assert_no_hit "an anchor is stripped before the existence check" "$d" BROKEN_SOURCE

d="$(fixture)"; roadmap "$d" '- [ ] Cited — Source: `CLAUDE.md`, ./README.md.'
touch "$d/CLAUDE.md" "$d/README.md"
assert_no_hit "backticks, commas, ./ and trailing punctuation are tolerated" "$d" BROKEN_SOURCE

d="$(fixture)"; roadmap "$d" '- [ ] Cited — source: CLAUDE.md, docs/gone.md'
touch "$d/CLAUDE.md"
assert_hit "checks every path in a multi-source citation" "$d" BROKEN_SOURCE "docs/gone.md"

d="$(fixture)"; roadmap "$d" '- [ ] Cited — source: #14, https://example.invalid/x.md'
assert_no_hit "issue refs and URLs are never checked on disk" "$d" BROKEN_SOURCE

d="$(fixture)"; roadmap "$d" '- [ ] Asked for — source: requested 2026-09-26'
assert_no_hit "free-text source is accepted as a citation" "$d" NO_SOURCE

d="$(fixture)"; roadmap "$d" '- [ ] Cited — source: #3 — then `src/after.py` in a note'
assert_no_hit "the citation ends at the next em-dash separator" "$d" BROKEN_SOURCE

# ---------------------------------------------------------------- deliverables

d="$(fixture)"; roadmap "$d" '- [ ] Build `src/cli.py` — source: #1'
touch "$d/src/cli.py"
assert_hit "unchecked item whose path exists is MAYBE_DONE" "$d" MAYBE_DONE "ROADMAP.md:4"

d="$(fixture)"; roadmap "$d" '- [ ] Build `src/cli.py` and `src/api.py` — source: #1'
touch "$d/src/cli.py"
assert_no_hit "MAYBE_DONE needs every named path, not just one" "$d" MAYBE_DONE

d="$(fixture)"; roadmap "$d" '- [ ] Build the CLI — source: #1'
assert_no_hit "an item naming no path is never MAYBE_DONE" "$d" MAYBE_DONE

d="$(fixture)"; roadmap "$d" '- [ ] Run `npm test` and `*.py` — source: #1'
touch "$d/src/x.py"
assert_no_hit "commands and globs are not treated as paths" "$d" MAYBE_DONE

d="$(fixture)"; roadmap "$d" '- [x] Built `src/cli.py` — source: #1 — done 2026-09-26 (abc1234)'
assert_hit "checked item whose path is gone is DONE_MISSING" "$d" DONE_MISSING "src/cli.py"

d="$(fixture)"; roadmap "$d" '- [x] Built `src/cli.py` — source: #1'
touch "$d/src/cli.py"
assert_no_hit "checked item whose path exists is fine" "$d" DONE_MISSING

d="$(fixture)"; roadmap "$d" '- [ ] Build the thing — source: `src/x.py`'
touch "$d/src/x.py"
assert_no_hit "a path only in the citation is not a deliverable" "$d" MAYBE_DONE

# ------------------------------------------------------------------ exit codes

d="$(fixture)"; roadmap "$d" '- [ ] Clean — source: #1
- [x] Also clean — source: #2'
assert_exit "exit 0 when there are only inventory lines" 0 "$d"; rm -rf "$d"

d="$(fixture)"; roadmap "$d" '- [ ] Uncited'
assert_exit "exit 1 on any flag" 1 "$d"; rm -rf "$d"

d="$(fixture)"
assert_exit "exit 2 when the roadmap file is missing" 2 "$d"; rm -rf "$d"

assert_exit "exit 2 on a repo root that is not a directory" 2 /nonexistent/roadmap-root

d="$(fixture)"; mkdir -p "$d/plan"; printf -- '- [ ] Elsewhere — source: #1\n' > "$d/plan/road.md"
assert_exit "honours a non-default roadmap path" 0 "$d" plan/road.md; rm -rf "$d"

# ---------------------------------------------------------------------- summary

echo "---"
echo "passed=$pass failed=$fail"
(( fail == 0 ))
