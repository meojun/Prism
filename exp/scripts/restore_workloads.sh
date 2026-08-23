#!/bin/bash
# Rebuild the 24 canonical evaluation workloads on a new machine, and prove
# they are the same ones.
#
# The traces are not distributed -- they carry raw ShareGPT text, some of which
# contains real leaked credentials. They are rebuilt instead. The builder is
# deterministic in (rate, duration, seed, slo_base, sharegpt source), so a
# correct rebuild reproduces the canonical files byte for byte; anything else
# is a mismatch and stops here rather than quietly running different work.
#
#   bash exp/scripts/restore_workloads.sh [outdir]
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
OUT=${1:-$ROOT/exp/workloads/final-evaluation}
MANIFEST="$ROOT/exp/final-handoff/workloads_manifest.json"
cd "$ROOT"
source "$SCRIPT_DIR/env.sh" >/dev/null 2>&1 || true
PY=$(command -v python3)

[ -f "$MANIFEST" ] || { echo "FATAL: no workload manifest at $MANIFEST" >&2; exit 1; }

SRC=${SHAREGPT_JSON:-$DATASETS/sharegpt/ShareGPT_V3_unfiltered_cleaned_split.json}
want=$($PY -c "import json;print(json.load(open('$MANIFEST'))['sharegpt_source']['sha256'])")
if [ ! -f "$SRC" ]; then
  echo "FATAL: the ShareGPT source is not present at $SRC" >&2
  echo "Get it (ungated, 673 MB):" >&2
  $PY -c "import json;print('  ' + json.load(open('$MANIFEST'))['sharegpt_source']['download'])" >&2
  exit 1
fi
echo "[restore] hashing the ShareGPT source (673 MB, ~15 s)"
got=$(sha256sum "$SRC" | cut -d' ' -f1)
if [ "$got" != "$want" ]; then
  echo "FATAL: the ShareGPT source is not the one the workloads were built from" >&2
  echo "  expected $want" >&2
  echo "  got      $got" >&2
  exit 1
fi
echo "[restore] ShareGPT source matches"

mkdir -p "$OUT"
DURATION=$($PY -c "import json;print(json.load(open('$MANIFEST'))['duration_s'])")
SLO="$ROOT/exp/configs/v2/slo_base.json"

# One build emits both the bursty and the steady trace for a (rate, seed).
PAIRS="2:1 2:2 2:3 4:1 4:2 4:3 8:1 8:2 8:3 14:1 14:2 14:3 20:1 20:2 20:3"
fail=0
for pair in $PAIRS; do
  rate=${pair%%:*}; seed=${pair##*:}
  if [ -s "$OUT/bursty_r${rate}_s${seed}.pkl" ] && [ -s "$OUT/steady_r${rate}_s${seed}.pkl" ]; then
    echo "[restore] r${rate}_s${seed} already present"
    continue
  fi
  echo "[restore] building r${rate}_s${seed}"
  $PY "$SCRIPT_DIR/build_paired_workload.py" --rate "$rate" --duration "$DURATION" \
    --seed "$seed" --slo-base "$SLO" --outdir "$OUT" >/dev/null 2>&1 \
    || { echo "[restore] build failed for r${rate}_s${seed}" >&2; fail=1; }
done

echo
echo "[restore] verifying against the canonical digests"
$PY "$SCRIPT_DIR/verify_workloads.py" --workloads "$OUT" --manifest "$MANIFEST" \
  --out "$ROOT/exp/results/final-evaluation/WORKLOAD_VERIFICATION.json"
rc=$?
[ "$fail" = "0" ] || rc=1
exit $rc
