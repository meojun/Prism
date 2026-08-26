#!/usr/bin/env bash
# Prepare a fresh server. Never contains secrets. Never launches experiments.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
echo "repository root: $ROOT"
echo
echo "1. Python environment"
if [ -x "$ROOT/prism-venv/bin/python" ]; then
  echo "   present: $ROOT/prism-venv"
else
  echo "   creating $ROOT/prism-venv"
  python3 -m venv "$ROOT/prism-venv" || { echo "   FAILED: python3 -m venv"; exit 1; }
  "$ROOT/prism-venv/bin/pip" install -q --upgrade pip
  echo "   install pinned dependencies:"
  echo "     $ROOT/prism-venv/bin/pip install -r $HERE/environment/pip-freeze.txt"
  echo "   (not run automatically: the lock contains CUDA wheels that must match the host driver)"
fi
echo
echo "2. Runtime source"
if [ -d "$ROOT/prism-research/.git" ]; then
  echo "   present. Rebuild the frozen runtime with:"
  echo "     git -C $ROOT/prism-research reset --hard \$(cat $ROOT/patches/lifecycle_containment/README.md | grep -o '[0-9a-f]\{40\}' | head -1)"
  echo "     git -C $ROOT/prism-research apply $ROOT/patches/lifecycle_containment/prism_research_worktree.patch"
else
  echo "   MISSING. See exp/manifests/prism_final/SOURCE_MANIFEST.md"
fi
echo
echo "3. External assets (NOT in Git)"
echo "   models   : exp/manifests/prism_final/MODEL_MANIFEST.md"
echo "   dataset  : exp/manifests/prism_final/DATASET_MANIFEST.md"
echo "   export HF_HOME=/workspace/.hf_home"
echo "   export SHAREGPT_JSON=/workspace/datasets/sharegpt/ShareGPT_V3_unfiltered_cleaned_split.json"
echo
echo "4. Secrets — set these yourself, they are never stored here"
sed -n '/^| \`/p' "$ROOT/exp/manifests/prism_final/ENVIRONMENT_MANIFEST.md" 2>/dev/null | grep -E "HUGGING_FACE|NTFY|GitHub" | sed 's/^/   /'
echo "   template: $HERE/example.env"
echo
echo "5. Next"
echo "   $HERE/verify_environment.sh     # is this machine compatible?"
echo "   $HERE/verify_artifacts.sh       # do the frozen results still validate?"
echo "   $HERE/smoke_test.sh             # can the stack start? (offline by default)"
echo "   $HERE/resume.sh --status        # what would run?"
echo
echo "BOOTSTRAP = DONE (no experiment was launched)"
