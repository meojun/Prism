#!/bin/bash
# The 4-model heterogeneous PAIRED evaluation: Released Prototype and Final
# Prism, on the same server, over byte-identical canonical traces.
#
# Ordering is paired per condition -- Prototype then Prism on the same trace,
# back to back -- so that anything that drifts on this machine over the hours
# drifts across both arms of a pair rather than across one whole arm. Each run
# is already a full server start and teardown, so pairing costs no stability.
#
# Frozen before any result is looked at: model set and revisions, c_i, tau,
# SLO, the 20 traces and their hashes, and the runtime. Nothing here reads a
# number and changes a parameter.
set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
OUT="$ROOT/exp/results/4het-paired"
WL="$ROOT/exp/workloads/4het"
CFG="$ROOT/exp/configs/v4het/4model_2gpu.json"
SLO="$ROOT/exp/configs/v4het/slo_base.json"
CI="$ROOT/exp/configs/v4het/prefill_speed_4het_a100.json"
PY=/workspace/prism-exp/prism-venv/bin/python
TAU=0.00035
RUNTIME_FREEZE=${PRISM_RUNTIME_FREEZE:-6618671}
# The STOP marker is machine-wide: final_stage.sh reads it from the original
# evaluation directory, and one stop namespace per machine is what we want.
SHARED_EVAL="$ROOT/exp/results/final-evaluation"
PROGRESS="$OUT/P4HET_PROGRESS.jsonl"
export PRISM_EVAL_DIR="$OUT"
mkdir -p "$OUT" "$OUT/raw"

log() { echo "[$(date -u +%FT%TZ)] [4het] $*" | tee -a "$OUT/pipeline.log"; }
stop_all() {
  bash "$SCRIPT_DIR/notify.sh" "p4het-stop-$(date +%s)" \
    "⛔ STOPPED | 4-HET Evaluation | $1" || true
  log "STOPPED: $1"
  exit 1
}

RATES="2 4 6 8 10"
SEEDS="1 2"
KINDS="bursty steady"

# ---------------------------------------------------------- preconditions
[ -f "$SHARED_EVAL/STOP" ] && stop_all "a STOP is in force: $(cat "$SHARED_EVAL/STOP")"

bash "$SCRIPT_DIR/restore_frozen_runtime.sh" --verify-only >>"$OUT/pipeline.log" 2>&1 \
  || stop_all "the built prism-research source does not reproduce freeze $RUNTIME_FREEZE"
log "runtime source verified byte-identical to freeze $RUNTIME_FREEZE"

for f in "$CFG" "$SLO" "$CI"; do
  [ -f "$f" ] || stop_all "missing frozen config $f"
done
$PY "$SCRIPT_DIR/p4het_verify_workloads.py" --workloads "$WL" \
    --manifest "$OUT/WORKLOAD_MANIFEST.json" >>"$OUT/pipeline.log" 2>&1 \
  || stop_all "the 20 canonical 4-model workloads do not verify"
log "20 canonical workloads verified against the frozen manifest"

# ------------------------------------------------------------- the sweep
idx=0
for kind in $KINDS; do
  for rate in $RATES; do
    for seed in $SEEDS; do
      trace="$WL/${kind}_r${rate}_s${seed}.pkl"
      [ -f "$trace" ] || stop_all "missing trace $trace"

      # Both arms of a pair consume the SAME file. No arm regenerates anything.
      for arm in prototype prism; do
        idx=$((idx + 1))
        if [ "$arm" = prototype ]; then
          sys=released-prototype; label="p4het-proto-${kind}-r${rate}-s${seed}"
          extra=""
        else
          sys=paper-faithful-v6; label="p4het-prism-${kind}-r${rate}-s${seed}"
          extra="KVPR_TAU=$TAU PREFILL_SPEED_FILE=$CI SLO_BASE_FILE=$SLO"
        fi
        # The inner session name is NOT ours to choose: run_v4_case.sh derives
        # it as v4-<system>-<workload>-r<rate>-s<seed> and creates the tmux
        # session under that name. The monitor is told which session to watch,
        # so passing our own label here makes it watch a session that never
        # exists -- it then reports "inner server session exited without
        # result" and the harness tears down a healthy server mid-benchmark.
        inner=$(echo "v4-${sys}-${kind}-r${rate}-s${seed}" | tr '.' '_')
        d="$OUT/raw/${arm}/${kind}/rate_${rate}/seed_${seed}"
        log "[$idx/40] $arm $kind r$rate s$seed"
        t0=$(date +%s)

        # Same pre-run cleanup before every arm, so neither inherits the
        # other's leftovers. Only segments no live server owns are removed.
        if ! pgrep -f "sglang.launch_multi_model_server" >/dev/null 2>&1; then
          rm -f /dev/shm/ipc_*_root 2>/dev/null || true
        fi

        STAGE_HARD_LIMIT=2400 \
        bash "$SCRIPT_DIR/final_stage.sh" "$d" "$label" "$inner" -- \
          env PRISM_ROOT=/workspace/prism-exp PRISM_REPO="$ROOT/prism-research" \
              PRISM_EXP="$ROOT/exp" PRISM_EVAL_DIR="$OUT" \
              CFG="$CFG" BENCHMARK_TIMEOUT=1500 $extra \
              bash "$SCRIPT_DIR/run_v4_case.sh" "$sys" "$kind" "$rate" "$seed" \
                "$trace" "$d" \
          >> "$OUT/sweep.log" 2>&1
        rc=$?
        t1=$(date +%s)

        $PY - "$PROGRESS" "$idx" "$arm" "$kind" "$rate" "$seed" "$rc" \
             "$((t1-t0))" "$d" <<'PY'
import json, sys, datetime
path, idx, arm, kind, rate, seed, rc, secs, d = sys.argv[1:]
rec = {"idx": int(idx), "arm": arm, "workload": kind, "rate": int(rate),
       "seed": int(seed), "ok": rc == "0", "seconds": int(secs), "run_dir": d,
       "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
try:
    rec["numbers"] = json.load(open(d + "/VERIFICATION.json"))["numbers"]
except Exception:
    rec["numbers"] = None
with open(path, "a") as f:
    f.write(json.dumps(rec) + "\n")
PY
        if [ "$rc" != "0" ]; then
          stop_all "stage=4het arm=$arm run=${kind}_r${rate}_s${seed} -- see $SHARED_EVAL/STOP"
        fi
      done
    done
  done
done

n=$(ls -d "$OUT"/raw/*/*/rate_*/seed_* 2>/dev/null | wc -l)
log "sweep complete: $n runs"
bash "$SCRIPT_DIR/notify.sh" "p4het-sweep-complete-$(date +%s)" \
  "✅ SUCCESS | 4-HET Paired Evaluation Complete | ${n}/40" || true

# ------------------------------------------------------------ aggregation
$PY "$SCRIPT_DIR/p4het_aggregate.py" --out-dir "$OUT" >> "$OUT/pipeline.log" 2>&1 \
  || stop_all "aggregation failed"
log "aggregation complete"

# Report, handoff and push run here, in this session, after the last benchmark
# -- never beside one.
bash "$SCRIPT_DIR/p4het_finalize.sh" || exit 1
exit 0
