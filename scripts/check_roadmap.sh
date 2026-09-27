#!/usr/bin/env bash
# check_roadmap.sh
#
# Reads a roadmap's markdown checklist and reports two things: an inventory of
# every item with its line number, and the items whose claims do not match the
# repo. The /roadmap command runs this rather than reading the file and forming
# an opinion — a roadmap that says "done" is a claim, and this resolves the
# checkable part of it against what is actually on disk.
#
# An item is a checklist line: `- [ ] text`, with `-`, `*` or `+`, `[x]` or
# `[X]`, at any indentation. Lines inside fenced code blocks are ignored.
#
#   - [ ] Add TFA toggle in `src/auth/tfa.py` — source: docs/foundation.md
#   - [ ] Wire CI deploy step — source: #14 — blocked: needs ADR-003
#   - [x] Scaffold CLI `src/cli.py` — source: CLAUDE.md — done 2026-09-26 (f73e0d6)
#
# Inventory, one line per item:
#
#   OPEN           unchecked
#   BLOCKED        unchecked, and carries `blocked:`
#   DONE           checked
#
# Flags — each is a reason to look, not a verdict:
#
#   NO_SOURCE      no `source:` citation, so nothing ties the item to a goal,
#                  an issue, or the code
#   BROKEN_SOURCE  a cited source path that does not exist on disk
#   MAYBE_DONE     unchecked, but every backticked path in its text exists —
#                  the deliverable may have landed without the box being ticked
#   DONE_MISSING   checked, but a backticked path in its text does not exist —
#                  it was removed, renamed, or never landed
#
# What counts as a path: a source token or backticked span containing `/` or
# `.`, with no spaces or glob characters. `#123` issue references and URLs are
# never checked here — resolving them needs network access this script does not
# assume. Other words (`requested 2026-09-26`, `ADR-003`) are free text.
# A `#anchor` on a path is stripped before the existence check.
#
# Usage:
#   check_roadmap.sh [repo_root] [roadmap_path]
#     repo_root     defaults to the current directory
#     roadmap_path  relative to repo_root; defaults to docs/plans/ROADMAP.md
#
# Output (to stdout), tab-separated:
#   <CHECK>	<roadmap_path>:<line>	<detail>
#   ---
#   OPEN=<n> BLOCKED=<n> DONE=<n> NO_SOURCE=<n> BROKEN_SOURCE=<n> MAYBE_DONE=<n> DONE_MISSING=<n>
#
# Inventory detail is `[<section>] <item text>`, where section is the nearest
# heading above the item at level 2 or deeper.
#
# Exit status:
#   0  no flags (inventory lines alone are not a problem)
#   1  at least one flag
#   2  bad usage, or no roadmap file at the path

set -euo pipefail

ROOT="${1:-.}"
ROADMAP="${2:-docs/plans/ROADMAP.md}"

if [[ ! -d "$ROOT" ]]; then
  echo "check_roadmap.sh: not a directory: $ROOT" >&2
  echo "usage: check_roadmap.sh [repo_root] [roadmap_path]" >&2
  exit 2
fi

ROOT="$(cd "$ROOT" && pwd)"

if [[ ! -f "$ROOT/$ROADMAP" ]]; then
  echo "check_roadmap.sh: no roadmap at $ROADMAP" >&2
  exit 2
fi

n_open=0
n_blocked=0
n_done=0
n_no_source=0
n_broken=0
n_maybe=0
n_missing=0

emit() { printf '%s\t%s:%s\t%s\n' "$1" "$ROADMAP" "$2" "$3"; }

# A token names a path when it contains / or . and nothing that makes it prose
# or a pattern. Leading ./ is dropped so `./src/x.py` and `src/x.py` agree.
is_path() {
  local t="$1"
  [[ -n "$t" ]] || return 1
  [[ "$t" == *[/.]* ]] || return 1
  [[ "$t" != *[[:space:]*?]* ]] || return 1
  [[ "$t" != *://* ]] || return 1
  [[ "$t" != \#* ]] || return 1
  return 0
}

normalize() {
  local t="$1"
  t="${t%%#*}"          # drop a markdown anchor
  t="${t#./}"
  printf '%s' "$t"
}

item_re='^[[:space:]]*[-*+][[:space:]]+\[([ xX])\][[:space:]]+(.*)$'
fence_re='^[[:space:]]*(```|~~~)'
heading_re='^#{2,6}[[:space:]]+(.*)$'
# The backticks are literal markdown code spans, not command substitution.
# shellcheck disable=SC2016
tick_re='`([^`]+)`'

in_fence=0
section='-'
lineno=0

while IFS= read -r line || [[ -n "$line" ]]; do
  lineno=$((lineno + 1))
  line="${line%$'\r'}"

  if [[ "$line" =~ $fence_re ]]; then
    in_fence=$((1 - in_fence))
    continue
  fi
  (( in_fence )) && continue

  if [[ "$line" =~ $heading_re ]]; then
    section="${BASH_REMATCH[1]}"
    continue
  fi

  [[ "$line" =~ $item_re ]] || continue
  mark="${BASH_REMATCH[1]}"
  text="${BASH_REMATCH[2]}"
  lower="$(tr '[:upper:]' '[:lower:]' <<<"$text")"

  # Split the item into what it promises (before `source:`) and what it cites.
  body="$text"
  sources=''
  has_source=0
  prefix="${lower%%source:*}"
  # `resource:` is not a citation — the keyword must start a word.
  if [[ "$lower" == *source:* && ! "$prefix" =~ [a-z0-9_-]$ ]]; then
    has_source=1
    body="${text:0:${#prefix}}"
    sources="${text:$(( ${#prefix} + 7 ))}"
    # The citation ends at the next em-dash or double-hyphen separator.
    sources="${sources%% — *}"
    sources="${sources%% -- *}"
  fi

  # ------------------------------------------------------------ inventory
  if [[ "$mark" == " " ]]; then
    if [[ "$lower" == *blocked:* ]]; then
      emit BLOCKED "$lineno" "[$section] $text"
      n_blocked=$((n_blocked + 1))
    else
      emit OPEN "$lineno" "[$section] $text"
      n_open=$((n_open + 1))
    fi
  else
    emit DONE "$lineno" "[$section] $text"
    n_done=$((n_done + 1))
  fi

  # ------------------------------------------------------------ citation
  cited=0
  if (( has_source )); then
    read -ra tokens <<<"${sources//,/ }"
    for token in "${tokens[@]}"; do
      token="${token//\`/}"
      token="${token%[.,;:)]}"
      [[ -n "$token" ]] && cited=1
      is_path "$token" || continue
      path="$(normalize "$token")"
      if [[ ! -e "$ROOT/$path" ]]; then
        emit BROKEN_SOURCE "$lineno" "cites $path, which does not exist"
        n_broken=$((n_broken + 1))
      fi
    done
  fi
  if (( ! cited )); then
    emit NO_SOURCE "$lineno" "no source: citation"
    n_no_source=$((n_no_source + 1))
  fi

  # ------------------------------------------------------------ deliverables
  present=0
  absent=()
  rest="$body"
  while [[ "$rest" =~ $tick_re ]]; do
    span="${BASH_REMATCH[1]}"
    rest="${rest#*"\`$span\`"}"
    is_path "$span" || continue
    path="$(normalize "$span")"
    if [[ -e "$ROOT/$path" ]]; then
      present=$((present + 1))
    else
      absent+=("$path")
    fi
  done

  if [[ "$mark" == " " ]] && (( present > 0 && ${#absent[@]} == 0 )); then
    emit MAYBE_DONE "$lineno" "unchecked, but every path it names exists"
    n_maybe=$((n_maybe + 1))
  elif [[ "$mark" != " " ]] && (( ${#absent[@]} > 0 )); then
    emit DONE_MISSING "$lineno" "checked, but missing: ${absent[*]}"
    n_missing=$((n_missing + 1))
  fi
done < "$ROOT/$ROADMAP"

echo '---'
printf 'OPEN=%d BLOCKED=%d DONE=%d NO_SOURCE=%d BROKEN_SOURCE=%d MAYBE_DONE=%d DONE_MISSING=%d\n' \
  "$n_open" "$n_blocked" "$n_done" "$n_no_source" "$n_broken" "$n_maybe" "$n_missing"

if (( n_no_source + n_broken + n_maybe + n_missing > 0 )); then
  exit 1
fi
exit 0
