#!/bin/bash
# Rebuild the evaluation workloads and prove they are the ones already used.
#
# The .pkl traces are gitignored and did not survive the instance rebuild, but
# the paired_requests/phases JSON that describes each one is committed. The
# builder is deterministic in (rate, duration, seed, slo_base, sharegpt), so a
# rebuild must reproduce those JSON files byte for byte -- which is the check
# this script makes. A mismatch means the workload is not the one the earlier
# runs used, and it stops.
set -uo pipefail

WL=${1:?workload dir}
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
source "$SCRIPT_DIR/env.sh"
REF="$ROOT/exp/workloads/final-compare"
SLO_BASE="$ROOT/exp/configs/v2/slo_base.json"
DURATION=420
mkdir -p "$WL"

# (rate, seed) pairs the evaluation needs: calibration 20/{0,42}, Final C
# bursty {2,4,8,14,20}x{1,2,3} and steady {4,8,20}x{1,2,3}, prototype
# correction 20/3. Each build emits both the bursty and the steady trace.
PAIRS="20:0 20:42 2:1 2:2 2:3 4:1 4:2 4:3 8:1 8:2 8:3 14:1 14:2 14:3 20:1 20:2 20:3"

fail=0
for pair in $PAIRS; do
  rate=${pair%%:*}; seed=${pair##*:}; tag="r${rate}_s${seed}"
  if [ -s "$WL/bursty_${tag}.pkl" ] && [ -s "$WL/steady_${tag}.pkl" ]; then
    echo "[workload] $tag already built"
  else
    python3 "$SCRIPT_DIR/build_paired_workload.py" --rate "$rate" \
      --duration "$DURATION" --seed "$seed" --slo-base "$SLO_BASE" \
      --outdir "$WL" >/dev/null 2>&1 || { echo "[workload] build failed: $tag" >&2; fail=1; continue; }
  fi
  for kind in paired_requests phases; do
    ref="$REF/${kind}_${tag}.json"
    [ -f "$ref" ] || { echo "[workload] no committed reference for ${kind}_${tag}"; continue; }
    a=$(sha256sum "$ref" | cut -d' ' -f1)
    b=$(sha256sum "$WL/${kind}_${tag}.json" | cut -d' ' -f1)
    if [ "$a" != "$b" ]; then
      echo "[workload] MISMATCH ${kind}_${tag}: committed $a vs rebuilt $b" >&2
      fail=1
    fi
  done
done

python3 - "$WL" <<'PY'
import hashlib, json, sys
from pathlib import Path
wl = Path(sys.argv[1])
out = {}
for f in sorted(wl.glob("*")):
    h = hashlib.sha256()
    with f.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    out[f.name] = h.hexdigest()
(wl / "WORKLOAD_HASHES.json").write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
print(f"[workload] hashed {len(out)} files")
PY

if [ "$fail" != "0" ]; then
  echo "[workload] FAIL: rebuilt workloads do not match the committed provenance" >&2
  exit 1
fi
echo "[workload] every rebuilt workload matches its committed provenance"
