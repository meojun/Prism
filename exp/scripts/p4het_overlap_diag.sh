#!/bin/bash
# 8-run diagnostic: Prism with --overlap-migration OFF, everything else frozen.
#
# Treatment is exactly one flag. The control arms are the EXISTING validated
# 4-HET artifacts (Prototype and Prism overlap-ON); neither is re-run and
# neither is touched. Traces are the existing canonical files, reused byte for
# byte -- nothing is generated here.
#
# No aggregation push, no handoff, no git. Analysis is a separate step.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
OUT="$ROOT/exp/results/4het-overlap-diagnostic"
WL="$ROOT/exp/workloads/4het"
CFG="$ROOT/exp/configs/v4het/4model_2gpu.json"
SLO="$ROOT/exp/configs/v4het/slo_base.json"
CI="$ROOT/exp/configs/v4het/prefill_speed_4het_a100.json"
PY=/workspace/prism-exp/prism-venv/bin/python
TAU=0.00035
SHARED_EVAL="$ROOT/exp/results/final-evaluation"
PROGRESS="$OUT/PROGRESS.jsonl"
export PRISM_EVAL_DIR="$OUT"
mkdir -p "$OUT/raw"
log() { echo "[$(date -u +%FT%TZ)] [overlap-diag] $*" | tee -a "$OUT/pipeline.log"; }
stop_all() {
  bash "$SCRIPT_DIR/notify.sh" "odiag-stop-$(date +%s)" \
    "⛔ STOPPED | overlap-migration diagnostic | $1" || true
  log "STOPPED: $1"; exit 1
}

# steady r8 s1 runs first by contract: it is the validation gate.
CONDS="${P4HET_CONDS:-steady:8:1 steady:8:2 steady:10:1 steady:10:2 bursty:6:1 bursty:6:2 bursty:8:1 bursty:8:2}"

[ -f "$SHARED_EVAL/STOP" ] && stop_all "a STOP is in force: $(cat "$SHARED_EVAL/STOP")"
bash "$SCRIPT_DIR/restore_frozen_runtime.sh" --verify-only >>"$OUT/pipeline.log" 2>&1 \
  || stop_all "built runtime source does not reproduce the freeze"
log "runtime source verified byte-identical to freeze 6618671"

idx=0
for c in $CONDS; do
  IFS=: read -r kind rate seed <<< "$c"
  idx=$((idx + 1))
  trace="$WL/${kind}_r${rate}_s${seed}.pkl"
  [ -f "$trace" ] || stop_all "missing canonical trace $trace"

  # The trace must be the same bytes the ON/Prototype arms consumed.
  want=$($PY -c "
import json;m=json.load(open('$OUT/../4het-paired/WORKLOAD_MANIFEST.json'))
print(m['files']['${kind}_r${rate}_s${seed}.pkl']['sha256'])")
  got=$(sha256sum "$trace" | cut -d' ' -f1)
  [ "$want" = "$got" ] || stop_all "trace hash mismatch for ${kind}_r${rate}_s${seed}"

  d="$OUT/raw/prism-nooverlap/${kind}/rate_${rate}/seed_${seed}"
  log "[$idx/8] prism-nooverlap $kind r$rate s$seed (trace ${got:0:12} verified)"
  t0=$(date +%s)

  # Wait for the previous run's processes to actually be gone before clearing
  # shared memory. Guarding the clear with a bare pgrep skips it while the last
  # server is still winding down, and the next server then dies at startup on
  # the partial segment set (KeyError: '/ipc_*_root'). Observation + own-PID
  # wait only; nothing is signalled.
  for _ in $(seq 1 30); do
    pgrep -f "sglang.launch_multi_model_server" >/dev/null 2>&1 || break
    sleep 2
  done
  if ! pgrep -f "sglang.launch_multi_model_server" >/dev/null 2>&1; then
    rm -f /dev/shm/ipc_*_root /dev/shm/cuda.shm.* 2>/dev/null || true
  else
    log "WARNING: a server is still running at pre-run cleanup; shm not cleared"
  fi

  STAGE_HARD_LIMIT=2400 \
  bash "$SCRIPT_DIR/final_stage.sh" "$d" "p4het-prism-${kind}-r${rate}-s${seed}" \
    "$(echo "v4-paper-faithful-v6-nooverlap-${kind}-r${rate}-s${seed}" | tr '.' '_')" -- \
    env PRISM_ROOT=/workspace/prism-exp PRISM_REPO="$ROOT/prism-research" \
        PRISM_EXP="$ROOT/exp" PRISM_EVAL_DIR="$OUT" \
        CFG="$CFG" BENCHMARK_TIMEOUT=1500 KVPR_TAU="$TAU" \
        PREFILL_SPEED_FILE="$CI" SLO_BASE_FILE="$SLO" \
        bash "$SCRIPT_DIR/run_v4_case.sh" paper-faithful-v6-nooverlap "$kind" "$rate" "$seed" \
          "$trace" "$d" \
    >> "$OUT/sweep.log" 2>&1
  rc=$?
  t1=$(date +%s)

  $PY - "$PROGRESS" "$idx" "$kind" "$rate" "$seed" "$rc" "$((t1-t0))" "$d" <<'PY'
import json, sys, datetime
path, idx, kind, rate, seed, rc, secs, d = sys.argv[1:]
rec = {"idx": int(idx), "arm": "prism-nooverlap", "workload": kind,
       "rate": int(rate), "seed": int(seed), "ok": rc == "0",
       "seconds": int(secs), "run_dir": d,
       "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
try:
    rec["numbers"] = json.load(open(d + "/VERIFICATION.json"))["numbers"]
except Exception:
    rec["numbers"] = None
open(path, "a").write(json.dumps(rec) + "\n")
PY
  [ "$rc" = "0" ] || stop_all "run ${kind}_r${rate}_s${seed} failed (rc=$rc)"
  [ "$idx" = "1" ] && log "gate run PASSED -- continuing with the remaining 7"
done

n=$(ls -d "$OUT"/raw/prism-nooverlap/*/rate_*/seed_* 2>/dev/null | wc -l)
log "diagnostic complete: $n/8"
bash "$SCRIPT_DIR/notify.sh" "odiag-done-$(date +%s)" \
  "✅ SUCCESS | overlap-migration diagnostic complete | ${n}/8" || true
exit 0
