#!/usr/bin/env bash
# sync_labels.sh
#
# Makes the repo's GitHub labels match .github/labels.yml: creates the ones that
# are missing and updates the ones whose colour or description drifted. Labels
# on GitHub that the file does not list are reported and left alone — this
# never deletes, so a label someone added by hand, or one of GitHub's defaults,
# survives every run.
#
# .github/workflows/label-sync.yml runs this on every push to main, which is how
# a new repo gets its labels with nothing run by hand. The issue-tracker skill
# runs it with --check to find out whether the labels are there yet.
#
# The labels file is parsed here without a YAML library, so its format is
# narrow on purpose: one `- name:` line per label, followed by `color:` and
# `description:` lines, values bare or in double quotes. Comments and blank
# lines are skipped. Anything else stops the run with exit 2 and the line
# number — a label silently dropped would look like a sync that worked.
#
# Needs `gh`, logged in (or GH_TOKEN set, as the workflow does). Every call is
# `gh api` against the REST API, never `gh label …`: those subcommands use
# GraphQL, which Claude Code cloud sessions do not allow, while REST works
# there, locally and in Actions alike. The repo is the one the {owner}/{repo}
# placeholders resolve to — this directory's git remote, or GH_REPO if set.
# JSON is shaped with gh's built-in --jq, so jq itself is not needed.
#
# Usage:
#   sync_labels.sh [--check] [labels_file]
#     --check      report only; write nothing
#     labels_file  defaults to .github/labels.yml
#
# Output (to stdout), tab-separated:
#   <ACTION>	<label>	<detail>
#   ---
#   CREATE=<n> UPDATE=<n> OK=<n> UNMANAGED=<n>
#
#   CREATE     listed, missing on GitHub
#   UPDATE     listed, on GitHub with a different colour or description
#   OK         listed and already matching
#   UNMANAGED  on GitHub, not listed; never touched
#
# Exit status:
#   0  labels match (or were made to match)
#   1  --check found a label to create or update
#   2  bad usage, an unreadable or malformed labels file, gh missing, or a
#      gh call that failed

set -euo pipefail

usage() { echo "usage: sync_labels.sh [--check] [labels_file]" >&2; }

CHECK=0
FILE=""
for arg in "$@"; do
  case "$arg" in
    --check) CHECK=1 ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "sync_labels.sh: unknown option: $arg" >&2; usage; exit 2 ;;
    *)
      if [[ -n "$FILE" ]]; then usage; exit 2; fi
      FILE="$arg"
      ;;
  esac
done
FILE="${FILE:-.github/labels.yml}"

if [[ ! -f "$FILE" ]]; then
  echo "sync_labels.sh: no labels file at $FILE" >&2
  exit 2
fi

# ------------------------------------------------------------ parse the file

# Strip surrounding double quotes and undo \" and \\ inside them. A bare value
# is taken as written, minus trailing spaces.
unquote() {
  local v="$1"
  v="${v%"${v##*[![:space:]]}"}"
  if [[ "$v" == \"*\" && ${#v} -ge 2 ]]; then
    v="${v:1:${#v}-2}"
    v="${v//\\\"/\"}"
    v="${v//\\\\/\\}"
  fi
  printf '%s' "$v"
}

die_at() { echo "sync_labels.sh: $FILE:$1: $2" >&2; exit 2; }

names=()
colors=()
descs=()
starts=()

# index_of <needle> <items...> — print the index of needle among items, or
# return 1. Lists here are a few dozen labels, so a linear scan is plenty, and
# it keeps this working on the bash 3.2 macOS ships (no associative arrays).
index_of() {
  local needle="$1" i=0
  shift
  for item in "$@"; do
    if [[ "$item" == "$needle" ]]; then echo "$i"; return 0; fi
    i=$((i + 1))
  done
  return 1
}

lineno=0
cur=-1
while IFS= read -r line || [[ -n "$line" ]]; do
  lineno=$((lineno + 1))
  line="${line%$'\r'}"
  [[ "$line" =~ ^[[:space:]]*(#.*)?$ ]] && continue

  if [[ "$line" =~ ^-[[:space:]]+name:[[:space:]]*(.*)$ ]]; then
    name="$(unquote "${BASH_REMATCH[1]}")"
    [[ -n "$name" ]] || die_at "$lineno" "empty name"
    if first="$(index_of "$name" ${names[@]+"${names[@]}"})"; then
      die_at "$lineno" "duplicate label \"$name\" (first at line ${starts[first]})"
    fi
    cur=${#names[@]}
    names+=("$name"); colors+=(""); descs+=(""); starts+=("$lineno")
  elif [[ "$line" =~ ^[[:space:]]+color:[[:space:]]*(.*)$ ]]; then
    (( cur >= 0 )) || die_at "$lineno" "color: before any - name:"
    colors[cur]="$(unquote "${BASH_REMATCH[1]}")"
  elif [[ "$line" =~ ^[[:space:]]+description:[[:space:]]*(.*)$ ]]; then
    (( cur >= 0 )) || die_at "$lineno" "description: before any - name:"
    descs[cur]="$(unquote "${BASH_REMATCH[1]}")"
  else
    die_at "$lineno" "not a - name:, color: or description: line"
  fi
done < "$FILE"

(( ${#names[@]} > 0 )) || { echo "sync_labels.sh: $FILE lists no labels" >&2; exit 2; }

for (( i = 0; i < ${#names[@]}; i++ )); do
  [[ "${colors[i]}" =~ ^[0-9a-fA-F]{6}$ ]] \
    || die_at "${starts[i]}" "label \"${names[i]}\" needs color: as six hex digits, got \"${colors[i]}\""
  colors[i]="$(tr '[:upper:]' '[:lower:]' <<<"${colors[i]}")"
done

# ------------------------------------------------------------ read GitHub

if ! command -v gh >/dev/null 2>&1; then
  echo "sync_labels.sh: gh is not installed — https://cli.github.com" >&2
  exit 2
fi

# @tsv escapes tabs and newlines inside a value, so each label is exactly one
# line. A repo with under 100 labels is one page, so one request.
if ! remote="$(gh api 'repos/{owner}/{repo}/labels?per_page=100' --paginate \
                 --jq '.[] | [.name, .color, (.description // "")] | @tsv')"; then
  echo "sync_labels.sh: could not list labels — is gh logged in (gh auth login) and is this a GitHub repo?" >&2
  exit 2
fi

# A label name goes into the URL path when it is updated, so escape everything
# but the unreserved characters. Byte by byte, so a multi-byte name survives.
urlencode() {
  local LC_ALL=C s="$1" out="" ch i
  for (( i = 0; i < ${#s}; i++ )); do
    ch="${s:i:1}"
    case "$ch" in
      [A-Za-z0-9._~-]) out+="$ch" ;;
      *) out+="$(printf '%%%02X' "'$ch")" ;;
    esac
  done
  printf '%s' "$out"
}

r_names=()
r_colors=()
r_descs=()
# Read with a field split that does not collapse runs of tabs, so an empty
# description stays in its column instead of shifting the next one over.
while IFS= read -r row; do
  [[ -n "$row" ]] || continue
  rn="${row%%$'\t'*}"; row="${row#*$'\t'}"
  rc="${row%%$'\t'*}"; rd="${row#*$'\t'}"
  r_names+=("$rn")
  r_colors+=("$(tr '[:upper:]' '[:lower:]' <<<"$rc")")
  r_descs+=("$rd")
done <<<"$remote"

# ------------------------------------------------------------ compare and act

n_create=0
n_update=0
n_ok=0
n_unmanaged=0

emit() { printf '%s\t%s\t%s\n' "$1" "$2" "$3"; }

for (( i = 0; i < ${#names[@]}; i++ )); do
  n="${names[i]}" c="${colors[i]}" d="${descs[i]}"
  if ! j="$(index_of "$n" ${r_names[@]+"${r_names[@]}"})"; then
    emit CREATE "$n" "#$c $d"
    n_create=$((n_create + 1))
    if (( ! CHECK )); then
      gh api -X POST 'repos/{owner}/{repo}/labels' \
          -f name="$n" -f color="$c" -f description="$d" >/dev/null \
        || { echo "sync_labels.sh: creating label \"$n\" failed" >&2; exit 2; }
    fi
  elif [[ "${r_colors[j]}" != "$c" || "${r_descs[j]}" != "$d" ]]; then
    detail=""
    [[ "${r_colors[j]}" != "$c" ]] && detail="color #${r_colors[j]} -> #$c"
    if [[ "${r_descs[j]}" != "$d" ]]; then
      detail="${detail:+$detail; }description \"${r_descs[j]}\" -> \"$d\""
    fi
    emit UPDATE "$n" "$detail"
    n_update=$((n_update + 1))
    if (( ! CHECK )); then
      gh api -X PATCH "repos/{owner}/{repo}/labels/$(urlencode "$n")" \
          -f color="$c" -f description="$d" >/dev/null \
        || { echo "sync_labels.sh: updating label \"$n\" failed" >&2; exit 2; }
    fi
  else
    emit OK "$n" ""
    n_ok=$((n_ok + 1))
  fi
done

for rn in ${r_names[@]+"${r_names[@]}"}; do
  if ! index_of "$rn" "${names[@]}" >/dev/null; then
    emit UNMANAGED "$rn" "not in $FILE; left as is"
    n_unmanaged=$((n_unmanaged + 1))
  fi
done

echo '---'
printf 'CREATE=%d UPDATE=%d OK=%d UNMANAGED=%d\n' "$n_create" "$n_update" "$n_ok" "$n_unmanaged"

if (( CHECK && n_create + n_update > 0 )); then
  exit 1
fi
exit 0
