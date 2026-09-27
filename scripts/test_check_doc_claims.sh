#!/usr/bin/env bash
# test_check_doc_claims.sh
#
# Tests for check_doc_claims.sh. Each case builds a throwaway fixture repo and
# asserts on the tab-separated output.
#
# Usage:
#   test_check_doc_claims.sh
#
# Exit status: 0 all passed, 1 at least one failure.

# The fixtures below are markdown, not shell: backticks inside single quotes are
# code spans and fenced blocks being written verbatim to a file, never command
# substitution. Writing them faithfully is the point — the checks under test
# read real documentation shapes.
# shellcheck disable=SC2016

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$HERE/check_doc_claims.sh"

pass=0
fail=0

# Indent captured output so a failure's detail reads as a block under its
# heading. SC2001 suggests ${var//from/to}, which cannot anchor per line.
# shellcheck disable=SC2001
indent() { sed 's/^/    /'  <<<"$1"; }

fixture() {
  local dir
  dir="$(mktemp -d -t doc-claims-test.XXXXXX)"
  mkdir -p "$dir/.github" "$dir/scripts"
  # A dependabot config every fixture shares unless it overwrites it.
  printf 'version: 2\nupdates:\n  - package-ecosystem: "github-actions"\n  - package-ecosystem: "pip"\n' \
    > "$dir/.github/dependabot.yml"
  printf '%s' "$dir"
}

assert_hit() {
  local name="$1" dir="$2" check="$3" needle="$4" out
  out="$("$SUT" "$dir" 2>&1)" || true
  if grep -q "^$check	" <<<"$out" && grep -qF "$needle" <<<"$out"; then
    pass=$((pass + 1))
  else
    fail=$((fail + 1))
    echo "FAIL: $name"
    echo "  expected a $check hit containing: $needle"
    echo "  got:"
    indent "$out"
  fi
  rm -rf "$dir"
}

assert_no_hit() {
  local name="$1" dir="$2" check="$3" out
  out="$("$SUT" "$dir" 2>&1)" || true
  if grep -q "^$check	" <<<"$out"; then
    fail=$((fail + 1))
    echo "FAIL: $name"
    echo "  expected no $check hit, got:"
    indent "$out"
  else
    pass=$((pass + 1))
  fi
  rm -rf "$dir"
}

assert_exit() {
  local name="$1" dir="$2" want="$3" got
  "$SUT" "$dir" >/dev/null 2>&1
  got=$?
  if [[ "$got" == "$want" ]]; then
    pass=$((pass + 1))
  else
    fail=$((fail + 1))
    echo "FAIL: $name — expected exit $want, got $got"
  fi
  rm -rf "$dir"
}

# ---------------------------------------------------------------- exit status

d="$(fixture)"; echo "# Clean" > "$d/README.md"
assert_exit "repo with no claims exits 0" "$d" 0

d="$(fixture)"; echo "Dependabot monitors npm weekly." > "$d/SECURITY.md"
assert_exit "unbacked claim exits 1" "$d" 1

d="$(mktemp -d)"
assert_exit "missing directory argument path exits 2" "/nonexistent-$$" 2
rm -rf "$d"

# ------------------------------------------------------------ ecosystem claims

# The exact bug this script was written for: SECURITY.md claimed npm.
d="$(fixture)"; echo "Dependabot monitors pip, npm, and GitHub Actions weekly." > "$d/SECURITY.md"
assert_hit "flags an ecosystem dependabot does not configure" "$d" ECOSYSTEM_CLAIM "npm named"

d="$(fixture)"; echo "Dependabot monitors pip and GitHub Actions weekly." > "$d/SECURITY.md"
assert_no_hit "accepts ecosystems dependabot does configure" "$d" ECOSYSTEM_CLAIM

d="$(fixture)"; echo "Dependabot watches github-actions." > "$d/SECURITY.md"
assert_no_hit "accepts the hyphenated spelling" "$d" ECOSYSTEM_CLAIM

d="$(fixture)"; echo "One day we may add npm. <!-- doc-claims-ok -->" > "$d/SECURITY.md"
assert_no_hit "doc-claims-ok suppresses a hypothetical ecosystem" "$d" ECOSYSTEM_CLAIM

d="$(fixture)"; echo "Docker images are not scanned." > "$d/SECURITY.md"
assert_hit "flags docker when unconfigured" "$d" ECOSYSTEM_CLAIM "docker named"

# ------------------------------------------------------------- manifest claims

# The second live bug: a requirements.txt that was never committed.
d="$(fixture)"; echo 'A vulnerable dependency in `requirements.txt`.' > "$d/SECURITY.md"
assert_hit "flags a manifest that does not exist" "$d" MANIFEST_CLAIM "requirements.txt named"

d="$(fixture)"; echo 'A vulnerable dependency in `requirements-dev.txt`.' > "$d/SECURITY.md"
printf 'ruff==1.0\n' > "$d/requirements-dev.txt"
assert_no_hit "accepts a manifest that exists" "$d" MANIFEST_CLAIM

d="$(fixture)"; echo 'Projects often use package.json. <!-- doc-claims-ok -->' > "$d/README.md"
assert_no_hit "doc-claims-ok suppresses a generic manifest mention" "$d" MANIFEST_CLAIM

d="$(fixture)"; mkdir -p "$d/.claude/skills/demo"
echo 'Write pyproject.toml or package.json as the stack needs.' > "$d/.claude/skills/demo/SKILL.md"
echo "# Clean" > "$d/README.md"
assert_no_hit "ignores manifest mentions inside skills" "$d" MANIFEST_CLAIM

# --------------------------------------------------------------- script targets

# Regression guard: an earlier version piped through `tr -d '[:space:]'`, which
# ate the trailing newline so `read` hit EOF and this check never fired at all.
d="$(fixture)"; printf 'Run:\n```bash\nbash scripts/gone.sh\n```\n' > "$d/CONTRIBUTING.md"
assert_hit "flags a command naming a missing script" "$d" MISSING_TARGET "scripts/gone.sh"

d="$(fixture)"; printf 'Run:\n```bash\nbash scripts/here.sh\n```\n' > "$d/CONTRIBUTING.md"
touch "$d/scripts/here.sh"
assert_no_hit "accepts a command naming a real script" "$d" MISSING_TARGET

d="$(fixture)"
printf 'Run:\n```bash\nbash scripts/a.sh && python3 scripts/b.py\n```\n' > "$d/CONTRIBUTING.md"
touch "$d/scripts/a.sh"
assert_hit "finds the second target on a line with two" "$d" MISSING_TARGET "scripts/b.py"

d="$(fixture)"; printf 'Run:\n```bash\nbash scripts/gone.sh <!-- doc-claims-ok -->\n```\n' > "$d/CONTRIBUTING.md"
assert_no_hit "doc-claims-ok suppresses a script target" "$d" MISSING_TARGET

# ------------------------------------------------------- settings allow rules

# A permission rule is a claim: an allow rule naming a renamed script grants
# nothing while still reading like a grant.
settings() { mkdir -p "$1/.claude"; printf '{"permissions":{"allow":["Bash(bash %s *)"]}}\n' "$2" > "$1/.claude/settings.json"; }

d="$(fixture)"; settings "$d" "scripts/renamed_away.sh"
assert_hit "flags an allow rule naming a missing script" "$d" MISSING_TARGET "scripts/renamed_away.sh"

d="$(fixture)"; settings "$d" "scripts/present.sh"; touch "$d/scripts/present.sh"
assert_no_hit "accepts an allow rule naming a real script" "$d" MISSING_TARGET

d="$(fixture)"; mkdir -p "$d/.claude"
printf '{"permissions":{"deny":["Read(**/.env)"]},"hooks":{}}\n' > "$d/.claude/settings.json"
assert_no_hit "settings with no allow rules is fine" "$d" MISSING_TARGET

# ---------------------------------------------------------------------- summary

echo "---"
echo "passed=$pass failed=$fail"
(( fail == 0 ))
