#!/bin/bash
# Retry the one calibration point that failed, then hand the chain back its own
# driver. Nothing that already succeeded is repeated.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
EVAL="$ROOT/exp/results/final-evaluation"
WL="$ROOT/exp/workloads/final-evaluation"
CI="$EVAL/01-ci-profile/prefill_speed_final_a100.json"
PY=/workspace/prism-exp/prism-venv/bin/python
cd "$ROOT"
log() { echo "[$(date -u +%FT%TZ)] [resume] $*" | tee -a "$EVAL/pipeline.log"; }

# Every failed attempt is evidence; keep them all and retry beside them.
FAILED="$EVAL/02-tau-calibration/raw/tau_0p00035/seed_0"
if [ -d "$FAILED" ]; then
  n=1
  while [ -d "$FAILED.attempt$n" ]; do n=$((n+1)); done
  mv "$FAILED" "$FAILED.attempt$n"
  log "preserved the failed attempt as $(basename "$FAILED").attempt$n"
fi

rm -f "$EVAL/STOP"
log "retrying cal-0p00035-s0 on the patched runtime"
STAGE_HARD_LIMIT=2400 \
bash "$SCRIPT_DIR/final_stage.sh" "$FAILED" "cal-0p00035-s0" \
  v4-paper-faithful-v6-bursty-r20-s0 -- \
  env PRISM_ROOT=/workspace/prism-exp PRISM_REPO="$ROOT/prism-research" \
      PRISM_EXP="$ROOT/exp" KVPR_TAU=0.00035 \
      PREFILL_SPEED_FILE="$CI" BENCHMARK_TIMEOUT=1500 \
      bash "$SCRIPT_DIR/run_v4_case.sh" paper-faithful-v6 bursty 20 0 \
        "$WL/bursty_r20_s0.pkl" "$FAILED" \
  >> "$EVAL/02-tau-calibration/calibration.log" 2>&1
rc=$?

if [ "$rc" != "0" ]; then
  log "the retry did not pass; the chain stays stopped (see FAILURE_AUTOPSY.json)"
  exit 1
fi

# The retry has to be clean on the things that stopped it, not merely finish.
$PY "$SCRIPT_DIR/check_alg2_interaction.py" --run "$FAILED" \
  --out "$EVAL/02-tau-calibration/cal_0p00035_s0_interaction.json" \
  >> "$EVAL/02-tau-calibration/calibration.log" 2>&1
verdict=$($PY -c "import json;print(json.load(open('$EVAL/02-tau-calibration/cal_0p00035_s0_interaction.json'))['verdict'])" 2>/dev/null || echo FAIL)
log "retry interaction gate: $verdict"
if [ "$verdict" != "PASS" ]; then
  echo "cal-0p00035-s0 retry did not pass the interaction gate" > "$EVAL/STOP"
  $PY "$SCRIPT_DIR/final_failure_autopsy.py" --run "$FAILED" --label cal-0p00035-s0 \
    --out "$FAILED/FAILURE_AUTOPSY.json" >> "$EVAL/autopsy.log" 2>&1 || true
  log "STOP: the retry finished but the gate did not pass"
  exit 1
fi

# Stage 02 must run again from its own driver so the remaining 11 points and
# the selection happen exactly as the chain defines them. The retried point is
# COMPLETE, so final_stage.sh will skip it.
rm -f "$EVAL/02-tau-calibration/STATUS.json"
log "retry clean; handing back to the pipeline"
exec bash "$SCRIPT_DIR/final_pipeline.sh"
