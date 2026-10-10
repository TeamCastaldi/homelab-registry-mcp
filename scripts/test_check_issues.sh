#!/usr/bin/env bash
# test_check_issues.sh
#
# Tests for check_issues.sh. Each case writes a small issues JSON file — built
# with jq, in the shape the REST issues endpoint returns unless a case says
# otherwise — next to a copy of the repo's .github/labels.yml, and asserts on
# the tab-separated output.
#
# Usage:
#   test_check_issues.sh
#
# Exit status: 0 all passed, 1 at least one failure.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$HERE/check_issues.sh"
REPO="$(cd "$HERE/.." && pwd)"

if ! command -v jq >/dev/null 2>&1; then
  echo "test_check_issues.sh: needs jq" >&2
  exit 1
fi

pass=0
fail=0

# shellcheck disable=SC2001
indent() { sed 's/^/    /' <<<"$1"; }

# issue <number> <labels, comma-separated> [body] [state] — one issue object.
issue() {
  jq -n --argjson n "$1" --arg labels "$2" --arg body "${3:-}" --arg state "${4:-OPEN}" '
    {number: $n, title: "Issue \($n)", state: $state, body: $body,
     labels: ($labels | split(",") | map(select(. != "")) | map({id: "x", name: ., color: "ededed", description: ""}))}'
}

# fixture <issue objects...> — a directory holding issues.json and the labels file.
fixture() {
  local dir
  dir="$(mktemp -d -t check-issues-test.XXXXXX)"
  mkdir -p "$dir/.github"
  cp "$REPO/.github/labels.yml" "$dir/.github/labels.yml"
  if [[ $# -gt 0 ]]; then printf '%s\n' "$@" | jq -s . > "$dir/issues.json"; else echo '[]' > "$dir/issues.json"; fi
  printf '%s' "$dir"
}

run() { local dir="$1"; shift; out="$(cd "$dir" && "$SUT" "$@" 2>&1)"; code=$?; }

has_hit() { awk -F'\t' -v c="$2" -v n="$3" '$1 == c && index($0, n) { f = 1 } END { exit !f }' <<<"$1"; }

assert_hit() {
  local name="$1" dir="$2" check="$3" needle="$4"
  run "$dir" issues.json
  if has_hit "$out" "$check" "$needle"; then pass=$((pass + 1)); else
    fail=$((fail + 1)); echo "FAIL: $name"; echo "  expected a $check line containing: $needle"; indent "$out"
  fi
  rm -rf "$dir"
}

assert_no_hit() {
  local name="$1" dir="$2" check="$3"
  run "$dir" issues.json
  if grep -q "^$check	" <<<"$out"; then
    fail=$((fail + 1)); echo "FAIL: $name"; echo "  expected no $check line, got:"; indent "$out"
  else pass=$((pass + 1)); fi
  rm -rf "$dir"
}

assert_exit() {
  local name="$1" want="$2" dir="$3"; shift 3
  run "$dir" "$@"
  if [[ "$code" == "$want" ]]; then pass=$((pass + 1)); else
    fail=$((fail + 1)); echo "FAIL: $name — expected exit $want, got $code"; indent "$out"
  fi
  rm -rf "$dir"
}

CRIT_OPEN=$'### Acceptance criteria\n\n- [ ] works\n- [x] tested'
CRIT_DONE=$'### Acceptance criteria\n\n- [x] works\n- [X] tested'

# ------------------------------------------------------------------ inventory

d="$(fixture "$(issue 1 'status:in-progress,type:bug,p1:high' "$CRIT_OPEN")")"
assert_hit "an in-progress issue is listed with its type and priority" "$d" IN_PROGRESS $'#1\t[bug] [p1] Issue 1'

d="$(fixture "$(issue 3 'status:backlog,type:idea')" "$(issue 2 'status:planned,type:feature,p2:medium' "$CRIT_OPEN")" \
             "$(issue 4 'status:planned,type:feature,p1:high' "$CRIT_OPEN")" "$(issue 5 'status:in-progress,type:bug,p3:low' "$CRIT_OPEN")")"
run "$d" issues.json
order="$(awk -F'\t' '$1 ~ /^(IN_PROGRESS|PLANNED|BLOCKED|BACKLOG|UNSORTED)$/ { printf "%s ", $2 }' <<<"$out")"
if [[ "$order" == "#5 #4 #2 #3 " ]]; then pass=$((pass + 1)); else
  fail=$((fail + 1)); echo "FAIL: inventory is in pick-up order (status, then priority)"; echo "  got: $order"; indent "$out"; fi
rm -rf "$d"

d="$(fixture "$(issue 1 'status:planned,type:feature,p1:high' $'### Acceptance criteria\n\n- [ ] a\n\n### Dependencies\n\nBlocked by #12, and #3, and #12 again')")"
assert_hit "deps are read from ### Dependencies, deduplicated, in order" "$d" PLANNED "deps:#12,#3"

d="$(fixture "$(issue 1 'status:backlog,type:bug' '' CLOSED)" "$(issue 2 'status:backlog,type:bug' '' closed)")"
run "$d" issues.json
if grep -q '^IN_PROGRESS=0 PLANNED=0 BLOCKED=0 BACKLOG=0 UNSORTED=0 FLAGS=0$' <<<"$out"; then pass=$((pass + 1)); else
  fail=$((fail + 1)); echo "FAIL: closed issues are skipped, in either case"; indent "$out"; fi
rm -rf "$d"

d="$(fixture)"; echo '{"issues":[{"number":7,"title":"REST shape","state":"open","body":null,"labels":["status:backlog","type:idea"]}],"pageInfo":{}}' > "$d/issues.json"
assert_hit "REST-style input: wrapped in .issues, labels as strings, null body" "$d" BACKLOG $'#7\t[idea] [-] REST shape'

d="$(fixture)"
printf '%s\n' "$(issue 1 'status:backlog,type:idea' | jq -s .)" "$(issue 2 'status:backlog,type:bug' | jq -s .)" > "$d/issues.json"
assert_hit "back-to-back arrays, as gh api --paginate prints them, are all read" "$d" BACKLOG '#2'

d="$(fixture)"
printf '%s\n' "$(issue 1 'status:backlog,type:idea' | jq -c .)" "$(issue 2 'status:backlog,type:bug' | jq -c .)" > "$d/issues.json"
assert_hit "one issue object per line is read" "$d" BACKLOG '#2'

d="$(fixture)"
issue 4 'status:backlog,type:bug' | jq '. + {pull_request: {url: "x"}}' | jq -s . > "$d/issues.json"
run "$d" issues.json
if grep -q '^IN_PROGRESS=0 PLANNED=0 BLOCKED=0 BACKLOG=0 UNSORTED=0 FLAGS=0$' <<<"$out"; then pass=$((pass + 1)); else
  fail=$((fail + 1)); echo "FAIL: pull requests from the REST issues endpoint are skipped"; indent "$out"; fi
rm -rf "$d"

d="$(fixture "$(issue 1 'status:backlog,type:idea')")"
out="$(cd "$d" && "$SUT" - < issues.json 2>&1)"; code=$?
if [[ $code -eq 0 ]] && has_hit "$out" BACKLOG '#1'; then pass=$((pass + 1)); else
  fail=$((fail + 1)); echo "FAIL: reads issues from stdin with -"; indent "$out"; fi
rm -rf "$d"

# ------------------------------------------------------------------ labels

d="$(fixture "$(issue 1 'type:bug')")"
assert_hit "flags an issue with no status label" "$d" NO_STATUS "#1"

d="$(fixture "$(issue 1 'status:backlog,type:bug')")"
assert_no_hit "one status label is fine" "$d" NO_STATUS

d="$(fixture "$(issue 1 'status:backlog,status:planned,type:bug')")"
assert_hit "flags two status labels" "$d" MULTI_STATUS "status:backlog,status:planned"

d="$(fixture "$(issue 1 'status:backlog,status:planned,type:bug')")"
assert_hit "two status labels leave the issue UNSORTED" "$d" UNSORTED "#1"

d="$(fixture "$(issue 1 'status:backlog')")"
assert_hit "flags an issue with no type label" "$d" NO_TYPE "#1"

d="$(fixture "$(issue 1 'status:backlog,type:bug,type:debt')")"
assert_hit "flags two type labels" "$d" MULTI_TYPE "type:bug,type:debt"

d="$(fixture "$(issue 1 'status:backlog,type:bug,p1:high,p2:medium')")"
assert_hit "flags two priority labels" "$d" MULTI_PRIORITY "p1:high,p2:medium"

d="$(fixture "$(issue 1 'status:planned,type:bug' "$CRIT_OPEN")")"
assert_hit "flags a planned issue with no priority" "$d" NO_PRIORITY "#1"

d="$(fixture "$(issue 1 'status:backlog,type:bug')")"
assert_no_hit "a backlog issue may have no priority" "$d" NO_PRIORITY

d="$(fixture "$(issue 1 'status:inprogress,type:bug')")"
assert_hit "flags a status label the labels file does not define" "$d" UNKNOWN_LABEL "status:inprogress"

d="$(fixture "$(issue 1 'status:backlog,type:bug,good first issue,dependencies')")"
assert_no_hit "labels outside the three families are not checked" "$d" UNKNOWN_LABEL

# ------------------------------------------------------------------ body

d="$(fixture "$(issue 1 'status:planned,type:feature,p1:high' '### Context\n\nno criteria here')")"
assert_hit "flags a planned feature with no acceptance checkbox" "$d" NO_CRITERIA "#1"

d="$(fixture "$(issue 1 'status:planned,type:idea,p1:high' 'just an idea')")"
assert_no_hit "an idea needs no acceptance criteria" "$d" NO_CRITERIA

d="$(fixture "$(issue 1 'status:backlog,type:feature' 'no criteria yet')")"
assert_no_hit "a backlog issue needs no acceptance criteria yet" "$d" NO_CRITERIA

d="$(fixture "$(issue 1 'status:planned,type:feature,p1:high' $'### Acceptance criteria\n\n```\n- [ ] inside a fence\n```')")"
assert_hit "a checkbox inside a code fence does not count" "$d" NO_CRITERIA "#1"

d="$(fixture "$(issue 1 'status:planned,type:feature,p1:high' $'### Context\n\n- [ ] a box in the wrong section')")"
assert_hit "a checkbox outside ### Acceptance criteria does not count" "$d" NO_CRITERIA "#1"

d="$(fixture "$(issue 1 'status:in-progress,type:bug,p1:high' "$CRIT_DONE")")"
assert_hit "flags an open issue whose every criterion is ticked" "$d" CRITERIA_MET "all 2 acceptance criteria ticked"

d="$(fixture "$(issue 1 'status:in-progress,type:bug,p1:high' "$CRIT_OPEN")")"
assert_no_hit "one unticked criterion is not CRITERIA_MET" "$d" CRITERIA_MET

d="$(fixture "$(issue 1 'status:blocked,type:bug,p1:high' $'### Dependencies\n\n_No response_')")"
assert_hit "flags a blocked issue whose Dependencies says nothing" "$d" BLOCKED_NO_REASON "#1"

d="$(fixture "$(issue 1 'status:blocked,type:bug,p1:high' $'### Dependencies\n\nWaiting on the vendor API key')")"
assert_no_hit "a blocked issue with a reason under Dependencies is fine" "$d" BLOCKED_NO_REASON

# ------------------------------------------------------------------ exit status

d="$(fixture "$(issue 1 'status:in-progress,type:bug,p1:high' "$CRIT_OPEN")")"
assert_exit "exit 0 when there are only inventory lines" 0 "$d" issues.json

d="$(fixture "$(issue 1 'type:bug')")"
assert_exit "exit 1 on any flag" 1 "$d" issues.json

d="$(fixture)"
assert_exit "exit 2 with no arguments" 2 "$d"

d="$(fixture)"
assert_exit "exit 2 when the issues file is missing" 2 "$d" nope.json

d="$(fixture)"; echo '{not json' > "$d/issues.json"
assert_exit "exit 2 on invalid JSON" 2 "$d" issues.json

d="$(fixture)"; echo '{"total": 3}' > "$d/issues.json"
assert_exit "exit 2 on JSON that holds no issue list" 2 "$d" issues.json

d="$(fixture)"; rm "$d/.github/labels.yml"
assert_exit "exit 2 when the labels file is missing" 2 "$d" issues.json

# ---------------------------------------------------------------------- summary

echo "---"
echo "passed=$pass failed=$fail"
(( fail == 0 ))
