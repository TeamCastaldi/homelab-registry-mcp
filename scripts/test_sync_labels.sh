#!/usr/bin/env bash
# test_sync_labels.sh
#
# Tests for sync_labels.sh. Each case builds a throwaway directory with a
# labels file and a stub `gh` on PATH. The stub answers the script's
# `gh api … --jq` label listing from a JSON fixture, run through the --jq
# expression the script passes — so that expression is tested too, which needs
# jq — and logs every POST or PATCH instead of calling GitHub.
#
# Usage:
#   test_sync_labels.sh
#
# Exit status: 0 all passed, 1 at least one failure.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$HERE/sync_labels.sh"
REPO="$(cd "$HERE/.." && pwd)"

if ! command -v jq >/dev/null 2>&1; then
  echo "test_sync_labels.sh: needs jq for the gh stub" >&2
  exit 1
fi

pass=0
fail=0

# shellcheck disable=SC2001
indent() { sed 's/^/    /' <<<"$1"; }

# fixture <labels file body> <remote labels JSON> — prints the directory.
fixture() {
  local dir
  dir="$(mktemp -d -t sync-labels-test.XXXXXX)"
  mkdir -p "$dir/.github" "$dir/bin"
  printf '%s\n' "$1" > "$dir/.github/labels.yml"
  printf '%s\n' "$2" > "$dir/remote.json"
  : > "$dir/log"
  cat > "$dir/bin/gh" <<'STUB'
#!/usr/bin/env bash
[[ "${1:-}" == api ]] || { echo "gh stub: only gh api is expected, got: $*" >&2; exit 99; }
shift
method=GET expr='.' path=''
args=("$@")
while [[ $# -gt 0 ]]; do
  case "$1" in
    -X) method="$2"; shift ;;
    --jq) expr="$2"; shift ;;
    -f|-F) shift ;;
    --paginate) ;;
    -*) echo "gh stub: unexpected flag $1" >&2; exit 99 ;;
    *) path="$1" ;;
  esac
  shift
done
if [[ "$method" == GET && "$path" == 'repos/{owner}/{repo}/labels?per_page=100' ]]; then
  if [[ -n "${STUB_FAIL_LIST:-}" ]]; then echo "HTTP 401: Bad credentials" >&2; exit 1; fi
  jq -r "$expr" "$STUB_DIR/remote.json"
elif [[ "$method" == POST || "$method" == PATCH ]]; then
  printf '%s|' "${args[@]}" >> "$STUB_DIR/log"
  printf '\n' >> "$STUB_DIR/log"
else
  echo "gh stub: unexpected call: api ${args[*]}" >&2
  exit 99
fi
STUB
  chmod +x "$dir/bin/gh"
  printf '%s' "$dir"
}

# run <dir> [args...] — sets $out, $err and $code.
run() {
  local dir="$1"; shift
  out="$(cd "$dir" && STUB_DIR="$dir" PATH="$dir/bin:$PATH" "$SUT" "$@" 2>"$dir/stderr")"
  code=$?
  err="$(cat "$dir/stderr")"
}

ok()  { pass=$((pass + 1)); }
bad() {
  fail=$((fail + 1))
  echo "FAIL: $1"
  echo "  exit $code; stdout:"
  indent "$out"
  [[ -n "$err" ]] && { echo "  stderr:"; indent "$err"; }
  [[ -n "${2:-}" && -s "$2/log" ]] && { echo "  gh log:"; indent "$(cat "$2/log")"; }
}

# A line is a hit when its first field is the action and it contains needle.
has_line() { awk -F'\t' -v a="$2" -v n="$3" '$1 == a && index($0, n) { f = 1 } END { exit !f }' <<<"$1"; }
logged()   { grep -qF -- "$2" "$1/log"; }

ONE='- name: "status:backlog"
  color: "ededed"
  description: "Noted, not yet prioritized"'

# ------------------------------------------------------------------ actions

d="$(fixture "$ONE" '[]')"; run "$d"
if has_line "$out" CREATE "status:backlog" && logged "$d" '-X|POST|repos/{owner}/{repo}/labels|-f|name=status:backlog|-f|color=ededed|-f|description=Noted, not yet prioritized|'; then ok; else bad "a missing label is created with its colour and description" "$d"; fi
rm -rf "$d"

d="$(fixture "$ONE" '[{"name":"status:backlog","color":"000000","description":"Noted, not yet prioritized"}]')"; run "$d"
if has_line "$out" UPDATE "color #000000 -> #ededed" && logged "$d" '-X|PATCH|repos/{owner}/{repo}/labels/status%3Abacklog|-f|color=ededed|'; then ok; else bad "a drifted colour is updated" "$d"; fi
rm -rf "$d"

d="$(fixture "$ONE" '[{"name":"status:backlog","color":"ededed","description":"old words"}]')"; run "$d"
if has_line "$out" UPDATE 'description "old words" -> "Noted, not yet prioritized"' && logged "$d" '-X|PATCH|repos/{owner}/{repo}/labels/status%3Abacklog|'; then ok; else bad "a drifted description is updated" "$d"; fi
rm -rf "$d"

d="$(fixture "$ONE" '[{"name":"status:backlog","color":"EDEDED","description":"Noted, not yet prioritized"}]')"; run "$d"
if has_line "$out" OK "status:backlog" && [[ ! -s "$d/log" && $code -eq 0 ]]; then ok; else bad "a matching label (colour case aside) gets no write" "$d"; fi
rm -rf "$d"

d="$(fixture "$ONE" '[{"name":"status:backlog","color":"ededed","description":"Noted, not yet prioritized"},{"name":"good first issue","color":"7057ff","description":""}]')"; run "$d"
if has_line "$out" UNMANAGED "good first issue" && [[ ! -s "$d/log" ]]; then ok; else bad "an unlisted label is reported by its full name and never touched" "$d"; fi
rm -rf "$d"

# ------------------------------------------------------------------ --check

d="$(fixture "$ONE" '[]')"; run "$d" --check
if [[ $code -eq 1 ]] && has_line "$out" CREATE "status:backlog" && [[ ! -s "$d/log" ]]; then ok; else bad "--check reports drift, exits 1 and writes nothing" "$d"; fi
rm -rf "$d"

d="$(fixture "$ONE" '[{"name":"status:backlog","color":"ededed","description":"Noted, not yet prioritized"}]')"; run "$d" --check
if [[ $code -eq 0 ]] && grep -q '^CREATE=0 UPDATE=0 OK=1 UNMANAGED=0$' <<<"$out"; then ok; else bad "--check exits 0 when in sync" "$d"; fi
rm -rf "$d"

d="$(fixture "$ONE" '[]')"; run "$d"
if [[ $code -eq 0 ]]; then ok; else bad "applying drift exits 0" "$d"; fi
rm -rf "$d"

# ------------------------------------------------------------------ parsing

d="$(fixture '- name: "status:blocked"
  color: "5319e7"
  description: "Waiting: see \"Dependencies\""' '[]')"; run "$d"
if logged "$d" '-f|description=Waiting: see "Dependencies"|'; then ok; else bad "a description keeps its colon and escaped quotes" "$d"; fi
rm -rf "$d"

d="$(fixture '# a comment

- name: tooling
  color: bfdadc
  description: Dev tooling   ' '[]')"; run "$d"
if logged "$d" '-f|name=tooling|-f|color=bfdadc|-f|description=Dev tooling|'; then ok; else bad "bare values parse, comments and trailing spaces are dropped" "$d"; fi
rm -rf "$d"

d="$(fixture '- name: "a"
  color: "ABCDEF"
  description: ""' '[{"name":"a","color":"abcdef","description":null}]')"; run "$d" --check
if [[ $code -eq 0 ]]; then ok; else bad "an upper-case colour matches GitHub's lower case, and a null description matches an empty one" "$d"; fi
rm -rf "$d"

d="$(fixture '- name: "good first issue"
  color: "7057ff"
  description: "Newcomers"' '[{"name":"good first issue","color":"000000","description":"Newcomers"}]')"; run "$d"
if logged "$d" '-X|PATCH|repos/{owner}/{repo}/labels/good%20first%20issue|'; then ok; else bad "a label name is URL-encoded in the update path" "$d"; fi
rm -rf "$d"

# ------------------------------------------------------------------ exit 2

d="$(fixture "$ONE" '[]')"; rm "$d/.github/labels.yml"; run "$d"
if [[ $code -eq 2 ]]; then ok; else bad "exit 2 when the labels file is missing" "$d"; fi
rm -rf "$d"

d="$(fixture '- name: "status:backlog"
  description: "no colour"' '[]')"; run "$d"
if [[ $code -eq 2 && "$err" == *":1:"*"color"* && ! -s "$d/log" ]]; then ok; else bad "exit 2 on an entry with no colour, naming its line" "$d"; fi
rm -rf "$d"

d="$(fixture '- name: "x"
  color: "#ededed"
  description: ""' '[]')"; run "$d"
if [[ $code -eq 2 ]]; then ok; else bad "exit 2 on a colour with a leading #" "$d"; fi
rm -rf "$d"

d="$(fixture "$ONE
$ONE" '[]')"; run "$d"
if [[ $code -eq 2 && "$err" == *":4:"*"duplicate"* ]]; then ok; else bad "exit 2 on a duplicate name, naming the second line" "$d"; fi
rm -rf "$d"

d="$(fixture "$ONE
  colour: \"ededed\"" '[]')"; run "$d"
if [[ $code -eq 2 && "$err" == *":4:"* ]]; then ok; else bad "exit 2 on a line it does not understand" "$d"; fi
rm -rf "$d"

d="$(fixture '# nothing here' '[]')"; run "$d"
if [[ $code -eq 2 ]]; then ok; else bad "exit 2 on a file that lists no labels" "$d"; fi
rm -rf "$d"

d="$(fixture "$ONE" '[]')"; out="$(cd "$d" && STUB_DIR="$d" STUB_FAIL_LIST=1 PATH="$d/bin:$PATH" "$SUT" 2>&1)"; code=$?; err=""
if [[ $code -eq 2 && "$out" == *"gh auth login"* ]]; then ok; else bad "exit 2 with a hint when gh cannot list labels" "$d"; fi
rm -rf "$d"

# No gh anywhere on PATH: give the script only the one tool it needs.
d="$(fixture "$ONE" '[]')"; mkdir -p "$d/nogh"; ln -s "$(command -v tr)" "$d/nogh/tr"
out="$(cd "$d" && PATH="$d/nogh" "$BASH" "$SUT" 2>&1)"; code=$?; err=""
if [[ $code -eq 2 && "$out" == *"not installed"* ]]; then ok; else bad "exit 2 when gh is not installed" "$d"; fi
rm -rf "$d"

d="$(fixture "$ONE" '[]')"; run "$d" --bogus
if [[ $code -eq 2 ]]; then ok; else bad "exit 2 on an unknown option" "$d"; fi
rm -rf "$d"

# ------------------------------------------------------------------ the shipped file

d="$(fixture "$ONE" '[]')"; cp "$REPO/.github/labels.yml" "$d/.github/labels.yml"; run "$d" --check
want="$(grep -c '^- name:' "$REPO/.github/labels.yml")"
if [[ $code -eq 1 ]] && grep -q "^CREATE=$want UPDATE=0 OK=0 UNMANAGED=0$" <<<"$out"; then ok; else bad "the shipped .github/labels.yml parses: all $want labels reported" "$d"; fi
rm -rf "$d"

# ---------------------------------------------------------------------- summary

echo "---"
echo "passed=$pass failed=$fail"
(( fail == 0 ))
