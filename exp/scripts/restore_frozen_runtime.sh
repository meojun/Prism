#!/bin/bash
# Rebuild prism-research into EXACTLY the frozen runtime, and prove it.
#
#   bash exp/scripts/restore_frozen_runtime.sh [--verify-only]
#
# The freeze is defined by patches/final_baseline_ready/prism_research_worktree.patch
# against the base commit in that directory's README -- the complete working-tree
# delta, 36 files. bootstrap.sh's step 8b applies only the paper_faithful and
# paper_faithful_v3 patches, which is an EARLIER runtime: it has no
# `kvpr-global-v4` policy, so the Final Prism arm cannot start at all.
#
# Proof, not assertion: after applying, the worktree is re-snapshotted the same
# way the freeze was taken and the two patches must hash identically. That check
# covers the built source, which the freeze gate in final_stage.sh does not --
# that one hashes the patch file inside the experiment repo, so a wrongly built
# prism-research passes it.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
REPO=${PRISM_REPO:-$ROOT/prism-research}
PATCH="$ROOT/patches/final_baseline_ready/prism_research_worktree.patch"
BASE=$(sed -nE 's/^([0-9a-f]{40})$/\1/p' "$ROOT/patches/final_baseline_ready/README.md" | head -1)
WANT=$(sha256sum "$PATCH" | cut -d' ' -f1)
VERIFY_ONLY=0
[ "${1:-}" = "--verify-only" ] && VERIFY_ONLY=1

[ -d "$REPO/.git" ] || { echo "FATAL: no source checkout at $REPO" >&2; exit 1; }
[ -n "$BASE" ] || { echo "FATAL: no base commit in patches/final_baseline_ready/README.md" >&2; exit 1; }

snapshot() {   # regenerate the worktree patch into $1 and echo its sha256
  local tmp=$1
  rm -rf "$tmp"; mkdir -p "$tmp"
  PRISM_REPO="$REPO" bash "$SCRIPT_DIR/snapshot_source_patch.sh" "$tmp" verify >/dev/null 2>&1 \
    || { echo "snapshot failed" >&2; return 1; }
  sha256sum "$tmp/prism_research_worktree.patch" | cut -d' ' -f1
}

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

got=$(snapshot "$TMP/a" || echo none)
if [ "$got" = "$WANT" ]; then
  echo "runtime already matches the freeze ($WANT)"
  exit 0
fi
if [ "$VERIFY_ONLY" = "1" ]; then
  echo "RUNTIME MISMATCH: built source is $got, the freeze is $WANT" >&2
  echo "rebuild it with: bash exp/scripts/restore_frozen_runtime.sh" >&2
  exit 1
fi

echo "rebuilding $REPO at $BASE + the freeze patch (was $got)"
git -C "$REPO" reset --hard --quiet "$BASE" || exit 1
git -C "$REPO" clean -fdq || exit 1
find "$REPO" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null
git -C "$REPO" apply "$PATCH" || { echo "FATAL: the freeze patch did not apply" >&2; exit 1; }

got=$(snapshot "$TMP/b" || echo none)
if [ "$got" != "$WANT" ]; then
  echo "FATAL: rebuilt source does not reproduce the freeze ($got != $WANT)" >&2
  exit 1
fi
echo "runtime rebuilt and verified against the freeze ($WANT)"
