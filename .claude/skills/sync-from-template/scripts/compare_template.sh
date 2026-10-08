#!/usr/bin/env bash
# compare_template.sh
#
# Ephemeral sparse-clones a template repo and reports how each file under
# the given sync paths compares to the same path in the current project repo.
# Leaves the clone on disk (path printed as TEMP_CLONE=...) so the caller can
# diff and copy individual files before removing it.
#
# The clone is ephemeral rather than a persistent `template` git remote on
# purpose: the only thing that needs to know where the template lives is the
# skill's config file. If the template moves, one YAML value changes and every
# later sync picks it up, with no untracked remote URL to update as well. The
# cost is a fresh clone per run instead of an incremental fetch; if that ever
# becomes slow enough to matter, a persistent remote is the fallback.
#
# A sync path may be a directory or a single file. On top of the paths passed
# in, the template itself can name files that its commands and skills depend on
# but that live outside the synced folders — a check script under scripts/, say.
# It lists them in TOOLING_MANIFEST below, one path per line, `#` for comments.
# The list is read from the template commit, not the project, so a template that
# gains a dependency ships the line that pulls it: no downstream config changes.
# Anything not on the list stays untouched, which is the point — scripts/ also
# holds template-only files that a whole-folder sync would keep re-offering.
#
# Usage:
#   compare_template.sh <template_repo_url> <template_ref> <project_repo_root> <sync_path> [<sync_path> ...]
#
# Output (to stdout):
#   TEMP_CLONE=<path>
#   TEMPLATE_SHA=<short sha>
#   TEMPLATE_VERSION=<version>   from the template's .template-version
#   PROJECT_VERSION=<version>    from the project's .template-version
#   TOOLING_PATHS=<paths>        space-separated, from TOOLING_MANIFEST, or "none"
#   ---
#   <STATUS>\t<path relative to repo root>
#     STATUS is one of: NEW, CHANGED, SAME, LOCAL_ONLY
#
# Either version reads "unknown" when that side has no .template-version —
# a project scaffolded before the template carried one, or a template ref
# predating it. That is a reportable state, not an error.

set -euo pipefail

if [ "$#" -lt 4 ]; then
  echo "Usage: $0 <template_repo_url> <template_ref> <project_repo_root> <sync_path> [<sync_path> ...]" >&2
  exit 1
fi

TEMPLATE_URL="$1"; shift
TEMPLATE_REF="$1"; shift
PROJECT_ROOT="$1"; shift
SYNC_PATHS=("$@")

TOOLING_MANIFEST=".claude/skills/sync-from-template/tooling_paths.txt"

TMP_DIR=$(mktemp -d -t sync-from-template.XXXXXX)
echo "TEMP_CLONE=$TMP_DIR"

# --depth 1 --filter=blob:none keep this cheap against a real remote (a local
# filesystem clone will just ignore these two flags, which is harmless).
git clone --quiet --depth 1 --filter=blob:none --sparse --branch "$TEMPLATE_REF" "$TEMPLATE_URL" "$TMP_DIR"

# Tooling paths the template declares, read from the commit like the version
# below. A path already inside a sync path is dropped so it is not reported twice.
TOOLING_PATHS=()
while IFS= read -r line; do
  line="${line%%#*}"
  line="${line#"${line%%[![:space:]]*}"}"
  line="${line%"${line##*[![:space:]]}"}"
  [ -n "$line" ] || continue
  covered=0
  for sp in "${SYNC_PATHS[@]}"; do
    case "$line" in "$sp"|"$sp"/*) covered=1 ;; esac
  done
  [ "$covered" -eq 1 ] || TOOLING_PATHS+=("$line")
done < <(git -C "$TMP_DIR" show "HEAD:$TOOLING_MANIFEST" 2>/dev/null || true)

ALL_PATHS=("${SYNC_PATHS[@]}" ${TOOLING_PATHS[@]+"${TOOLING_PATHS[@]}"})

# Sparse checkout (cone mode) accepts directories only — handing it a file is a
# fatal error. So directories go to sparse-checkout, and each file is written
# into the clone from the commit, keeping its executable bit, so the caller can
# diff and cp from TEMP_CLONE exactly as it does for anything else. Files are
# written after the checkout is set, since setting it rewrites the working tree.
DIRS=()
FILES=()
for p in "${ALL_PATHS[@]}"; do
  case "$(git -C "$TMP_DIR" cat-file -t "HEAD:$p" 2>/dev/null || true)" in
    tree) DIRS+=("$p") ;;
    blob) FILES+=("$p") ;;
  esac
done
if [ "${#DIRS[@]}" -gt 0 ]; then
  git -C "$TMP_DIR" sparse-checkout set "${DIRS[@]}"
fi
for p in ${FILES[@]+"${FILES[@]}"}; do
  mkdir -p "$(dirname "$TMP_DIR/$p")"
  git -C "$TMP_DIR" show "HEAD:$p" > "$TMP_DIR/$p"
  if [ "$(git -C "$TMP_DIR" ls-tree HEAD -- "$p" | cut -c1-6)" = "100755" ]; then
    chmod +x "$TMP_DIR/$p"
  fi
done

TEMPLATE_SHA=$(git -C "$TMP_DIR" rev-parse --short HEAD)
echo "TEMPLATE_SHA=$TEMPLATE_SHA"

# Read the template's version from the commit rather than the working tree:
# the sparse checkout above is scoped to SYNC_PATHS, so a root-level file is
# not guaranteed to be on disk. Both sides report "unknown" rather than
# failing — a project scaffolded before the template was versioned simply has
# no marker, and the sync workflow reports that as its own case.
TEMPLATE_VERSION=$(git -C "$TMP_DIR" show "HEAD:.template-version" 2>/dev/null | head -n1 | tr -d '[:space:]')
echo "TEMPLATE_VERSION=${TEMPLATE_VERSION:-unknown}"

PROJECT_VERSION=""
if [ -f "$PROJECT_ROOT/.template-version" ]; then
  PROJECT_VERSION=$(head -n1 "$PROJECT_ROOT/.template-version" | tr -d '[:space:]')
fi
echo "PROJECT_VERSION=${PROJECT_VERSION:-unknown}"

if [ "${#TOOLING_PATHS[@]}" -gt 0 ]; then
  echo "TOOLING_PATHS=${TOOLING_PATHS[*]}"
else
  echo "TOOLING_PATHS=none"
fi

echo "---"

for sp in "${ALL_PATHS[@]}"; do
  TEMPLATE_PATH="$TMP_DIR/$sp"
  LOCAL_PATH="$PROJECT_ROOT/$sp"

  # A single file: compare it directly.
  if [ -f "$TEMPLATE_PATH" ]; then
    if [ ! -e "$LOCAL_PATH" ]; then
      echo -e "NEW\t$sp"
    elif ! cmp -s "$TEMPLATE_PATH" "$LOCAL_PATH"; then
      echo -e "CHANGED\t$sp"
    else
      echo -e "SAME\t$sp"
    fi
    continue
  fi
  if [ -f "$LOCAL_PATH" ]; then
    echo -e "LOCAL_ONLY\t$sp"
    continue
  fi

  # Files that exist in the template: NEW, CHANGED, or SAME.
  if [ -d "$TEMPLATE_PATH" ]; then
    while IFS= read -r -d '' f; do
      rel="${f#"$TEMPLATE_PATH"/}"
      local_f="$LOCAL_PATH/$rel"
      if [ ! -e "$local_f" ]; then
        echo -e "NEW\t$sp/$rel"
      elif ! cmp -s "$f" "$local_f"; then
        echo -e "CHANGED\t$sp/$rel"
      else
        echo -e "SAME\t$sp/$rel"
      fi
    done < <(find "$TEMPLATE_PATH" -type f -print0)
  fi

  # Files that exist locally but not in the template: LOCAL_ONLY.
  # These are reported only. Nothing in this skill deletes them.
  if [ -d "$LOCAL_PATH" ]; then
    while IFS= read -r -d '' f; do
      rel="${f#"$LOCAL_PATH"/}"
      template_f="$TEMPLATE_PATH/$rel"
      if [ ! -e "$template_f" ]; then
        echo -e "LOCAL_ONLY\t$sp/$rel"
      fi
    done < <(find "$LOCAL_PATH" -type f -print0)
  fi
done
