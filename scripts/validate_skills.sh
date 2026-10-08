#!/usr/bin/env bash
# validate_skills.sh
#
# Checks that every skill under .claude/skills/ is laid out the way Claude Code
# expects to find it, and within the limits Anthropic's skill authoring guide
# sets. A skill that fails these checks does not error at load time — it
# simply never triggers, or fails only when someone uploads it elsewhere, which
# is invisible until someone notices the skill "doesn't work." This script
# makes that failure loud instead.
#
# Checks, per skill directory:
#
#   MISSING_SKILL_MD    no SKILL.md (a renamed file like foo-SKILL.md never loads)
#   BAD_FRONTMATTER     SKILL.md does not open with a --- delimited YAML block
#   MISSING_NAME        no `name:` key in the frontmatter
#   MISSING_DESC        no `description:` key, or one with an empty value
#   NAME_MISMATCH       `name:` does not match the directory name
#   NAME_FORMAT         `name:` is not 1-64 lowercase letters, digits and hyphens
#   NAME_RESERVED       `name:` contains "anthropic" or "claude"
#   DESC_TOO_LONG       `description:` is over 1,024 characters
#   XML_IN_FRONTMATTER  `name:` or `description:` contains an XML tag
#   BODY_TOO_LONG       the body after the frontmatter is over 500 lines
#   STRAY_SKILL_ZIP     a packaged .skill archive is committed alongside
#
# And one warning, printed but not counted as a problem:
#
#   WARN                the body is over 20,000 characters, roughly the part of
#                       an invoked skill Claude Code keeps after compaction
#
# The name, description and body limits are the guide's:
#   https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
# claude.ai uploads and the Skills API enforce them; Claude Code itself is more
# lenient, so a skill can work here and still fail to upload. The compaction
# figure is from Claude Code's skills page:
#   https://code.claude.com/docs/en/skills
#
# A description is measured as YAML reads it: a folded (>) or literal (|) block
# joined into one value, quotes stripped, in characters rather than bytes.
#
# Usage:
#   validate_skills.sh [repo_root]      # defaults to the current directory
#
# Output (to stdout), tab-separated:
#   <CHECK>	<skill dir>	<detail>
#   ---
#   SKILLS=<n> PROBLEMS=<n> WARNINGS=<n>
#
# Exit status:
#   0  every skill is well-formed (warnings do not change this)
#   1  at least one problem
#   2  bad usage

set -euo pipefail

# The guide's frontmatter and body limits.
NAME_MAX=64
DESC_MAX=1024
BODY_MAX_LINES=500
# After compaction Claude Code re-attaches the first 5,000 tokens of each
# invoked skill. At about four characters a token, that is 20,000 characters;
# instructions past it are lost from a long session.
BODY_WARN_CHARS=20000

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
n_warnings=0

problem() {
  printf '%s\t%s\t%s\n' "$1" "$2" "$3"
  n_problems=$((n_problems + 1))
}

warn() {
  printf 'WARN\t%s\t%s\n' "$1" "$2"
  n_warnings=$((n_warnings + 1))
}

# Print the whole value of a top-level YAML key from the frontmatter block, as
# YAML would read it, closely enough to measure: a plain or quoted value with
# any indented continuation lines joined by spaces; a folded (>) block joined
# by spaces; a literal (|) block joined by newlines. Surrounding quotes are
# stripped. Prints nothing when the key is absent.
frontmatter_value() {
  local file="$1" key="$2"
  awk -v key="$key" -v sq="'" '
    NR == 1 && $0 != "---" { exit }
    NR == 1 { next }
    $0 == "---" { exit }
    collecting && ($0 ~ /^[ \t]/ || $0 == "") {
      line = $0
      gsub(/^[ \t]+|[ \t]+$/, "", line)
      if (style == "literal") {
        value = started ? value "\n" line : line
        started = 1
      } else if (line == "") {
        if (started) value = value "\n"
      } else {
        value = (started && value !~ /\n$/) ? value " " line : value line
        started = 1
      }
      next
    }
    collecting { exit }
    index($0, key ":") == 1 {
      v = substr($0, length(key) + 2)
      gsub(/^[ \t]+|[ \t]+$/, "", v)
      if (v ~ /^[>|][-+]?$/) {
        style = (substr(v, 1, 1) == "|") ? "literal" : "folded"
        value = ""
        started = 0
      } else {
        style = "plain"
        value = v
        started = (v != "")
      }
      collecting = 1
    }
    END {
      if (!collecting) exit
      sub(/\n+$/, "", value)
      if (style == "plain" && length(value) >= 2) {
        first = substr(value, 1, 1)
        last = substr(value, length(value), 1)
        if ((first == "\"" || first == sq) && last == first) {
          value = substr(value, 2, length(value) - 2)
          if (first == "\"") gsub(/\\"/, "\"", value)
        }
      }
      printf "%s", value
    }
  ' "$file"
}

# Everything after the closing --- of the frontmatter.
body_text() {
  awk 'NR == 1 { next } !closed && $0 == "---" { closed = 1; next } closed' "$1"
}

# Characters, not bytes, whatever the locale: drop UTF-8 continuation bytes,
# then count what is left. awk's length() counts bytes under mawk and the C
# locale, which would put an em dash at three.
char_count() {
  LC_ALL=C tr -d '\200-\277' | LC_ALL=C wc -c | tr -d ' '
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
  [[ -z "$desc" ]] && problem MISSING_DESC "$rel" "no description: in frontmatter, or an empty one"

  if [[ -n "$name" ]]; then
    if [[ "$name" != "$dir_name" ]]; then
      problem NAME_MISMATCH "$rel" "name: $name does not match directory $dir_name"
    fi
    if ! [[ "$name" =~ ^[a-z0-9-]{1,${NAME_MAX}}$ ]]; then
      problem NAME_FORMAT "$rel" "name: $name — must be 1-$NAME_MAX lowercase letters, digits and hyphens"
    fi
    if [[ "$name" == *anthropic* || "$name" == *claude* ]]; then
      problem NAME_RESERVED "$rel" "name: $name — must not contain \"anthropic\" or \"claude\""
    fi
  fi

  if [[ -n "$desc" ]]; then
    desc_chars="$(printf '%s' "$desc" | char_count)"
    if (( desc_chars > DESC_MAX )); then
      problem DESC_TOO_LONG "$rel" "description is $desc_chars characters — the limit is $DESC_MAX"
    fi
  fi

  tag="$(printf '%s\n%s\n' "$name" "$desc" | grep -oE '<[A-Za-z/!?][^>]*>' | head -n 1 || true)"
  if [[ -n "$tag" ]]; then
    problem XML_IN_FRONTMATTER "$rel" "name or description contains an XML tag: $tag"
  fi

  body_lines="$(body_text "$skill_md" | wc -l | tr -d ' ')"
  if (( body_lines > BODY_MAX_LINES )); then
    problem BODY_TOO_LONG "$rel" "body is $body_lines lines — keep it to $BODY_MAX_LINES and move detail into files it links"
  fi

  body_chars="$(body_text "$skill_md" | char_count)"
  if (( body_chars > BODY_WARN_CHARS )); then
    warn "$rel" "body is $body_chars characters — after compaction Claude Code keeps about the first $BODY_WARN_CHARS; keep must-not-lose instructions near the top"
  fi
done

echo '---'
printf 'SKILLS=%d PROBLEMS=%d WARNINGS=%d\n' "$n_skills" "$n_problems" "$n_warnings"

if (( n_problems > 0 )); then
  exit 1
fi
exit 0
