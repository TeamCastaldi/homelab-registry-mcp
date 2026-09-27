#!/usr/bin/env bash
# validate_skills.sh
#
# Checks that every skill under .claude/skills/ is laid out the way Claude Code
# expects to find it. A skill that fails these checks does not error at load
# time — it simply never triggers, which is invisible until someone notices the
# skill "doesn't work." This script makes that failure loud instead.
#
# Checks, per skill directory:
#
#   MISSING_SKILL_MD   no SKILL.md (a renamed file like foo-SKILL.md never loads)
#   BAD_FRONTMATTER    SKILL.md does not open with a --- delimited YAML block
#   MISSING_NAME       no `name:` key in the frontmatter
#   MISSING_DESC       no `description:` key in the frontmatter
#   NAME_MISMATCH      `name:` does not match the directory name
#   STRAY_SKILL_ZIP    a packaged .skill archive is committed alongside
#
# Usage:
#   validate_skills.sh [repo_root]      # defaults to the current directory
#
# Output (to stdout), tab-separated:
#   <CHECK>	<skill dir>	<detail>
#   ---
#   SKILLS=<n> PROBLEMS=<n>
#
# Exit status:
#   0  every skill is well-formed
#   1  at least one problem
#   2  bad usage

set -euo pipefail

ROOT="${1:-.}"

if [[ ! -d "$ROOT" ]]; then
  echo "validate_skills.sh: not a directory: $ROOT" >&2
  echo "usage: validate_skills.sh [repo_root]" >&2
  exit 2
fi

ROOT="$(cd "$ROOT" && pwd)"
SKILLS_DIR="$ROOT/.claude/skills"

if [[ ! -d "$SKILLS_DIR" ]]; then
  echo "validate_skills.sh: no .claude/skills directory under $ROOT" >&2
  exit 2
fi

n_skills=0
n_problems=0

problem() {
  printf '%s\t%s\t%s\n' "$1" "$2" "$3"
  n_problems=$((n_problems + 1))
}

# Read the value of a top-level YAML key from the frontmatter block. Handles
# both `key: value` and the folded `key: >` form, where the value starts on the
# following lines; for the folded form we only need to know a value exists, so
# returning the first continuation line is enough.
frontmatter_value() {
  local file="$1" key="$2"
  awk -v key="$key" '
    NR == 1 && $0 != "---" { exit }
    NR == 1 { in_fm = 1; next }
    in_fm && $0 == "---" { exit }
    in_fm && index($0, key ":") == 1 {
      value = substr($0, length(key) + 2)
      gsub(/^[ \t]+|[ \t]+$/, "", value)
      if (value == ">" || value == "|" || value == ">-" || value == "|-") { folded = 1; next }
      print value
      exit
    }
    folded {
      gsub(/^[ \t]+|[ \t]+$/, "", $0)
      if ($0 != "") { print $0; exit }
    }
  ' "$file"
}

for skill_dir in "$SKILLS_DIR"/*/; do
  [[ -d "$skill_dir" ]] || continue
  skill_dir="${skill_dir%/}"
  dir_name="$(basename "$skill_dir")"
  rel=".claude/skills/$dir_name"
  n_skills=$((n_skills + 1))

  # A committed .skill archive is a second, drifting copy that nothing loads.
  while IFS= read -r -d '' zip; do
    problem STRAY_SKILL_ZIP "$rel" "$(basename "$zip")"
  done < <(find "$skill_dir" -maxdepth 1 -type f -name '*.skill' -print0)

  skill_md="$skill_dir/SKILL.md"
  if [[ ! -f "$skill_md" ]]; then
    # Name the near-miss if there is one — that is the usual cause.
    near="$(find "$skill_dir" -maxdepth 1 -type f -name '*SKILL.md' -print -quit)"
    if [[ -n "$near" ]]; then
      problem MISSING_SKILL_MD "$rel" "found $(basename "$near") — must be exactly SKILL.md"
    else
      problem MISSING_SKILL_MD "$rel" "no SKILL.md"
    fi
    continue
  fi

  if [[ "$(head -n 1 "$skill_md")" != "---" ]]; then
    problem BAD_FRONTMATTER "$rel" "SKILL.md does not open with ---"
    continue
  fi

  name="$(frontmatter_value "$skill_md" name)"
  desc="$(frontmatter_value "$skill_md" description)"

  [[ -z "$name" ]] && problem MISSING_NAME "$rel" "no name: in frontmatter"
  [[ -z "$desc" ]] && problem MISSING_DESC "$rel" "no description: in frontmatter"

  if [[ -n "$name" && "$name" != "$dir_name" ]]; then
    problem NAME_MISMATCH "$rel" "name: $name does not match directory $dir_name"
  fi
done

echo '---'
printf 'SKILLS=%d PROBLEMS=%d\n' "$n_skills" "$n_problems"

if (( n_problems > 0 )); then
  exit 1
fi
exit 0
