#!/bin/bash
# The final calibration and evaluation chain, as one resumable sequence.
#
#   c_i -> tau calibration -> freeze -> readiness -> prototype correction
#       -> Final C 24 runs -> aggregation
#
# Every stage writes STATUS.json. A stage that has already PASSed is skipped and
# never rerun; a stage that FAILs stops the chain, and nothing downstream runs.
# No benchmark result feeds back into runtime or policy: c_i and tau are frozen
# before the evaluation starts and are not touched afterwards.
set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
OUT="$ROOT/exp/results/final-evaluation"
WL="$ROOT/exp/workloads/final-evaluation"
PY=/workspace/prism-exp/prism-venv/bin/python
RUNTIME_FREEZE=${PRISM_RUNTIME_FREEZE:-d849065}
mkdir -p "$OUT"

log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$OUT/pipeline.log"; }

status_path() { echo "$OUT/$1/STATUS.json"; }

stage_done() {
  local s=$1 f; f=$(status_path "$s")
  [ -f "$f" ] && grep -q '"result": "PASS"' "$f"
}

stage_start() {
  local s=$1; mkdir -p "$OUT/$s"
  $PY - "$(status_path "$s")" "$s" "$RUNTIME_FREEZE" <<'PY'
import json, sys, datetime
path, stage, sha = sys.argv[1:]
json.dump({"stage": stage, "result": "RUNNING", "git_sha": sha,
           "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "finished_at": None, "reason": None, "artifacts": []},
          open(path, "w"), indent=2)
PY
}

stage_end() {
  local s=$1 result=$2 reason=${3:-}; shift 3 2>/dev/null || shift 2
  $PY - "$(status_path "$s")" "$result" "$reason" "$@" <<'PY'
import json, sys, datetime
path, result, reason, *artifacts = sys.argv[1:]
try:
    rec = json.load(open(path))
except Exception:
    rec = {}
rec.update(result=result, reason=reason or None, artifacts=artifacts,
           finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
json.dump(rec, open(path, "w"), indent=2)
PY
  log "stage $s -> $result ${reason:+($reason)}"
  # Notification only: it cannot change the outcome or the exit path below.
  if [ "$result" = "PASS" ]; then
    case "$s" in
      02-tau-calibration)
        valid=$(ls -d "$OUT"/02-tau-calibration/raw/*/seed_[0-9]* 2>/dev/null \
                | grep -vE 'invalid|attempt' | wc -l)
        tau=$($PY -c "import json;print(json.load(open('$OUT/02-tau-calibration/FROZEN_TAU.json'))['tau'])" 2>/dev/null || echo "?")
        bash "$SCRIPT_DIR/notify.sh" "calibration-complete" \
          "🟢 Prism calibration COMPLETE | ${valid}/12 valid | selected τ=${tau}" || true
        bash "$SCRIPT_DIR/notify.sh" "tau-frozen" \
          "🔵 Prism τ FROZEN | τ=${tau} | calibration input locked" || true ;;
      04-prototype-correction)
        bash "$SCRIPT_DIR/notify.sh" "stage04-complete" \
          "🟢 Prism Prototype correction COMPLETE" || true ;;
      05-final-c)
        n=$(ls -d "$OUT"/05-final-c/raw/*/rate_*/seed_* 2>/dev/null | wc -l)
        bash "$SCRIPT_DIR/notify.sh" "finalc-complete" \
          "🟢 Prism Final evaluation COMPLETE | ${n}/24 runs" || true ;;
    esac
  else
    bash "$SCRIPT_DIR/notify_stop.sh" "$s" "-" "${reason:-stage failed}" || true
  fi
  [ "$result" = "PASS" ] || { log "CHAIN STOPPED at $s"; exit 1; }
}

# ---------------------------------------------------------------- stage 0
if stage_done 00-preflight; then log "skip 00-preflight"; else
  stage_start 00-preflight
  if $PY "$SCRIPT_DIR/final_preflight.py" "$OUT/FINAL_ENVIRONMENT.json" \
        > "$OUT/00-preflight/preflight.log" 2>&1 \
     && bash "$SCRIPT_DIR/final_build_workloads.sh" "$WL" \
        > "$OUT/00-preflight/workloads.log" 2>&1; then
    stage_end 00-preflight PASS "" "$OUT/FINAL_ENVIRONMENT.json" "$WL/WORKLOAD_HASHES.json"
  else
    stage_end 00-preflight FAIL "preflight or workload provenance failed"
  fi
fi

# ---------------------------------------------------------------- stage 1
CI_FILE="$OUT/01-ci-profile/prefill_speed_final_a100.json"
if stage_done 01-ci-profile; then log "skip 01-ci-profile"; else
  stage_start 01-ci-profile
  if bash "$SCRIPT_DIR/final_profile_ci.sh" "$OUT/01-ci-profile" \
        > "$OUT/01-ci-profile/profile.log" 2>&1 && [ -s "$CI_FILE" ]; then
    stage_end 01-ci-profile PASS "" "$CI_FILE"
  else
    stage_end 01-ci-profile FAIL "c_i profiling did not produce all six models"
  fi
fi
[ -s "$CI_FILE" ] || { log "FATAL: c_i file missing"; exit 1; }

# ---------------------------------------------------------------- stage 2
TAUS="0.00035 0.07 0.10 0.13 0.171086 1000000000"
CAL_SEEDS="0 42"
if stage_done 02-tau-calibration; then log "skip 02-tau-calibration"; else
  stage_start 02-tau-calibration
  ok=1
  for tau in $TAUS; do
    label=$(echo "$tau" | tr '.' 'p'); [ "$tau" = "1000000000" ] && label=inf
    for seed in $CAL_SEEDS; do
      d="$OUT/02-tau-calibration/raw/tau_${label}/seed_${seed}"
      log "calibration tau=$tau seed=$seed"
      PREFILL_SPEED_FILE="$CI_FILE" \
      STAGE_HARD_LIMIT=2400 \
      bash "$SCRIPT_DIR/final_stage.sh" "$d" "cal-${label}-s${seed}" \
        v4-paper-faithful-v6-bursty-r20-s${seed} -- \
        env PRISM_ROOT=/workspace/prism-exp PRISM_REPO="$ROOT/prism-research" \
            PRISM_EXP="$ROOT/exp" KVPR_TAU="$tau" \
            PREFILL_SPEED_FILE="$CI_FILE" BENCHMARK_TIMEOUT=1500 \
            bash "$SCRIPT_DIR/run_v4_case.sh" paper-faithful-v6 bursty 20 "$seed" \
              "$WL/bursty_r20_s${seed}.pkl" "$d" \
        >> "$OUT/02-tau-calibration/calibration.log" 2>&1 || ok=0
      [ "$ok" = "1" ] || break 2
    done
  done
  if [ "$ok" = "1" ] && $PY "$SCRIPT_DIR/final_select_tau.py" \
        --calibration "$OUT/02-tau-calibration/raw" \
        --out "$OUT/02-tau-calibration/FROZEN_TAU.json" \
        --summary "$OUT/02-tau-calibration/calibration_summary.csv" \
        --git-sha "$RUNTIME_FREEZE" --ci-file "$CI_FILE" \
        >> "$OUT/02-tau-calibration/calibration.log" 2>&1; then
    stage_end 02-tau-calibration PASS "" "$OUT/02-tau-calibration/FROZEN_TAU.json"
  else
    stage_end 02-tau-calibration FAIL "a calibration run failed or tau selection failed"
  fi
fi
TAU_FILE="$OUT/02-tau-calibration/FROZEN_TAU.json"
TAU=$($PY -c "import json;print(json.load(open('$TAU_FILE'))['tau'])")
log "frozen tau = $TAU"

# ---------------------------------------------------------------- stage 3
if stage_done 03-readiness; then log "skip 03-readiness"; else
  stage_start 03-readiness
  if $PY "$SCRIPT_DIR/final_readiness.py" --out-dir "$OUT" \
        --report "$OUT/03-readiness/BASELINE_READINESS_REPORT.md" \
        > "$OUT/03-readiness/readiness.log" 2>&1; then
    stage_end 03-readiness PASS "" "$OUT/03-readiness/BASELINE_READINESS_REPORT.md"
  else
    stage_end 03-readiness FAIL "readiness check reported a remaining blocker"
  fi
fi

# ---------------------------------------------------------------- stage 4
if stage_done 04-prototype-correction; then log "skip 04-prototype-correction"; else
  stage_start 04-prototype-correction
  ok=1
  for spec in "bursty 20 3" "steady 20 3"; do
    set -- $spec; wlkind=$1; rate=$2; seed=$3
    d="$OUT/04-prototype-correction/raw/${wlkind}/rate_${rate}/seed_${seed}"
    log "prototype correction $wlkind r$rate s$seed"
    STAGE_HARD_LIMIT=2400 \
    bash "$SCRIPT_DIR/final_stage.sh" "$d" "proto-${wlkind}-r${rate}-s${seed}" \
      v4-released-prototype-${wlkind}-r${rate}-s${seed} -- \
      env PRISM_ROOT=/workspace/prism-exp PRISM_REPO="$ROOT/prism-research" \
          PRISM_EXP="$ROOT/exp" BENCHMARK_TIMEOUT=1500 \
          bash "$SCRIPT_DIR/run_v4_case.sh" released-prototype "$wlkind" "$rate" "$seed" \
            "$WL/${wlkind}_r${rate}_s${seed}.pkl" "$d" \
      >> "$OUT/04-prototype-correction/correction.log" 2>&1 || ok=0
    [ "$ok" = "1" ] || break
  done
  if [ "$ok" = "1" ]; then
    stage_end 04-prototype-correction PASS "" "$OUT/04-prototype-correction/raw"
  else
    stage_end 04-prototype-correction FAIL "a prototype correction run failed"
  fi
fi

# ---------------------------------------------------------------- stage 5
if stage_done 05-final-c; then log "skip 05-final-c"; else
  stage_start 05-final-c
  ok=1
  for spec in "bursty 2" "bursty 4" "bursty 8" "bursty 14" "bursty 20" \
              "steady 4" "steady 8" "steady 20"; do
    set -- $spec; wlkind=$1; rate=$2
    for seed in 1 2 3; do
      d="$OUT/05-final-c/raw/${wlkind}/rate_${rate}/seed_${seed}"
      log "final-C $wlkind r$rate s$seed (tau=$TAU)"
      STAGE_HARD_LIMIT=2400 \
      bash "$SCRIPT_DIR/final_stage.sh" "$d" "finalc-${wlkind}-r${rate}-s${seed}" \
        v4-paper-faithful-v6-${wlkind}-r${rate}-s${seed} -- \
        env PRISM_ROOT=/workspace/prism-exp PRISM_REPO="$ROOT/prism-research" \
            PRISM_EXP="$ROOT/exp" KVPR_TAU="$TAU" \
            PREFILL_SPEED_FILE="$CI_FILE" BENCHMARK_TIMEOUT=1500 \
            bash "$SCRIPT_DIR/run_v4_case.sh" paper-faithful-v6 "$wlkind" "$rate" "$seed" \
              "$WL/${wlkind}_r${rate}_s${seed}.pkl" "$d" \
        >> "$OUT/05-final-c/final_c.log" 2>&1 || ok=0
      [ "$ok" = "1" ] || break 2
    done
  done
  if [ "$ok" = "1" ]; then
    stage_end 05-final-c PASS "" "$OUT/05-final-c/raw"
  else
    stage_end 05-final-c FAIL "a Final C run failed"
  fi
fi

# ---------------------------------------------------------------- stage 6
if stage_done 06-aggregate; then log "skip 06-aggregate"; else
  stage_start 06-aggregate
  if $PY "$SCRIPT_DIR/final_aggregate.py" --out-dir "$OUT" \
        > "$OUT/06-aggregate/aggregate.log" 2>&1; then
    stage_end 06-aggregate PASS "" "$OUT/06-aggregate"
  else
    stage_end 06-aggregate FAIL "aggregation failed"
  fi
fi

bash "$SCRIPT_DIR/notify.sh" "pipeline-complete" \
  "🎉 Prism pipeline COMPLETE | all stages PASS | results in 06-aggregate" || true
log "CHAIN COMPLETE"
