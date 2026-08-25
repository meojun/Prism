#!/bin/bash
# Build the 8 CALIBRATION traces (seeds 3,4 - rates 8,10), then freeze their hashes.
#
# One invocation of the generator emits BOTH the bursty and the steady trace
# for a (rate, seed) -- that is what makes the pair differ in arrival timing
# and nothing else. Ten invocations therefore produce twenty files.
#
# Both arms consume these exact files. Neither arm ever regenerates a trace.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
OUT=${1:-$ROOT/exp/workloads/4het-cal}
CFGDIR="$ROOT/exp/configs/v4het"
DURATION=${P4HET_DURATION:-420}
MODELS=model_3,model_4,model_5,model_6
PY=/workspace/prism-exp/prism-venv/bin/python
cd "$ROOT"
source "$SCRIPT_DIR/env.sh" >/dev/null 2>&1 || true
mkdir -p "$OUT"

SRC=${SHAREGPT_JSON:-/workspace/datasets/sharegpt/ShareGPT_V3_unfiltered_cleaned_split.json}
[ -f "$SRC" ] || { echo "FATAL: ShareGPT source missing at $SRC" >&2; exit 1; }

fail=0
for rate in 8 10; do
  for seed in 3 4; do
    if [ -s "$OUT/bursty_r${rate}_s${seed}.pkl" ] && [ -s "$OUT/steady_r${rate}_s${seed}.pkl" ]; then
      echo "[4het-cal] r${rate}_s${seed} already present"; continue
    fi
    echo "[4het-cal] building r${rate}_s${seed}"
    $PY "$SCRIPT_DIR/build_paired_workload.py" \
      --rate "$rate" --duration "$DURATION" --seed "$seed" \
      --models "$MODELS" --revisions "$CFGDIR/model_revisions.json" \
      --slo-base "$CFGDIR/slo_base.json" --sharegpt "$SRC" --outdir "$OUT" \
      >> "$OUT/build.log" 2>&1 \
      || { echo "[4het-cal] build FAILED for r${rate}_s${seed}" >&2; fail=1; }
  done
done
[ "$fail" = 0 ] || { echo "FATAL: a build failed; see $OUT/build.log" >&2; exit 1; }

mkdir -p "$ROOT/exp/results/4het-window-calibration"
$PY "$SCRIPT_DIR/p4het_freeze_workloads.py" --workloads "$OUT" \
  --out "$ROOT/exp/results/4het-window-calibration/CALIBRATION_TRACE_MANIFEST.json"
