#!/usr/bin/env bash
# Compare this machine against the frozen baseline environment.
# Read-only. Exits 0 = READY, 1 = BLOCKED.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
MAN="$ROOT/exp/manifests/prism_final"
PY=${PRISM_PYTHON:-$ROOT/prism-venv/bin/python}
fail=0; warn=0
ok(){ printf '  PASS  %s\n' "$1"; }
no(){ printf '  FAIL  %s\n' "$1"; fail=1; }
wn(){ printf '  WARN  %s\n' "$1"; warn=1; }

echo "=== GPU ==="
if command -v nvidia-smi >/dev/null 2>&1; then
  n=$(nvidia-smi --query-gpu=name --format=csv,noheader | wc -l)
  [ "$n" = "2" ] && ok "GPU count 2" || no "GPU count $n, baseline used 2"
  nvidia-smi --query-gpu=name --format=csv,noheader | grep -q "A100-SXM4-80GB" \
    && ok "GPU model A100-SXM4-80GB" || no "GPU model differs from baseline"
  vram=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1)
  [ "${vram:-0}" -ge 80000 ] && ok "VRAM ${vram} MiB" || no "VRAM ${vram} MiB, baseline 81920"
  nvidia-smi topo -m 2>/dev/null | grep -qE "NV[0-9]" && ok "NVLink present" \
    || wn "no NVLink in topology; baseline used NV4 between GPU0 and GPU1"
  drv=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)
  echo "        driver $drv (baseline 580.173.02)"
else
  no "nvidia-smi not found"
fi

echo "=== Python / framework ==="
if [ -x "$PY" ]; then
  ok "python at $PY"
  "$PY" - <<'PYEOF'
import sys, torch
print(f"        python {sys.version.split()[0]} (baseline 3.10.21)")
print(f"        torch {torch.__version__} (baseline 2.4.0+cu121)")
print(f"        torch.version.cuda {torch.version.cuda} (baseline 12.1)")
print(f"        cuda available {torch.cuda.is_available()}  devices {torch.cuda.device_count()}")
PYEOF
else
  no "python interpreter not found at $PY (set PRISM_PYTHON)"
fi

echo "=== runtime source identity ==="
if [ -d "$ROOT/prism-research/.git" ]; then
  T=$(mktemp -d)
  if PRISM_REPO="$ROOT/prism-research" bash "$ROOT/exp/scripts/snapshot_source_patch.sh" "$T" verify >/dev/null 2>&1; then
    got=$(sha256sum "$T/prism_research_worktree.patch" | cut -d' ' -f1)
    want=$(cat "$ROOT/patches/lifecycle_containment/WORKTREE_PATCH_SHA256")
    [ "$got" = "$want" ] && ok "runtime matches the frozen baseline" \
      || no "runtime hash $got != frozen $want  (rebuild: see SOURCE_MANIFEST.md)"
  else
    no "could not snapshot the runtime source"
  fi
  rm -rf "$T"
else
  no "prism-research checkout missing"
fi

echo "=== models ==="
HF=${HF_HOME:-/workspace/.hf_home}
miss=0
"$PY" - "$MAN/MODEL_MANIFEST.json" "$HF" <<'PYEOF'
import json, sys
from pathlib import Path
man=json.load(open(sys.argv[1])); hf=Path(sys.argv[2]); bad=0
for m in man["models"]:
    p=hf/"hub"/f"models--{m['hf_id'].replace('/','--')}"/"snapshots"/m["revision"]
    print(("  PASS  " if p.exists() else "  FAIL  ")+f"{m['slot']} {m['hf_id']} @ {m['revision'][:12]}")
    bad += 0 if p.exists() else 1
sys.exit(1 if bad else 0)
PYEOF
[ $? -eq 0 ] || { fail=1; echo "        restore: see exp/manifests/prism_final/MODEL_MANIFEST.md"; }

echo "=== dataset ==="
SG=${SHAREGPT_JSON:-/workspace/datasets/sharegpt/ShareGPT_V3_unfiltered_cleaned_split.json}
if [ -f "$SG" ]; then
  ok "ShareGPT present at $SG"
  echo "        verify with: sha256sum \"$SG\"  (expected in DATASET_MANIFEST.md)"
else
  no "ShareGPT missing at $SG (set SHAREGPT_JSON; see DATASET_MANIFEST.md)"
fi

echo "=== secrets (names only) ==="
[ -n "${HUGGING_FACE_HUB_TOKEN:-}" ] && ok "HUGGING_FACE_HUB_TOKEN set" \
  || wn "HUGGING_FACE_HUB_TOKEN unset — needed only to download gated meta-llama models"
[ -n "${PRISM_NTFY_TOPIC:-}" ] && ok "PRISM_NTFY_TOPIC set" \
  || wn "PRISM_NTFY_TOPIC unset — notifications disabled, experiments unaffected"

echo
if [ "$fail" = 0 ]; then
  echo "NEW_SERVER_REPRO_STATUS = READY${warn:+  (with warnings)}"; exit 0
else
  echo "NEW_SERVER_REPRO_STATUS = BLOCKED"; exit 1
fi
