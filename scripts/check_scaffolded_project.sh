#!/usr/bin/env bash
# check_scaffolded_project.sh
#
# Verifies that a repo scaffolded from this template actually came out the way
# init-project promises. The skill is seven phases of instructions; this script
# is the post-condition those phases are judged against.
#
# Run it on a real project right after init-project finishes (Phase 7 does),
# or any time later to confirm nothing has rotted back into template state.
# Running it on the un-initialized template is expected to fail — that is the
# point, and CI asserts it.
#
# Checks:
#
#   PLACEHOLDER      an unfilled template placeholder survived scaffolding
#                    ({PROJECT_NAME}, "init-project fills this in", a prompt
#                    Config value still reading "{set by init-project ...}")
#   CLAUDE_MD        CLAUDE.md's Project identity is still the HTML-comment
#                    placeholder rather than real content
#   EMPTY_SECTION    a section scaffolding is supposed to fill has a heading
#                    but no content under it — as useless as a placeholder,
#                    and it slips past the placeholder check precisely because
#                    the placeholder was removed
#   MISSING_FILE     a file every scaffolded project must have is absent
#                    (the founding brief at docs/foundation.md, or at
#                    docs/foundation/FOUNDING_BRIEF.md for a project that keeps
#                    its docs in a folder; .template-version; and the issue
#                    tracking the template ships ready to use: labels.yml,
#                    the issue forms, label-sync.yml and its two scripts)
#   TEMPLATE_RESIDUE a file belonging to the template itself was carried into
#                    the project (CHANGELOG.md still logging template releases;
#                    the docs/template/ folder of the template's own session
#                    records; SNAPSHOT_PATH still pointing into that folder)
#   GETTING_STARTED  README.md still carries the one-time "Getting started"
#                    section whose own instructions say to delete it
#   ADR_UNINDEXED    an ADR file with no row in docs/ADRs/README.md's Index
#   ADR_INDEX_STALE  an Index row naming an ADR file that does not exist
#   ADR_UNLINKED     an ADR file that CLAUDE.md's Decision log does not link
#   INHERITED_DOCS   check_inherited_docs.sh reports an unresolved hit
#
# Usage:
#   check_scaffolded_project.sh [project_root]   # defaults to current directory
#
# Output (to stdout), tab-separated:
#   <CHECK>	<path>	<detail>
#   ---
#   PROBLEMS=<n>
#
# Exit status:
#   0  the project looks properly scaffolded
#   1  at least one problem
#   2  bad usage

set -uo pipefail

ROOT="${1:-.}"

if [[ ! -d "$ROOT" ]]; then
  echo "check_scaffolded_project.sh: not a directory: $ROOT" >&2
  echo "usage: check_scaffolded_project.sh [project_root]" >&2
  exit 2
fi

ROOT="$(cd "$ROOT" && pwd)"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

n_problems=0

problem() {
  printf '%s\t%s\t%s\n' "$1" "$2" "$3"
  n_problems=$((n_problems + 1))
}

# ---------------------------------------------------------------- placeholders

# Placeholders the scaffolding phases are supposed to replace. Files under
# .claude/skills/ are skipped: a skill that documents a placeholder has to
# spell it out. Files under node_modules/ are skipped for the same reason:
# installed dependencies' docs aren't this repo's docs.
PLACEHOLDER_RE='\{PROJECT_NAME\}|init-project fills this in|\{set by init-project'

while IFS= read -r -d '' f; do
  while IFS=: read -r lineno text; do
    [[ -z "${lineno:-}" ]] && continue
    text="${text#"${text%%[![:space:]]*}"}"
    problem PLACEHOLDER "$(printf '%s' "${f#"$ROOT"/}"):$lineno" "${text:0:80}"
  done < <(grep -niE "$PLACEHOLDER_RE" "$f" || true)
done < <(
  find "$ROOT" \
    -type d \( -name .git -o -name node_modules -o -path "$ROOT/.claude/skills" \) -prune -o \
    -type f \( -name '*.md' -o -name '*.yml' -o -name '*.yaml' \) -print0
)

# ------------------------------------------------------------------- CLAUDE.md

CLAUDE_MD="$ROOT/CLAUDE.md"
if [[ ! -f "$CLAUDE_MD" ]]; then
  problem MISSING_FILE "CLAUDE.md" "every project needs one"
else
  # Pull the Project identity section and see whether anything survives once
  # HTML comments and blank lines are removed.
  identity=$(
    awk '
      /^## Project identity/ { inside = 1; next }
      inside && /^## / { exit }
      inside { print }
    ' "$CLAUDE_MD" | sed '/<!--/,/-->/d' | tr -d '[:space:]'
  )
  if [[ -z "$identity" ]]; then
    problem CLAUDE_MD "CLAUDE.md" "Project identity is still the placeholder — nothing but the HTML comment"
  fi
fi

# --------------------------------------------------------------- empty sections

# Read the body of a `## Heading` section with comments and whitespace removed.
section_body() {
  local file="$1" heading="$2"
  awk -v want="## $heading" '
    $0 == want { inside = 1; next }
    inside && /^## / { exit }
    inside { print }
  ' "$file" | sed '/<!--/,/-->/d' | tr -d '[:space:]'
}

README_MD="$ROOT/README.md"
if [[ -f "$README_MD" ]]; then
  for heading in Stack "Quick Start"; do
    if grep -q "^## $heading" "$README_MD" && [[ -z "$(section_body "$README_MD" "$heading")" ]]; then
      problem EMPTY_SECTION "README.md" "## $heading has a heading but no content"
    fi
  done
fi

# --------------------------------------------------------------- required files

# The founding brief has two accepted homes. init-project writes docs/foundation.md;
# a project that keeps its docs in a folder may hold the same brief as
# docs/foundation/FOUNDING_BRIEF.md. Either one satisfies the check, and nothing
# else under docs/foundation/ does.
if [[ ! -f "$ROOT/docs/foundation.md" && ! -f "$ROOT/docs/foundation/FOUNDING_BRIEF.md" ]]; then
  problem MISSING_FILE "docs/foundation.md" "no founding brief: init-project Phase 6 writes docs/foundation.md, or docs/foundation/FOUNDING_BRIEF.md in a project that keeps its docs in a folder"
fi

[[ -f "$ROOT/.template-version" ]] || \
  problem MISSING_FILE ".template-version" "records the template version this project came from"

# Issue tracking works in a new repo with no setup only because these files
# arrive with it, and init-project's Phase 3 keeps them. The issue-tracker
# skill, /roadmap and /session-start all stand on them.
for f in .github/labels.yml .github/workflows/label-sync.yml scripts/sync_labels.sh scripts/check_issues.sh; do
  [[ -f "$ROOT/$f" ]] || \
    problem MISSING_FILE "$f" "part of the issue tracking init-project keeps"
done
if ! compgen -G "$ROOT/.github/ISSUE_TEMPLATE/*.yml" >/dev/null; then
  problem MISSING_FILE ".github/ISSUE_TEMPLATE/" "no issue forms; init-project keeps the shipped ones"
fi

# ------------------------------------------------------------ template residue

# The template's own changelog logs template releases, not this project's. A
# project may keep a CHANGELOG.md of its own — only the inherited one is wrong.
if [[ -f "$ROOT/CHANGELOG.md" ]] && grep -qiE 'this template|stack-agnostic' "$ROOT/CHANGELOG.md"; then
  problem TEMPLATE_RESIDUE "CHANGELOG.md" "still logs template releases — a project's changelog starts at its own first version"
fi

# The template keeps its own session snapshots and reviews in docs/template/;
# init-project deletes the whole folder and points SNAPSHOT_PATH back at the
# project's own docs/session-history/. Checked as a folder, not by file name: the
# template adds a snapshot every session, so no list of names could stay current.
# A project's own snapshots live in docs/session-history/ and are never residue.
if [[ -d "$ROOT/docs/template" ]]; then
  problem TEMPLATE_RESIDUE "docs/template/" "the template's own session snapshots and reviews — init-project deletes this folder"
fi

if [[ -f "$CLAUDE_MD" ]]; then
  # First SNAPSHOT_PATH row of the Session Config table, value cell only. awk
  # reads the file directly and exits at the first match, so no pipe is involved.
  snapshot_path="$(awk -F'|' '/^\| *`SNAPSHOT_PATH` *\|/ { gsub(/[[:space:]`]/, "", $3); print $3; exit }' "$CLAUDE_MD")"
  snapshot_path="${snapshot_path#./}"
  snapshot_path="${snapshot_path%/}"
  case "$snapshot_path" in
    docs/template|docs/template/*)
      problem TEMPLATE_RESIDUE "CLAUDE.md" "SNAPSHOT_PATH is still $snapshot_path, the template's own records — a project's snapshots belong in docs/session-history/"
      ;;
  esac
fi

if [[ -f "$ROOT/README.md" ]] && grep -q '^## Getting started' "$ROOT/README.md"; then
  problem GETTING_STARTED "README.md" "the one-time Getting started section says to delete it once init is done"
fi

# ------------------------------------------------------------------------ ADRs

ADR_DIR="$ROOT/docs/ADRs"
ADR_README="$ADR_DIR/README.md"

if [[ -d "$ADR_DIR" ]]; then
  adr_files=()
  while IFS= read -r -d '' f; do
    adr_files+=("$(basename "$f")")
  done < <(find "$ADR_DIR" -maxdepth 1 -type f -name '*.md' ! -name 'README.md' -print0)

  for adr in "${adr_files[@]:-}"; do
    [[ -z "$adr" ]] && continue
    if [[ -f "$ADR_README" ]] && ! grep -qF "$adr" "$ADR_README"; then
      problem ADR_UNINDEXED "docs/ADRs/$adr" "no row in docs/ADRs/README.md's Index table"
    fi
    if [[ -f "$CLAUDE_MD" ]] && ! grep -qF "$adr" "$CLAUDE_MD"; then
      problem ADR_UNLINKED "docs/ADRs/$adr" "CLAUDE.md's Decision log does not link it"
    fi
  done

  # Index rows naming a file that is not there. Scoped to the Index section:
  # the naming-convention section above it spells out an example filename
  # (ADR-001-database-choice.md) that is illustrative, not a row.
  if [[ -f "$ADR_README" ]]; then
    while read -r named; do
      [[ -z "$named" ]] && continue
      [[ -f "$ADR_DIR/$named" ]] || \
        problem ADR_INDEX_STALE "docs/ADRs/README.md" "Index names $named, which does not exist"
    done < <(
      awk '
        /^## Index/ { inside = 1; next }
        inside && /^## / { exit }
        inside { print }
      ' "$ADR_README" | grep -oE 'ADR-[0-9]+[A-Za-z0-9_-]*\.md' | sort -u || true
    )
  fi
fi

# -------------------------------------------------------------- inherited docs

# Reuse the template's own sweep when it travelled with the project.
SWEEP="$ROOT/.claude/skills/init-project/scripts/check_inherited_docs.sh"
[[ -f "$SWEEP" ]] || SWEEP="$HERE/../.claude/skills/init-project/scripts/check_inherited_docs.sh"

if [[ -f "$SWEEP" ]]; then
  if ! sweep_out="$(bash "$SWEEP" "$ROOT" 2>&1)"; then
    summary="$(tail -n1 <<<"$sweep_out")"
    problem INHERITED_DOCS "(sweep)" "$summary — run check_inherited_docs.sh for detail"
  fi
fi

echo '---'
printf 'PROBLEMS=%d\n' "$n_problems"

if (( n_problems > 0 )); then
  exit 1
fi
exit 0
