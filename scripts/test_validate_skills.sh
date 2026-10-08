#!/usr/bin/env bash
# test_validate_skills.sh
#
# Tests for validate_skills.sh. Each case builds a throwaway repo with one or
# more skills under .claude/skills/ and asserts on the tab-separated output.
#
# Usage:
#   test_validate_skills.sh
#   SUT=/path/to/other/validate_skills.sh test_validate_skills.sh
#
# SUT points the suite at another copy of the script, so the cases can be
# shown to fail against a version that lacks the check they cover.
#
# Exit status: 0 all passed, 1 at least one failure.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="${SUT:-$HERE/validate_skills.sh}"

pass=0
fail=0

# Indent captured output so a failure's detail reads as a block under its
# heading. SC2001 suggests ${var//from/to}, which cannot anchor per line.
# shellcheck disable=SC2001
indent() { sed 's/^/    /' <<<"$1"; }

fixture() {
  local dir
  dir="$(mktemp -d -t skills-test.XXXXXX)"
  mkdir -p "$dir/.claude/skills"
  printf '%s' "$dir"
}

# skill <repo> <dir name> <frontmatter lines> [body]
# Writes .claude/skills/<dir name>/SKILL.md with the frontmatter between ---
# markers, then the body (a single line by default).
skill() {
  local repo="$1" name="$2" fm="$3" body="${4:-Body.}"
  mkdir -p "$repo/.claude/skills/$name"
  printf -- '---\n%s\n---\n\n%s\n' "$fm" "$body" > "$repo/.claude/skills/$name/SKILL.md"
}

# repeat <string> <n> — the string n times, no separator.
repeat() { local out="" i; for ((i = 0; i < $2; i++)); do out+="$1"; done; printf '%s' "$out"; }

# n_lines <n> — n numbered lines, newline-separated, no trailing newline.
n_lines() { seq -f 'line %g' 1 "$1"; }

# A hit is one output line that starts with the check AND contains the needle.
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

# assert_clean <name> <dir> — exit 0 and PROBLEMS=0.
assert_clean() {
  local name="$1" dir="$2" out rc
  out="$("$SUT" "$dir" 2>&1)"
  rc=$?
  if [[ "$rc" == 0 ]] && grep -q 'PROBLEMS=0' <<<"$out"; then
    pass=$((pass + 1))
  else
    fail=$((fail + 1))
    echo "FAIL: $name"
    echo "  expected exit 0 and PROBLEMS=0, got exit $rc:"
    indent "$out"
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
    echo "FAIL: $name"
    echo "  expected exit $want, got $got"
  fi
}

GOOD_DESC='Does one thing well. Use when the user asks for that thing.'

# ------------------------------------------------------- layout (existing checks)

d="$(fixture)"; skill "$d" good-skill "name: good-skill
description: $GOOD_DESC"
assert_clean "a well-formed skill is clean" "$d"

d="$(fixture)"; mkdir -p "$d/.claude/skills/renamed"; printf -- '---\nname: renamed\ndescription: x\n---\n' > "$d/.claude/skills/renamed/renamed-SKILL.md"
assert_hit "a near-miss filename is named" "$d" MISSING_SKILL_MD "renamed-SKILL.md"

d="$(fixture)"; mkdir -p "$d/.claude/skills/no-front"; printf 'No frontmatter here.\n' > "$d/.claude/skills/no-front/SKILL.md"
assert_hit "a SKILL.md without --- is BAD_FRONTMATTER" "$d" BAD_FRONTMATTER "no-front"

d="$(fixture)"; skill "$d" nameless "description: $GOOD_DESC"
assert_hit "a missing name is reported" "$d" MISSING_NAME "nameless"

d="$(fixture)"; skill "$d" mismatch "name: other-name
description: $GOOD_DESC"
assert_hit "a name that differs from its directory is reported" "$d" NAME_MISMATCH "other-name"

d="$(fixture)"; skill "$d" zipped "name: zipped
description: $GOOD_DESC"; touch "$d/.claude/skills/zipped/zipped.skill"
assert_hit "a committed .skill archive is reported" "$d" STRAY_SKILL_ZIP "zipped.skill"

# ------------------------------------------------- reading the whole description

d="$(fixture)"; skill "$d" no-desc "name: no-desc"
assert_hit "a missing description is reported" "$d" MISSING_DESC "no-desc"

d="$(fixture)"; skill "$d" empty-folded "name: empty-folded
description: >"
assert_hit "a folded description with no lines is empty" "$d" MISSING_DESC "empty-folded"

d="$(fixture)"; skill "$d" empty-quoted "name: empty-quoted
description: \"\""
assert_hit "an empty quoted description is empty" "$d" MISSING_DESC "empty-quoted"

# 30 lines of 40 characters: each line is short, the value is 1,229 characters.
# The old reader returned only the first line, so this passed.
d="$(fixture)"; skill "$d" long-folded "name: long-folded
description: >
$(for _ in $(seq 30); do printf '  %s\n' "$(repeat 'abcdefghij' 4)"; done)"
assert_hit "a folded description is measured whole" "$d" DESC_TOO_LONG "1229 characters"

d="$(fixture)"; skill "$d" long-literal "name: long-literal
description: |-
$(for _ in $(seq 30); do printf '  %s\n' "$(repeat 'abcdefghij' 4)"; done)"
assert_hit "a literal description is measured whole" "$d" DESC_TOO_LONG "1229 characters"

d="$(fixture)"; skill "$d" long-plain "name: long-plain
description: $(repeat 'a' 1025)"
assert_hit "a plain description over 1,024 characters is reported" "$d" DESC_TOO_LONG "1025 characters"

d="$(fixture)"; skill "$d" at-limit "name: at-limit
description: $(repeat 'a' 1024)"
assert_no_hit "exactly 1,024 characters is within the limit" "$d" DESC_TOO_LONG

d="$(fixture)"; skill "$d" quoted-at-limit "name: quoted-at-limit
description: \"$(repeat 'a' 1024)\""
assert_no_hit "the quotes around a description are not counted" "$d" DESC_TOO_LONG

# 1,000 em dashes are 3,000 bytes but 1,000 characters.
d="$(fixture)"; skill "$d" multibyte "name: multibyte
description: $(repeat '—' 1000)"
assert_no_hit "a description is counted in characters, not bytes" "$d" DESC_TOO_LONG

# The next key's own indented lines must not be read as more description.
d="$(fixture)"; skill "$d" next-key "name: next-key
description: >
  Short description.
compatibility: >
$(for _ in $(seq 30); do printf '  %s\n' "$(repeat 'abcdefghij' 4)"; done)"
assert_no_hit "the next key's value is not read into the description" "$d" DESC_TOO_LONG

# ------------------------------------------------------------------- the name

d="$(fixture)"; skill "$d" Bad_Name "name: Bad_Name
description: $GOOD_DESC"
assert_hit "uppercase and underscores break the name format" "$d" NAME_FORMAT "Bad_Name"

long_name="$(repeat 'a' 65)"
d="$(fixture)"; skill "$d" "$long_name" "name: $long_name
description: $GOOD_DESC"
assert_hit "a name over 64 characters breaks the name format" "$d" NAME_FORMAT "$long_name"

d="$(fixture)"; skill "$d" "$(repeat 'a' 64)" "name: $(repeat 'a' 64)
description: $GOOD_DESC"
assert_no_hit "a 64-character name is within the limit" "$d" NAME_FORMAT

d="$(fixture)"; skill "$d" claude-helper "name: claude-helper
description: $GOOD_DESC"
assert_hit "a name containing claude is reserved" "$d" NAME_RESERVED "claude-helper"

d="$(fixture)"; skill "$d" anthropic-tools "name: anthropic-tools
description: $GOOD_DESC"
assert_hit "a name containing anthropic is reserved" "$d" NAME_RESERVED "anthropic-tools"

d="$(fixture)"; skill "$d" quoted-name "name: \"quoted-name\"
description: $GOOD_DESC"
assert_clean "a quoted name is read without its quotes" "$d"

# ------------------------------------------------------------------- XML tags

d="$(fixture)"; skill "$d" xml-desc "name: xml-desc
description: Formats replies. <example>Use it like this</example>"
assert_hit "an XML tag in the description is reported" "$d" XML_IN_FRONTMATTER "<example>"

d="$(fixture)"; skill "$d" less-than "name: less-than
description: Use when a < b or when b > c."
assert_no_hit "a bare less-than sign is not a tag" "$d" XML_IN_FRONTMATTER

d="$(fixture)"; skill "$d" body-xml "name: body-xml
description: $GOOD_DESC" "<example>Tags in the body are fine.</example>"
assert_no_hit "an XML tag in the body is not a frontmatter problem" "$d" XML_IN_FRONTMATTER

# ------------------------------------------------------------------- the body

# The body starts with the blank line skill() writes after the frontmatter, so
# 500 numbered lines make a 501-line body and 499 make exactly 500.
d="$(fixture)"; skill "$d" long-body "name: long-body
description: $GOOD_DESC" "$(n_lines 500)"
assert_hit "a body over 500 lines is reported" "$d" BODY_TOO_LONG "501 lines"

d="$(fixture)"; skill "$d" body-at-limit "name: body-at-limit
description: $GOOD_DESC" "$(n_lines 499)"
assert_no_hit "a 500-line body is within the limit" "$d" BODY_TOO_LONG

d="$(fixture)"; skill "$d" big-body "name: big-body
description: $GOOD_DESC" "$(repeat 'x' 20001)"
assert_hit "a body over 20,000 characters is warned about" "$d" WARN "big-body"

d="$(fixture)"; skill "$d" big-body-clean "name: big-body-clean
description: $GOOD_DESC" "$(repeat 'x' 20001)"
assert_clean "the size warning is not a problem" "$d"

d="$(fixture)"; skill "$d" small-body "name: small-body
description: $GOOD_DESC" "$(repeat 'x' 19990)"
assert_no_hit "a body under 20,000 characters is not warned about" "$d" WARN

# ----------------------------------------------------------------- exit codes

d="$(fixture)"; skill "$d" broken "name: Broken
description: $GOOD_DESC"
assert_exit "exit 1 on any problem" 1 "$d"; rm -rf "$d"

d="$(mktemp -d -t skills-test.XXXXXX)"
assert_exit "exit 2 when there is no .claude/skills directory" 2 "$d"; rm -rf "$d"

assert_exit "exit 2 on a repo root that is not a directory" 2 /nonexistent/skills-root

# ---------------------------------------------------------------------- summary

echo "---"
echo "passed=$pass failed=$fail"
(( fail == 0 ))
