#!/usr/bin/env bash
# check_doc_claims.sh
#
# Cross-references factual claims in the repo's root documentation against what
# the repo actually contains. This is the gap check_inherited_docs.sh explicitly
# does not cover: that script reads text patterns and says so in its own header
# ("it cannot tell you that SECURITY.md lists ecosystems Dependabot is not
# actually watching"). This one resolves the claim against the source of truth.
#
# The two failures that prompted it were both live in this repo: SECURITY.md
# claimed Dependabot watched npm when no such block existed, and pointed at a
# requirements.txt that had never been committed.
#
# Three checks, over a fixed set of root docs:
#
#   ECOSYSTEM_CLAIM  a package ecosystem named in SECURITY.md that
#                    .github/dependabot.yml does not configure
#   MANIFEST_CLAIM   a dependency-manifest filename named in a root doc that
#                    does not exist on disk
#   MISSING_TARGET   a script path invoked in a fenced command block, or named
#                    by a Bash() allow rule in .claude/settings.json, that does
#                    not exist on disk
#
# A permission rule is a claim too: an allow rule naming a renamed script grants
# nothing while still reading like a grant, which is the same rot as a doc naming
# a file that moved. Matcher *behavior* cannot be checked from here — only that
# the path a rule names still exists.
#
# Scope is deliberately narrow. This checks a handful of high-signal claim
# shapes about *this repo*; it does not try to understand prose. Files under
# .claude/skills/ are excluded — a skill describing manifest options for stacks
# it might scaffold is writing instructions, not asserting a fact about here.
#
# A line marked `doc-claims-ok` is skipped, for a mention that is deliberately
# generic or hypothetical. In markdown, write it as an HTML comment so it does
# not render:  <!-- doc-claims-ok -->
# Use it for a claim you have read and judged correct, never to quiet one you
# have not looked at.
#
# Usage:
#   check_doc_claims.sh [repo_root]      # defaults to the current directory
#
# Output (to stdout), tab-separated:
#   <CHECK>	<path>:<line>	<detail>
#   ---
#   ECOSYSTEM_CLAIM=<n> MANIFEST_CLAIM=<n> MISSING_TARGET=<n>
#
# Exit status:
#   0  every claim resolves
#   1  at least one claim does not
#   2  bad usage

set -euo pipefail

ROOT="${1:-.}"

if [[ ! -d "$ROOT" ]]; then
  echo "check_doc_claims.sh: not a directory: $ROOT" >&2
  echo "usage: check_doc_claims.sh [repo_root]" >&2
  exit 2
fi

ROOT="$(cd "$ROOT" && pwd)"

SKIP_MARKER='doc-claims-ok'

# Root docs that make assertions about this repository. Deliberately a fixed
# list rather than a glob: a doc that describes what belongs in a folder is not
# asserting anything checkable, and sweeping it in only produces noise.
CLAIM_DOCS=(SECURITY.md CONTRIBUTING.md README.md)

# Dependency manifests worth resolving when a doc names one. A doc that says
# "a vulnerable dependency in requirements.txt" is claiming that file exists.
MANIFESTS=(requirements.txt requirements-dev.txt package.json pyproject.toml
           go.mod Cargo.toml Gemfile composer.json pom.xml build.gradle)

n_eco=0
n_manifest=0
n_target=0

# ---------------------------------------------------------------- check 1
# Ecosystems named in SECURITY.md vs what dependabot.yml configures.
SECURITY="$ROOT/SECURITY.md"
DEPENDABOT="$ROOT/.github/dependabot.yml"

if [[ -f "$SECURITY" && -f "$DEPENDABOT" ]]; then
  configured=$(grep -oE 'package-ecosystem:[[:space:]]*"?[a-z-]+"?' "$DEPENDABOT" \
    | sed -E 's/.*package-ecosystem:[[:space:]]*"?([a-z-]+)"?.*/\1/' | sort -u)

  # Ecosystem names as Dependabot spells them, plus the prose spellings a
  # human would write. Each maps to the canonical value in dependabot.yml.
  while IFS='|' read -r pattern canonical; do
    [[ -z "$pattern" ]] && continue
    while IFS=: read -r lineno text; do
      [[ -z "${lineno:-}" ]] && continue
      if ! grep -qx "$canonical" <<<"$configured"; then
        text="${text#"${text%%[![:space:]]*}"}"
        printf 'ECOSYSTEM_CLAIM\tSECURITY.md:%s\t%s named, but dependabot.yml configures only: %s\n' \
          "$lineno" "$canonical" "$(tr '\n' ' ' <<<"$configured" | sed 's/ $//')"
        n_eco=$((n_eco + 1))
      fi
    done < <(grep -niE "$pattern" "$SECURITY" | grep -vF "$SKIP_MARKER" || true)
  done <<'PATTERNS'
\bnpm\b|npm
\bpip\b|pip
\bgithub[ -]actions\b|github-actions
\bdocker\b|docker
\bcargo\b|cargo
\bgomod\b|gomod
\bbundler\b|bundler
\bmaven\b|maven
\bnuget\b|nuget
\bcomposer\b|composer
PATTERNS
fi

# ---------------------------------------------------------------- check 2
# Manifest filenames named in a root doc must exist.
for doc in "${CLAIM_DOCS[@]}"; do
  f="$ROOT/$doc"
  [[ -f "$f" ]] || continue
  for manifest in "${MANIFESTS[@]}"; do
    [[ -e "$ROOT/$manifest" ]] && continue
    while IFS=: read -r lineno text; do
      [[ -z "${lineno:-}" ]] && continue
      text="${text#"${text%%[![:space:]]*}"}"
      printf 'MANIFEST_CLAIM\t%s:%s\t%s named but not present in the repo\n' \
        "$doc" "$lineno" "$manifest"
      n_manifest=$((n_manifest + 1))
    done < <(grep -nF "$manifest" "$f" | grep -vF "$SKIP_MARKER" || true)
  done
done

# ---------------------------------------------------------------- check 3
# Script paths invoked in a root doc's command blocks must exist. Catches a
# documented command that rots when a script is renamed or moved.
for doc in "${CLAIM_DOCS[@]}"; do
  f="$ROOT/$doc"
  [[ -f "$f" ]] || continue
  while IFS=: read -r lineno text; do
    [[ -z "${lineno:-}" ]] && continue
    while read -r target; do
      [[ -z "$target" ]] && continue
      if [[ ! -e "$ROOT/$target" ]]; then
        printf 'MISSING_TARGET\t%s:%s\t%s\n' "$doc" "$lineno" "$target"
        n_target=$((n_target + 1))
      fi
    done < <(
      # Strip the leading separator per line with sed rather than tr: `tr -d`
      # would also delete the newlines between matches, leaving the final line
      # unterminated so `read` reports EOF and never runs the body.
      printf '%s\n' "$text" \
        | grep -oE '(^|[[:space:]])(\.?[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.(sh|py)\b' \
        | sed 's/^[[:space:]]*//' || true
    )
  done < <(grep -nE '\.(sh|py)\b' "$f" | grep -vF "$SKIP_MARKER" || true)
done

# ---------------------------------------------------------------- check 4
# Bash() allow rules in settings.json must name a script that exists.
SETTINGS="$ROOT/.claude/settings.json"
if [[ -f "$SETTINGS" ]] && command -v jq >/dev/null 2>&1; then
  while read -r target; do
    [[ -z "$target" ]] && continue
    if [[ ! -e "$ROOT/$target" ]]; then
      printf 'MISSING_TARGET\t.claude/settings.json\t%s named by an allow rule\n' "$target"
      n_target=$((n_target + 1))
    fi
  done < <(
    jq -r '.permissions.allow // [] | .[]' "$SETTINGS" 2>/dev/null \
      | sed -n 's/^Bash(bash \([^ )]*\).*/\1/p'
  )
fi

echo '---'
printf 'ECOSYSTEM_CLAIM=%d MANIFEST_CLAIM=%d MISSING_TARGET=%d\n' \
  "$n_eco" "$n_manifest" "$n_target"

if (( n_eco + n_manifest + n_target > 0 )); then
  exit 1
fi
exit 0
