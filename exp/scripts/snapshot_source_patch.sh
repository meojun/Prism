#!/bin/bash
# Snapshot the prism-research working tree as a single applyable patch.
#
#   ./snapshot_source_patch.sh <output-dir> [label]
#
# `prism-research/` is gitignored by this experiment repository (bootstrap.sh
# recreates it), so the only way a milestone commit can carry the source delta
# is as a patch file.  Untracked files are included via --intent-to-add, which
# is undone afterwards so the source index is left exactly as it was found.
set -euo pipefail

OUTDIR=${1:?usage: snapshot_source_patch.sh <output-dir> [label]}
LABEL=${2:-source snapshot}
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=${PRISM_REPO:-$(cd "$SCRIPT_DIR/../.." && pwd)/prism-research}

if ! git -C "$REPO" diff --cached --quiet; then
  echo "FATAL: $REPO has a staged index; refusing to touch it" >&2
  exit 1
fi

mkdir -p "$OUTDIR"
BASE=$(git -C "$REPO" rev-parse HEAD)
PATCH="$OUTDIR/prism_research_worktree.patch"

git -C "$REPO" add --intent-to-add -A
trap 'git -C "$REPO" reset --quiet' EXIT
git -C "$REPO" diff --binary HEAD > "$PATCH"
git -C "$REPO" reset --quiet
trap - EXIT

TRACKED=$(grep -c "^diff --git" "$PATCH" || true)
cat > "$OUTDIR/README.md" <<README
# $LABEL

\`prism-research/\` is intentionally gitignored by the experiment repository and
is recreated by \`bootstrap.sh\`. The complete source working-tree delta is
therefore stored in \`prism_research_worktree.patch\`.

Base source repository commit:

\`\`\`text
$BASE
\`\`\`

Apply from a clean checkout of that commit:

\`\`\`bash
git apply /path/to/Prism/$(realpath --relative-to="$(cd "$SCRIPT_DIR/../.." && pwd)" "$PATCH")
\`\`\`

Files in the patch: $TRACKED
Snapshot taken: $(date -u +%FT%TZ)
README

echo "wrote $PATCH ($TRACKED files, base $BASE)"
