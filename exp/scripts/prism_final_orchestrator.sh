#!/bin/bash
# Prism final pipeline orchestrator (sections 8 / 67).
#
# Chains: wait for tau-48 -> select FINAL_TAU -> freeze baseline+protocols ->
# generate many-model traces -> pilot(r16,seed9) -> gate -> many-model 20 ->
# generate 4-HET traces -> final 4-HET 40 -> stop for reports.
#
# Idempotent: every stage checks for its own completed output first. Nothing is
# ever overwritten and no protocol is derived from a result.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
PY=/workspace/prism-exp/prism-venv/bin/python
STATE="$ROOT/exp/state/PRISM_FINAL_PIPELINE_STATE.json"
LOG="$ROOT/exp/state/orchestrator.log"
MAN="$ROOT/exp/manifests/prism_final"
SG=${SHAREGPT_JSON:-/workspace/datasets/sharegpt/ShareGPT_V3_unfiltered_cleaned_split.json}
log(){ echo "[$(date -u +%FT%TZ)] [orch] $*" | tee -a "$LOG"; }
halt(){ log "HALT: $1"; $PY - "$STATE" "$1" <<'P'
import json,sys,os,tempfile,datetime
from pathlib import Path
p,reason=Path(sys.argv[1]),sys.argv[2]
s=json.loads(p.read_text()); s["halted"]={"reason":reason,
  "utc":datetime.datetime.now(datetime.timezone.utc).isoformat()}
fd,t=tempfile.mkstemp(dir=str(p.parent)); os.close(fd)
Path(t).write_text(json.dumps(s,indent=1)); os.replace(t,p)
P
bash "$SCRIPT_DIR/notify.sh" "orch-halt-$(date +%s)" "⛔ PIPELINE HALTED | $1" || true; exit 1; }
setstage(){ $PY - "$STATE" "$1" <<'P'
import json,sys,os,tempfile,datetime
from pathlib import Path
p,stage=Path(sys.argv[1]),sys.argv[2]
s=json.loads(p.read_text()); s["current_stage"]=stage
s.setdefault("stages_completed",[]); s["timestamp"]=datetime.datetime.now(datetime.timezone.utc).isoformat()
fd,t=tempfile.mkstemp(dir=str(p.parent)); os.close(fd)
Path(t).write_text(json.dumps(s,indent=1)); os.replace(t,p)
P
}

# ---------------------------------------------------------------- stage: tau
setstage 14_tau_calibration
TAUOUT="$ROOT/exp/results/4het-tau-final"
# Only lines written AFTER this orchestrator starts may halt it. A STOPPED line
# from an earlier, already-diagnosed attempt must not re-trigger a halt -- that
# would make every restart fail immediately on stale history.
TAU_LOG_BASE=$(wc -l < "$TAUOUT/pipeline.log" 2>/dev/null || echo 0)
log "waiting for the 48-run tau matrix (ignoring the first $TAU_LOG_BASE existing log lines)"
while :; do
  n=$(wc -l < "$TAUOUT/PROGRESS.jsonl" 2>/dev/null || echo 0)
  newstop=$(tail -n +$((TAU_LOG_BASE+1)) "$TAUOUT/pipeline.log" 2>/dev/null | grep -m1 "STOPPED" || true)
  [ -n "$newstop" ] && halt "tau calibration stopped: $newstop"
  [ "$n" -ge 48 ] && break
  tmux has-session -t prism_final_auto 2>/dev/null || { [ "$n" -ge 48 ] || halt "tau session gone at $n/48"; }
  sleep 60
done
log "tau matrix complete: 48/48"

# --------------------------------------------------------- stage: tau select
setstage 16_tau_selection
$PY "$ROOT/exp/analysis/tau_calibration/select_tau.py" 2>&1 | tee -a "$LOG" \
  || halt "tau selection failed or inconclusive"
FINAL_TAU=$($PY -c "import json;print(json.load(open('$ROOT/exp/analysis/tau_calibration/FINAL_TAU.json'))['FINAL_TAU'])")
[ -n "$FINAL_TAU" ] || halt "FINAL_TAU empty"
log "FINAL_TAU = $FINAL_TAU"

# ------------------------------------------------------- stage: freeze stuff
setstage 18_freeze_baseline
$PY "$SCRIPT_DIR/prism_final_freeze.py" "$FINAL_TAU" 2>&1 | tee -a "$LOG" \
  || halt "baseline/protocol freeze failed"

# ------------------------------------------- stage: many-model trace  (pilot)
setstage 29_pilot_traces
MMWL="$ROOT/exp/workloads/many_model_pf"
mkdir -p "$MMWL"
HOTS="model_5,model_1|model_6,model_2|model_3,model_4"
gen_mm(){ # rate seed
  [ -s "$MMWL/bursty_r$1_s$2.pkl" ] && return 0
  $PY "$SCRIPT_DIR/build_paired_workload.py" --rate "$1" --duration 540 --seed "$2" \
    --models model_1,model_2,model_3,model_4,model_5,model_6 \
    --revisions "$ROOT/exp/configs/v2/model_revisions.json" \
    --slo-base "$ROOT/exp/configs/v2/slo_base.json" --sharegpt "$SG" \
    --hot-sets "$HOTS" --phase-duration 180 --hot-share 0.9 --outdir "$MMWL" \
    >> "$MMWL/build.log" 2>&1
}
gen_mm 16 9 || halt "pilot trace generation failed"
FREEZE_RATES=16 FREEZE_SEEDS=9 $PY "$SCRIPT_DIR/p4het_freeze_workloads.py" \
  --workloads "$MMWL" --out "$MAN/MANY_MODEL_PILOT_TRACE_MANIFEST.json" >>"$LOG" 2>&1 \
  || halt "pilot trace manifest failed"

# ------------------------------------------------------------ stage: pilot
setstage 29_pilot_runs
STAGE=mm-pilot OUT="$ROOT/exp/results/many-model-pilot" WL="$MMWL" \
  MANIFEST="$MAN/MANY_MODEL_PILOT_TRACE_MANIFEST.json" \
  CFG="$ROOT/exp/configs/v2/6model_2gpu.json" SLO="$ROOT/exp/configs/v2/slo_base.json" \
  CI="$ROOT/exp/configs/v2/prefill_speed.json" \
  CONDS="bursty:16:9" ARMS="prototype prism" KVPR_TAU_VAL="$FINAL_TAU" \
  BENCH_TIMEOUT=2000 STAGE_HARD_LIMIT=3000 \
  bash "$SCRIPT_DIR/prism_final_stage_runner.sh" || halt "pilot run failure"

# ------------------------------------------------------------- stage: gate
setstage 33_pilot_gate
$PY "$SCRIPT_DIR/prism_final_pilot_gate.py" 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
case "$rc" in
  0) log "PILOT_GATE = PASS (R >= 0.70) -- proceeding to the full 20-run matrix" ;;
  2) halt "PILOT_GATE = HALT_FOR_TELEMETRY_REVIEW (CASE B, 0.50 <= R < 0.70): review the resource-management telemetry before proceeding -- see reports/prism/07_many_model_prism_favorable/MANY_MODEL_PILOT_STOP_ANALYSIS.md" ;;
  *) halt "PILOT_GATE = STOP (CASE A, R < 0.50): see reports/prism/07_many_model_prism_favorable/MANY_MODEL_PILOT_STOP_ANALYSIS.md" ;;
esac

# -------------------------------------------------- stage: many-model final
setstage 34_many_model_traces
# Final many-model rate grid amended [4,8,12,16,20] -> [2,4,6,8,10] before any
# final Prism many-model result existed. See
# exp/manifests/prism_final/MANY_MODEL_PROTOCOL_FROZEN.md, "Protocol amendment".
for r in 2 4 6 8 10; do for s in 7 8; do gen_mm "$r" "$s" || halt "mm trace r$r s$s failed"; done; done
FREEZE_RATES=2,4,6,8,10 FREEZE_SEEDS=7,8 $PY "$SCRIPT_DIR/p4het_freeze_workloads.py" \
  --workloads "$MMWL" --out "$MAN/MANY_MODEL_TRACE_MANIFEST.json" >>"$LOG" 2>&1 \
  || halt "many-model trace manifest failed"
setstage 34_many_model_runs
MMC=""; for r in 2 4 6 8 10; do for s in 7 8; do MMC="$MMC bursty:$r:$s"; done; done
STAGE=mm-final OUT="$ROOT/exp/results/many-model-final" WL="$MMWL" \
  MANIFEST="$MAN/MANY_MODEL_TRACE_MANIFEST.json" \
  CFG="$ROOT/exp/configs/v2/6model_2gpu.json" SLO="$ROOT/exp/configs/v2/slo_base.json" \
  CI="$ROOT/exp/configs/v2/prefill_speed.json" \
  CONDS="$MMC" ARMS="prototype prism" KVPR_TAU_VAL="$FINAL_TAU" \
  BENCH_TIMEOUT=2000 STAGE_HARD_LIMIT=3000 \
  bash "$SCRIPT_DIR/prism_final_stage_runner.sh" || halt "many-model final run failure"

# ------------------------------------------------------ stage: final 4-HET
setstage 41_final_4het_traces
HWL="$ROOT/exp/workloads/4het-final"; mkdir -p "$HWL"
for r in 2 4 6 8 10; do for s in 5 6; do
  [ -s "$HWL/steady_r${r}_s${s}.pkl" ] && continue
  $PY "$SCRIPT_DIR/build_paired_workload.py" --rate "$r" --duration 420 --seed "$s" \
    --models model_3,model_4,model_5,model_6 \
    --revisions "$ROOT/exp/configs/v4het/model_revisions.json" \
    --slo-base "$ROOT/exp/configs/v4het/slo_base.json" --sharegpt "$SG" \
    --outdir "$HWL" >> "$HWL/build.log" 2>&1 || halt "4het trace r$r s$s failed"
done; done
FREEZE_RATES=2,4,6,8,10 FREEZE_SEEDS=5,6 $PY "$SCRIPT_DIR/p4het_freeze_workloads.py" \
  --workloads "$HWL" --out "$MAN/FINAL_4HET_TRACE_MANIFEST.json" >>"$LOG" 2>&1 \
  || halt "4het trace manifest failed"
setstage 41_final_4het_runs
HC=""; for k in steady bursty; do for r in 2 4 6 8 10; do for s in 5 6; do HC="$HC $k:$r:$s"; done; done; done
STAGE=final4het OUT="$ROOT/exp/results/4het-final" WL="$HWL" \
  MANIFEST="$MAN/FINAL_4HET_TRACE_MANIFEST.json" \
  CFG="$ROOT/exp/configs/v4het/4model_2gpu.json" SLO="$ROOT/exp/configs/v4het/slo_base.json" \
  CI="$ROOT/exp/configs/v4het/prefill_speed_4het_a100.json" \
  CONDS="$HC" ARMS="prototype prism" KVPR_TAU_VAL="$FINAL_TAU" \
  bash "$SCRIPT_DIR/prism_final_stage_runner.sh" || halt "final 4-HET run failure"

setstage 46_awaiting_reports
log "ALL EXPERIMENT STAGES COMPLETE -- reports pending"
bash "$SCRIPT_DIR/notify.sh" "orch-done-$(date +%s)" \
  "✅ ALL PRISM EXPERIMENTS COMPLETE | tau=$FINAL_TAU | reports pending" || true
