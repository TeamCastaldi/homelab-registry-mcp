#!/usr/bin/env bash
# check_issues.sh
#
# Reads a repo's open GitHub issues and reports two things: an inventory sorted
# the way work gets picked (in progress, then planned, blocked and backlog,
# each by priority), and the issues whose labels or body break the conventions
# in .github/labels.yml and .github/ISSUE_TEMPLATE/. The issue-tracker skill,
# /session-start and /roadmap evaluate run this rather than eyeballing a list —
# with forty issues, "this one has two status labels" is easy to miss and
# trivial to count.
#
# Input is the issues as JSON, from whichever tool a session has:
#
#   gh api 'repos/{owner}/{repo}/issues?state=open&per_page=100' --paginate \
#     | bash scripts/check_issues.sh -
#
# (REST on purpose: `gh issue list` uses GraphQL, which Claude Code cloud
# sessions do not allow), or the GitHub connector's list_issues output saved to
# a file. Only the fields every source shares are read: number, title, state,
# body, and label names (label objects or plain strings). Any sequence of JSON
# values is accepted — one array, the back-to-back arrays --paginate prints,
# objects carrying the array under `issues` or `items` (one per page), or one
# issue object per line. Pull requests, which the REST issues endpoint returns
# too, are skipped, and so are closed issues, whether the state reads OPEN or
# open.
#
# The body is read by its `### ` sections — the headings the issue forms
# produce and the issue-tracker skill writes.
#
# Inventory, one line per open issue, in pick-up order:
#
#   IN_PROGRESS  PLANNED  BLOCKED  BACKLOG   from its one status:* label
#   UNSORTED                                 no status label, or more than one
#
# Detail is `[type] [priority] title`, `-` for a missing type or priority, plus
# ` deps:#a,#b` when its Dependencies section names issues.
#
# Flags — each is a reason to look, not a verdict:
#
#   NO_STATUS, MULTI_STATUS      not exactly one status:* label
#   NO_TYPE, MULTI_TYPE          not exactly one type:* label
#   MULTI_PRIORITY               more than one pN:* label
#   NO_PRIORITY                  planned, in progress or blocked, with no pN:*
#   UNKNOWN_LABEL                a status:, type: or pN: label the labels file
#                                does not define — usually a typo
#   NO_CRITERIA                  planned or in progress, not an idea, and no
#                                checkbox under ### Acceptance criteria
#   CRITERIA_MET                 every acceptance checkbox is ticked, yet the
#                                issue is open — it may be done
#   BLOCKED_NO_REASON            status:blocked with nothing under
#                                ### Dependencies to say what it waits on
#
# Usage:
#   check_issues.sh <issues.json | -> [labels_file]
#     labels_file  defaults to .github/labels.yml
#
# Output (to stdout), tab-separated:
#   <CHECK>	#<number>	<detail>
#   ---
#   IN_PROGRESS=<n> PLANNED=<n> BLOCKED=<n> BACKLOG=<n> UNSORTED=<n> FLAGS=<n>
#
# Exit status:
#   0  no flags (inventory lines alone are not a problem)
#   1  at least one flag
#   2  bad usage, unreadable input or labels file, invalid JSON, or no jq

set -euo pipefail

usage() { echo "usage: check_issues.sh <issues.json | -> [labels_file]" >&2; }

if [[ $# -lt 1 || $# -gt 2 ]]; then usage; exit 2; fi
INPUT="$1"
LABELS="${2:-.github/labels.yml}"

if ! command -v jq >/dev/null 2>&1; then
  echo "check_issues.sh: needs jq — https://jqlang.org" >&2
  exit 2
fi
if [[ "$INPUT" != "-" && ! -f "$INPUT" ]]; then
  echo "check_issues.sh: no such file: $INPUT" >&2
  exit 2
fi
if [[ ! -f "$LABELS" ]]; then
  echo "check_issues.sh: no labels file at $LABELS" >&2
  exit 2
fi

# Label names the labels file defines, one per line with a newline at each
# end, so membership is a substring test. sync_labels.sh validates the file's
# format; here only the names matter. (No associative arrays: these scripts
# run on the bash 3.2 macOS ships.)
known=$'\n'"$(sed -n 's/^-[[:space:]]*name:[[:space:]]*//p' "$LABELS" \
                 | sed 's/[[:space:]]*$//; s/^"\(.*\)"$/\1/')"$'\n'
is_known() { [[ "$known" == *$'\n'"$1"$'\n'* ]]; }

if [[ "$INPUT" == "-" ]]; then
  json="$(cat)"
else
  json="$(cat "$INPUT")"
fi

# One line per open issue, fields split by \x1e (not a tab: bash's read
# collapses runs of whitespace separators, which would shift every field after
# an empty one):
#   number, title, labels (\x1f-joined), criteria total, criteria ticked,
#   dependency numbers (comma-joined), whether Dependencies says anything.
# shellcheck disable=SC2016  # $vars below are jq's, not the shell's
JQ='
  def clean: gsub("[\t\r\n\u001e\u001f]+"; " ");
  def section($want):
    reduce ((.body // "") | gsub("\r"; "") | split("\n"))[] as $l
      ({cur: null, fence: false, out: []};
       if ($l | test("^\\s*(```|~~~)")) then .fence = (.fence | not)
       elif (.fence | not) and ($l | test("^###\\s")) then
         .cur = ($l | sub("^###\\s+"; "") | sub("\\s+$"; "") | ascii_downcase)
       elif .cur == $want and (.fence | not) then .out += [$l]
       else . end)
    | .out;
  .[]
  | if type == "array" then .[]
    elif type == "object" and (.issues | type) == "array" then .issues[]
    elif type == "object" and (.items | type) == "array" then .items[]
    elif type == "object" and has("number") then .
    else error("expected issues: an array, pages of arrays, or issue objects") end
  | select(.pull_request == null)
  | select(((.state // "open") | ascii_downcase) != "closed")
  | (section("acceptance criteria") | map(select(test("^\\s*[-*+]\\s+\\[[ xX]\\]")))) as $boxes
  | (section("dependencies")) as $deps
  | [ (.number | tostring),
      ((.title // "") | clean),
      ([.labels[]? | if type == "string" then . else .name end] | join("\u001f")),
      ($boxes | length | tostring),
      ($boxes | map(select(test("\\[[xX]\\]"))) | length | tostring),
      ($deps | join("\n") | [scan("#([0-9]+)")[0]] | reduce .[] as $n ([]; if index([$n]) then . else . + [$n] end) | map("#" + .) | join(",")),
      ($deps | map(select(test("\\S") and (test("^\\s*_No response_\\s*$") | not))) | length > 0 | tostring)
    ]
  | join("\u001e")
'

if ! rows="$(jq -r -s "$JQ" <<<"$json" 2>&1)"; then
  echo "check_issues.sh: could not read the issues: $rows" >&2
  exit 2
fi

status_rank() {
  case "$1" in
    IN_PROGRESS) echo 1 ;; PLANNED) echo 2 ;; BLOCKED) echo 3 ;; BACKLOG) echo 4 ;; *) echo 5 ;;
  esac
}

n_in_progress=0 n_planned=0 n_blocked=0 n_backlog=0 n_unsorted=0
inventory=()
flags=()
n_flags=0

flag() { flags+=("$(printf '%s\t#%s\t%s' "$1" "$2" "$3")"); n_flags=$((n_flags + 1)); }

while IFS=$'\x1e' read -r num title labelstr total ticked deps has_reason; do
  [[ -n "$num" ]] || continue
  IFS=$'\x1f' read -r -a labels <<<"$labelstr"

  statuses=() types=() prios=()
  for l in ${labels[@]+"${labels[@]}"}; do
    case "$l" in
      status:*) statuses+=("$l") ;;
      type:*) types+=("$l") ;;
      p[0-9]:*|p[0-9][0-9]:*) prios+=("$l") ;;
      *) continue ;;
    esac
    is_known "$l" || flag UNKNOWN_LABEL "$num" "$l is not in $LABELS"
  done

  bucket=UNSORTED words=unsorted
  if (( ${#statuses[@]} == 1 )); then
    case "${statuses[0]}" in
      status:in-progress) bucket=IN_PROGRESS words="in progress" ;;
      status:planned) bucket=PLANNED words=planned ;;
      status:blocked) bucket=BLOCKED words=blocked ;;
      status:backlog) bucket=BACKLOG words=backlog ;;
    esac
  fi

  type_s="-"; (( ${#types[@]} == 1 )) && type_s="${types[0]#type:}"
  prio_s="-"; (( ${#prios[@]} == 1 )) && prio_s="${prios[0]%%:*}"
  prio_rank="${prio_s#p}"; [[ "$prio_s" == "-" ]] && prio_rank=99

  detail="[$type_s] [$prio_s] $title${deps:+ deps:$deps}"
  inventory+=("$(printf '%s\t%02d\t%08d\t%s\t#%s\t%s' "$(status_rank "$bucket")" "$prio_rank" "$num" "$bucket" "$num" "$detail")")
  case "$bucket" in
    IN_PROGRESS) n_in_progress=$((n_in_progress + 1)) ;;
    PLANNED) n_planned=$((n_planned + 1)) ;;
    BLOCKED) n_blocked=$((n_blocked + 1)) ;;
    BACKLOG) n_backlog=$((n_backlog + 1)) ;;
    *) n_unsorted=$((n_unsorted + 1)) ;;
  esac

  # -------------------------------------------------------------- flags
  if (( ${#statuses[@]} == 0 )); then flag NO_STATUS "$num" "no status:* label"
  elif (( ${#statuses[@]} > 1 )); then flag MULTI_STATUS "$num" "$(IFS=,; echo "${statuses[*]}")"; fi

  if (( ${#types[@]} == 0 )); then flag NO_TYPE "$num" "no type:* label"
  elif (( ${#types[@]} > 1 )); then flag MULTI_TYPE "$num" "$(IFS=,; echo "${types[*]}")"; fi

  if (( ${#prios[@]} > 1 )); then flag MULTI_PRIORITY "$num" "$(IFS=,; echo "${prios[*]}")"; fi

  case "$bucket" in
    IN_PROGRESS|PLANNED|BLOCKED)
      if (( ${#prios[@]} == 0 )); then flag NO_PRIORITY "$num" "$words, with no priority label"; fi
      ;;
  esac

  if [[ "$bucket" == IN_PROGRESS || "$bucket" == PLANNED ]] && [[ "$type_s" != idea ]] && (( total == 0 )); then
    flag NO_CRITERIA "$num" "$words, but no checkbox under ### Acceptance criteria"
  fi

  if (( total > 0 && ticked == total )); then
    flag CRITERIA_MET "$num" "all $total acceptance criteria ticked, still open"
  fi

  if [[ "$bucket" == BLOCKED && "$has_reason" != true ]]; then
    flag BLOCKED_NO_REASON "$num" "blocked, but ### Dependencies says nothing"
  fi
done <<<"$rows"

if (( ${#inventory[@]} > 0 )); then
  printf '%s\n' "${inventory[@]}" | sort -t$'\t' -k1,1n -k2,2n -k3,3n | cut -f4-
fi
if (( ${#flags[@]} > 0 )); then
  printf '%s\n' "${flags[@]}"
fi

echo '---'
printf 'IN_PROGRESS=%d PLANNED=%d BLOCKED=%d BACKLOG=%d UNSORTED=%d FLAGS=%d\n' \
  "$n_in_progress" "$n_planned" "$n_blocked" "$n_backlog" "$n_unsorted" "$n_flags"

(( n_flags == 0 )) || exit 1
exit 0
