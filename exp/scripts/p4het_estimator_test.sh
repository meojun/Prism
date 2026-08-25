#!/bin/bash
# Stage B: 4 Prism runs with kvpr-migration-cooldown = 0. One variable.
#
# The arm is the UNCHANGED `paper-faithful-v6`; the cooldown is already an
# environment variable in run_v4_case.sh (KVPR_COOLDOWN, default 30), so no
# harness edit is needed and no other flag can drift. Controls are the existing
# validated cooldown=30 Prism runs and the Prototype runs; neither is re-run.
# Traces are the existing canonical files, hash-checked before each run.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
OUT="$ROOT/exp/results/4het-estimator-correction"
WL="$ROOT/exp/workloads/4het"
CFG="$ROOT/exp/configs/v4het/4model_2gpu.json"
SLO="$ROOT/exp/configs/v4het/slo_base.json"
CI="$ROOT/exp/configs/v4het/prefill_speed_4het_a100.json"
PY=/workspace/prism-exp/prism-venv/bin/python
TAU=0.00035
COOLDOWN=30
SHARED_EVAL="$ROOT/exp/results/final-evaluation"
export PRISM_EVAL_DIR="$OUT"
mkdir -p "$OUT/raw"
log() { echo "[$(date -u +%FT%TZ)] [estimator-corr] $*" | tee -a "$OUT/pipeline.log"; }
stop_all() { bash "$SCRIPT_DIR/notify.sh" "ec-stop-$(date +%s)" \
  "⛔ STOPPED | estimator correction test | $1" || true; log "STOPPED: $1"; exit 1; }

CONDS="${P4HET_CONDS:-steady:8:1 steady:8:2 steady:10:1 steady:10:2}"

[ -f "$SHARED_EVAL/STOP" ] && stop_all "a STOP is in force: $(cat "$SHARED_EVAL/STOP")"
# The runtime is deliberately NOT the 6618671 freeze: it carries the estimator
# correction (exp/analysis/estimator_correction/estimator.patch, 4 hunks). Gate
# on the hash of THAT source so no other drift can slip in either.
WANT_SRC=$(cat "$ROOT/exp/analysis/estimator_correction/ESTIMATOR_RUNTIME.sha256")
SNAP=$(mktemp -d); trap 'rm -rf "$SNAP"' EXIT
PRISM_REPO="$ROOT/prism-research" bash "$SCRIPT_DIR/snapshot_source_patch.sh" "$SNAP" verify \
  >>"$OUT/pipeline.log" 2>&1 || stop_all "could not snapshot the built runtime source"
GOT_SRC=$(sha256sum "$SNAP/prism_research_worktree.patch" | cut -d\  -f1)
[ "$WANT_SRC" = "$GOT_SRC" ] \
  || stop_all "built runtime is $GOT_SRC, expected estimator-corrected $WANT_SRC"
log "runtime source verified as freeze 6618671 + estimator correction (${GOT_SRC:0:12})"

idx=0
for c in $CONDS; do
  IFS=: read -r kind rate seed <<< "$c"
  idx=$((idx + 1))
  trace="$WL/${kind}_r${rate}_s${seed}.pkl"
  want=$($PY -c "
import json;m=json.load(open('$ROOT/exp/results/4het-paired/WORKLOAD_MANIFEST.json'))
print(m['files']['${kind}_r${rate}_s${seed}.pkl']['sha256'])")
  got=$(sha256sum "$trace" | cut -d' ' -f1)
  [ "$want" = "$got" ] || stop_all "trace hash mismatch for ${kind}_r${rate}_s${seed}"

  d="$OUT/raw/prism-estimator/${kind}/rate_${rate}/seed_${seed}"
  log "[$idx/4] cooldown=$COOLDOWN $kind r$rate s$seed (trace ${got:0:12} verified)"

  # Wait for the previous run's processes to be gone before clearing shm.
  for _ in $(seq 1 30); do
    pgrep -f "sglang.launch_multi_model_server" >/dev/null 2>&1 || break
    sleep 2
  done
  pgrep -f "sglang.launch_multi_model_server" >/dev/null 2>&1 \
    || rm -f /dev/shm/ipc_*_root /dev/shm/cuda.shm.* 2>/dev/null || true

  t0=$(date +%s)
  STAGE_HARD_LIMIT=2400 \
  bash "$SCRIPT_DIR/final_stage.sh" "$d" "p4het-prism-${kind}-r${rate}-s${seed}" \
    "$(echo "v4-paper-faithful-v6-${kind}-r${rate}-s${seed}" | tr '.' '_')" -- \
    env PRISM_ROOT=/workspace/prism-exp PRISM_REPO="$ROOT/prism-research" \
        PRISM_EXP="$ROOT/exp" PRISM_EVAL_DIR="$OUT" \
        CFG="$CFG" BENCHMARK_TIMEOUT=1500 KVPR_TAU="$TAU" \
        KVPR_COOLDOWN="$COOLDOWN" \
        PREFILL_SPEED_FILE="$CI" SLO_BASE_FILE="$SLO" \
        bash "$SCRIPT_DIR/run_v4_case.sh" paper-faithful-v6 "$kind" "$rate" "$seed" \
          "$trace" "$d" \
    >> "$OUT/sweep.log" 2>&1
  rc=$?; t1=$(date +%s)

  $PY - "$OUT/PROGRESS.jsonl" "$idx" "$kind" "$rate" "$seed" "$rc" "$((t1-t0))" "$d" <<'PY'
import json, sys, datetime
path, idx, kind, rate, seed, rc, secs, d = sys.argv[1:]
rec = {"idx": int(idx), "arm": "prism-estimator", "workload": kind,
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
  [ "$idx" = "1" ] && log "gate run PASSED -- continuing"
done
log "estimator correction test complete"
bash "$SCRIPT_DIR/notify.sh" "ec-done-$(date +%s)" \
  "✅ SUCCESS | estimator correction test complete | 4/4 Prism runs" || true
exit 0
