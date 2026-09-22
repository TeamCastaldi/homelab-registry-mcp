#!/usr/bin/env bash
# check_inherited_docs.sh
#
# Sweeps a project repo for documentation that the stack-agnostic project
# template shipped and init-project never re-pointed at the real project.
# Three checks:
#
#   TEMPLATE_LANGUAGE  a doc still describing this repo as the template, or
#                      still carrying an unfilled template placeholder
#   BROKEN_LINK        a relative markdown link resolving to a path that does
#                      not exist
#   MISSING_PROMPT     a reference to a .prompt.md file that is not in
#                      .github/prompts/ (that folder is gone — its workflows
#                      became commands and skills — so any such reference is
#                      stale unless deliberately historical)
#
# Files under .claude/skills/ are skipped: a skill that explains the template
# is supposed to mention it.
#
# Every hit needs a human call. A deliberate historical mention — a decision
# log recording that the repo was scaffolded from a template — is a
# legitimate reason to leave one in place. An unexplained hit is not.
#
# When such a mention is permanent, mark its line with `inherited-docs-ok` and
# the checks skip it, so the sweep can stay a clean/dirty signal instead of
# accumulating known-good noise. In markdown, write the marker as an HTML
# comment:  <!-- inherited-docs-ok -->
# Use it only for a mention that is correct as written, never to silence one
# you have not looked at.
#
# Usage:
#   check_inherited_docs.sh [repo_root]      # defaults to the current directory
#
# Output (to stdout), tab-separated:
#   <CHECK>	<path>:<line>	<detail>
#   ---
#   TEMPLATE_LANGUAGE=<n> BROKEN_LINK=<n> MISSING_PROMPT=<n>
#
# Exit status:
#   0  nothing found
#   1  at least one hit
#   2  bad usage

set -euo pipefail

ROOT="${1:-.}"

if [[ ! -d "$ROOT" ]]; then
  echo "check_inherited_docs.sh: not a directory: $ROOT" >&2
  echo "usage: check_inherited_docs.sh [repo_root]" >&2
  exit 2
fi

ROOT="$(cd "$ROOT" && pwd)"

# Language that means "this repo is the template" rather than "this repo is a
# project", plus placeholders the scaffolding phase should have filled in.
TEMPLATE_RE='this template|the template itself|stack-agnostic|project template|from this template|init-project fills|\{PROJECT_NAME\}'

# A line carrying this marker is skipped by all three checks. It is for a
# mention that is deliberate and permanent — a migration table naming the
# prompt files that became skills has to spell them out, and would otherwise
# report as broken on every run forever. In markdown, write it as an HTML
# comment so it does not render:  <!-- inherited-docs-ok -->
SKIP_MARKER='inherited-docs-ok'

# Collect the files worth sweeping: prose and the config files that reference
# workflow paths. Read NUL-delimited so paths with spaces survive, via the
# read loop rather than `mapfile -d` — the latter needs bash 4.4, and macOS
# still ships 3.2.
FILES=()
while IFS= read -r -d '' f; do
  FILES+=("$f")
done < <(
  find "$ROOT" \
    -type d \( -name .git -o -path "$ROOT/.claude/skills" \) -prune -o \
    -type f \( -name '*.md' -o -name '*.yml' -o -name '*.yaml' \) -print0
)

if [[ ${#FILES[@]} -eq 0 ]]; then
  echo "check_inherited_docs.sh: no markdown or yaml files under $ROOT" >&2
  exit 2
fi

n_lang=0
n_link=0
n_prompt=0

rel() { printf '%s' "${1#"$ROOT"/}"; }

# ---------------------------------------------------------------- check 1
# Template language and unfilled placeholders.
for f in "${FILES[@]}"; do
  while IFS=: read -r lineno text; do
    [[ -z "${lineno:-}" ]] && continue
    # Trim leading whitespace for a readable one-line detail.
    text="${text#"${text%%[![:space:]]*}"}"
    printf 'TEMPLATE_LANGUAGE\t%s:%s\t%s\n' "$(rel "$f")" "$lineno" "${text:0:120}"
    n_lang=$((n_lang + 1))
  done < <(grep -niE "$TEMPLATE_RE" "$f" | grep -vF "$SKIP_MARKER" || true)
done

# ---------------------------------------------------------------- check 2
# Relative markdown links pointing at paths that do not exist.
for f in "${FILES[@]}"; do
  [[ "$f" == *.md ]] || continue
  dir="$(dirname "$f")"
  while IFS=: read -r lineno text; do
    [[ -z "${lineno:-}" ]] && continue
    # Extract each ](target) on the line.
    while read -r target; do
      [[ -z "$target" ]] && continue
      case "$target" in
        http://*|https://*|mailto:*|'#'*|'{'*|'<'*) continue ;;
      esac
      # Drop any #anchor and ?query suffix before testing the path.
      path="${target%%#*}"
      path="${path%%\?*}"
      [[ -z "$path" ]] && continue
      if [[ "$path" == /* ]]; then
        resolved="$ROOT$path"
      else
        resolved="$dir/$path"
      fi
      if [[ ! -e "$resolved" ]]; then
        printf 'BROKEN_LINK\t%s:%s\t%s\n' "$(rel "$f")" "$lineno" "$target"
        n_link=$((n_link + 1))
      fi
    done < <(printf '%s\n' "$text" | grep -oE '\]\([^)]+\)' | sed -E 's/^\]\(//; s/\)$//' || true)
  done < <(grep -nE '\]\([^)]+\)' "$f" | grep -vF "$SKIP_MARKER" || true)
done

# ---------------------------------------------------------------- check 3
# References to prompt files that are not in .github/prompts/. Nothing in the
# current layout creates that folder, so this resolves to "any .prompt.md
# mention that has not been marked as deliberate history".
for f in "${FILES[@]}"; do
  while IFS=: read -r lineno text; do
    [[ -z "${lineno:-}" ]] && continue
    while read -r name; do
      [[ -z "$name" ]] && continue
      if [[ ! -e "$ROOT/.github/prompts/$name" ]]; then
        printf 'MISSING_PROMPT\t%s:%s\t%s\n' "$(rel "$f")" "$lineno" "$name"
        n_prompt=$((n_prompt + 1))
      fi
    done < <(printf '%s\n' "$text" | grep -oE '[A-Za-z0-9_-]+\.prompt\.md' | sort -u || true)
  done < <(grep -nE '[A-Za-z0-9_-]+\.prompt\.md' "$f" | grep -vF "$SKIP_MARKER" || true)
done

echo '---'
printf 'TEMPLATE_LANGUAGE=%d BROKEN_LINK=%d MISSING_PROMPT=%d\n' \
  "$n_lang" "$n_link" "$n_prompt"

if (( n_lang + n_link + n_prompt > 0 )); then
  exit 1
fi
exit 0
