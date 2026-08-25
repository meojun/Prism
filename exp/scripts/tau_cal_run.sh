#!/bin/bash
# Tau calibration Stage B: 5 new candidate arms x 8 conditions = 40 runs.
# T1 (0.00035) is reused from the 60 s window calibration -- verified identical
# source hash, window, cooldown, trace hash and validity -- so it is not rerun.
#
# The ONLY intended difference between arms is KVPR_TAU. Candidates were frozen
# in exp/analysis/tau_calibration/TAU_CANDIDATES_FROZEN.json before this script
# ever ran and must not be changed now.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
OUT="${PRISM_OUT_DIR:-$ROOT/exp/results/4het-tau-calibration}"
WL="$ROOT/exp/workloads/4het-cal"
MAN="$ROOT/exp/results/4het-window-calibration/CALIBRATION_TRACE_MANIFEST.json"
FROZEN="$ROOT/exp/analysis/tau_calibration/TAU_CANDIDATES_FROZEN.json"
CFG="$ROOT/exp/configs/v4het/4model_2gpu.json"
SLO="$ROOT/exp/configs/v4het/slo_base.json"
CI="$ROOT/exp/configs/v4het/prefill_speed_4het_a100.json"
PY=/workspace/prism-exp/prism-venv/bin/python
WINDOW=60
COOLDOWN=30
SHARED_EVAL="$ROOT/exp/results/final-evaluation"
export PRISM_EVAL_DIR="$OUT"
mkdir -p "$OUT/raw"
log() { echo "[$(date -u +%FT%TZ)] [tau-cal] $*" | tee -a "$OUT/pipeline.log"; }
stop_all() { bash "$SCRIPT_DIR/notify.sh" "tc-stop-$(date +%s)" \
  "⛔ STOPPED | tau calibration | $1" || true; log "STOPPED: $1"; exit 1; }

CONDS="${TAU_CAL_CONDS:-steady:8:3 steady:8:4 steady:10:3 steady:10:4 bursty:8:3 bursty:8:4 bursty:10:3 bursty:10:4}"
# T1 is reused, not rerun
ARMS="${TAU_CAL_ARMS:-T0 T2 T3 T4 T5}"

[ -f "$SHARED_EVAL/STOP" ] && stop_all "a STOP is in force: $(cat "$SHARED_EVAL/STOP")"

WANT_SRC=$(cat "${PRISM_EXPECTED_SRC_FILE:-$ROOT/exp/analysis/estimator_correction/ESTIMATOR_RUNTIME.sha256}")
SNAP=$(mktemp -d); trap 'rm -rf "$SNAP"' EXIT
PRISM_REPO="$ROOT/prism-research" bash "$SCRIPT_DIR/snapshot_source_patch.sh" "$SNAP" verify \
  >>"$OUT/pipeline.log" 2>&1 || stop_all "could not snapshot the built runtime source"
GOT_SRC=$(sha256sum "$SNAP/prism_research_worktree.patch" | cut -d\  -f1)
[ "$WANT_SRC" = "$GOT_SRC" ] || stop_all "built runtime is $GOT_SRC, expected $WANT_SRC"
log "runtime source verified (${GOT_SRC:0:12}), window=${WINDOW}s cooldown=${COOLDOWN}s"

total=$(( $(echo $ARMS | wc -w) * $(echo $CONDS | wc -w) ))
idx=0
for arm in $ARMS; do
  TAU=$($PY -c "
import json;d=json.load(open('$FROZEN'))
print(next(c['tau'] for c in d['candidates'] if c['id']=='$arm'))")
  [ -n "$TAU" ] || stop_all "candidate $arm not found in the frozen manifest"
  log "=== arm $arm : tau=$TAU ==="
  for c in $CONDS; do
    IFS=: read -r kind rate seed <<< "$c"
    idx=$((idx + 1))
    trace="$WL/${kind}_r${rate}_s${seed}.pkl"
    want=$($PY -c "
import json;m=json.load(open('$MAN'));print(m['files']['${kind}_r${rate}_s${seed}.pkl']['sha256'])")
    got=$(sha256sum "$trace" | cut -d' ' -f1)
    [ "$want" = "$got" ] || stop_all "trace hash mismatch for ${kind}_r${rate}_s${seed}"

    d="$OUT/raw/prism-${arm}/${kind}/rate_${rate}/seed_${seed}"
    log "[$idx/$total] $arm tau=$TAU $kind r$rate s$seed (trace ${got:0:12} verified)"

    for _ in $(seq 1 30); do
      pgrep -f "sglang.launch_multi_model_server" >/dev/null 2>&1 || break
      sleep 2
    done
    pgrep -f "sglang.launch_multi_model_server" >/dev/null 2>&1 \
      || rm -f /dev/shm/ipc_*_root /dev/shm/cuda.shm.* 2>/dev/null || true

    t0=$(date +%s)
    STAGE_HARD_LIMIT=2400 \
    bash "$SCRIPT_DIR/final_stage.sh" "$d" "p4het-prism-${arm}-${kind}-r${rate}-s${seed}" \
      "$(echo "v4-paper-faithful-v6-${kind}-r${rate}-s${seed}" | tr '.' '_')" -- \
      env PRISM_ROOT=/workspace/prism-exp PRISM_REPO="$ROOT/prism-research" \
          PRISM_EXP="$ROOT/exp" PRISM_EVAL_DIR="$OUT" \
          CFG="$CFG" BENCHMARK_TIMEOUT=1500 KVPR_TAU="$TAU" \
          KVPR_WINDOW="$WINDOW" KVPR_COOLDOWN="$COOLDOWN" \
          PREFILL_SPEED_FILE="$CI" SLO_BASE_FILE="$SLO" \
          bash "$SCRIPT_DIR/run_v4_case.sh" paper-faithful-v6 "$kind" "$rate" "$seed" \
            "$trace" "$d" \
      >> "$OUT/sweep.log" 2>&1
    rc=$?; t1=$(date +%s)

    $PY - "$OUT/PROGRESS.jsonl" "$idx" "$arm" "$TAU" "$kind" "$rate" "$seed" "$rc" "$((t1-t0))" "$d" <<'PYEOF'
import json, sys, datetime
path, idx, arm, tau, kind, rate, seed, rc, secs, d = sys.argv[1:]
rec = {"idx": int(idx), "arm": arm, "tau": float(tau), "workload": kind,
       "rate": int(rate), "seed": int(seed), "ok": rc == "0", "seconds": int(secs),
       "run_dir": d, "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
try:
    rec["numbers"] = json.load(open(d + "/VERIFICATION.json"))["numbers"]
except Exception:
    rec["numbers"] = None
open(path, "a").write(json.dumps(rec) + "\n")
PYEOF
    [ "$rc" = "0" ] || stop_all "run $arm ${kind}_r${rate}_s${seed} failed (rc=$rc)"
    [ "$idx" = "1" ] && log "gate run PASSED -- continuing"
  done
done
log "tau calibration Stage B complete"
bash "$SCRIPT_DIR/notify.sh" "tc-done-$(date +%s)" \
  "✅ SUCCESS | tau calibration complete | 40/40 new runs" || true
exit 0
