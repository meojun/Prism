#!/bin/bash
# Final Prism arm only: the 24 canonical conditions of stage 05-final-c, then
# aggregation. The released-prototype arm (stage 04) is NOT run on this server
# by instruction; its historical results are preserved as evidence and the
# limitation is recorded rather than papered over.
#
# The per-run invocation is copied verbatim from final_pipeline.sh stage 5, so
# a run started here is the same run the chain would have started. Nothing in
# the runtime, tau, c_i or the heuristics is touched.
set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
OUT="$ROOT/exp/results/final-evaluation"
WL="$ROOT/exp/workloads/final-evaluation"
PY=/workspace/prism-exp/prism-venv/bin/python
RUNTIME_FREEZE=${PRISM_RUNTIME_FREEZE:-6618671}
PROGRESS="$OUT/FINAL_SWEEP_PROGRESS.jsonl"
mkdir -p "$OUT/05-final-c" "$OUT/06-aggregate"

log() { echo "[$(date -u +%FT%TZ)] [final-only] $*" | tee -a "$OUT/pipeline.log"; }

status_path() { echo "$OUT/$1/STATUS.json"; }

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
}

stop_all() {   # stop_all <reason>
  bash "$SCRIPT_DIR/notify.sh" "final-only-stop-$(date +%s)" \
    "⛔ STOPPED | Final Prism Sweep | $1" || true
  log "SWEEP STOPPED: $1"
  exit 1
}

# ------------------------------------------------------ preconditions
[ -f "$OUT/STOP" ] && stop_all "a STOP is in force: $(cat "$OUT/STOP")"

blob_frozen=$(git -C "$ROOT" rev-parse "$RUNTIME_FREEZE:patches/final_baseline_ready/prism_research_worktree.patch" 2>/dev/null)
blob_now=$(git -C "$ROOT" hash-object patches/final_baseline_ready/prism_research_worktree.patch 2>/dev/null)
[ -n "$blob_frozen" ] && [ "$blob_frozen" = "$blob_now" ] \
  || stop_all "runtime does not match freeze $RUNTIME_FREEZE"

# The check above hashes the patch FILE in this repository. It says nothing
# about the source tree that will actually be imported: bootstrap.sh built
# prism-research from the paper_faithful + v3 patches, an earlier runtime with
# no `kvpr-global-v4` policy, and that passed the check above while being
# unable to start the Final arm at all. So verify the built source too.
bash "$SCRIPT_DIR/restore_frozen_runtime.sh" --verify-only >>"$OUT/pipeline.log" 2>&1 \
  || stop_all "the built prism-research source does not reproduce freeze $RUNTIME_FREEZE"
log "built runtime source verified against the freeze"

CI_FILE="$OUT/01-ci-profile/prefill_speed_final_a100.json"
want_ci=$($PY -c "import json;print(json.load(open('$ROOT/exp/final-handoff/calibration_manifest.json'))['c_i_sha256'])")
got_ci=$(sha256sum "$CI_FILE" 2>/dev/null | cut -d' ' -f1)
[ "$want_ci" = "$got_ci" ] || stop_all "c_i profile changed (want ${want_ci:0:12}, got ${got_ci:0:12})"

TAU_FILE="$OUT/02-tau-calibration/FROZEN_TAU.json"
TAU=$($PY -c "import json;print(json.load(open('$TAU_FILE'))['tau'])")
MAN_TAU=$($PY -c "import json;print(json.load(open('$ROOT/exp/final-handoff/resume_manifest.json'))['selected_tau'])")
[ "$TAU" = "$MAN_TAU" ] || stop_all "tau disagreement: FROZEN_TAU=$TAU manifest=$MAN_TAU"
log "frozen tau = $TAU (from $TAU_FILE, agrees with the resume manifest)"

[ -f "$OUT/FAIRNESS_GATE_PASS" ] || stop_all "the fairness gate has not passed"

$PY "$SCRIPT_DIR/verify_workloads.py" --workloads "$WL" \
  --manifest "$ROOT/exp/final-handoff/workloads_manifest.json" >/dev/null 2>&1 \
  || stop_all "the 24 canonical workloads do not verify"
log "24 canonical workloads verified"

# ------------------------------------- stage 04: not run on this server
$PY - "$OUT/04-prototype-fresh/PROTOTYPE_NOT_RUN_HERE.json" "$RUNTIME_FREEZE" <<'PY'
import json, sys, datetime
path, sha = sys.argv[1:]
json.dump({
  "stage": "04-prototype-fresh",
  "result": "SKIPPED_BY_INSTRUCTION",
  "git_sha": sha,
  "reason": "the released-prototype arm is deliberately not run on this server; "
            "its historical results are preserved as evidence and the "
            "cross-server limitation is stated in the report",
  "consequence": "no same-server paired prototype-vs-final comparison exists; "
                 "only bursty r2 s1 was run on a canonical workload under this "
                 "runtime freeze, on the previous server instance",
  "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
}, open(path, "w"), indent=2)
PY
log "stage 04-prototype-fresh -> SKIPPED_BY_INSTRUCTION (not run on this server)"

# ------------------------------------------------------------ stage 05
stage_start 05-final-c
ok=1
idx=0
for spec in "bursty 2" "bursty 4" "bursty 8" "bursty 14" "bursty 20" \
            "steady 4" "steady 8" "steady 20"; do
  set -- $spec; wlkind=$1; rate=$2
  for seed in 1 2 3; do
    idx=$((idx + 1))
    d="$OUT/05-final-c/raw/${wlkind}/rate_${rate}/seed_${seed}"
    log "final-C [$idx/24] $wlkind r$rate s$seed (tau=$TAU)"
    t0=$(date +%s)
    STAGE_HARD_LIMIT=2400 \
    bash "$SCRIPT_DIR/final_stage.sh" "$d" "finalc-${wlkind}-r${rate}-s${seed}" \
      v4-paper-faithful-v6-${wlkind}-r${rate}-s${seed} -- \
      env PRISM_ROOT=/workspace/prism-exp PRISM_REPO="$ROOT/prism-research" \
          PRISM_EXP="$ROOT/exp" KVPR_TAU="$TAU" \
          PREFILL_SPEED_FILE="$CI_FILE" BENCHMARK_TIMEOUT=1500 \
          bash "$SCRIPT_DIR/run_v4_case.sh" paper-faithful-v6 "$wlkind" "$rate" "$seed" \
            "$WL/${wlkind}_r${rate}_s${seed}.pkl" "$d" \
      >> "$OUT/05-final-c/final_c.log" 2>&1 || ok=0
    t1=$(date +%s)
    $PY - "$PROGRESS" "$idx" "$wlkind" "$rate" "$seed" "$ok" "$((t1-t0))" "$d" <<'PY'
import json, sys, datetime
path, idx, wl, rate, seed, ok, secs, d = sys.argv[1:]
rec = {"idx": int(idx), "workload": wl, "rate": int(rate), "seed": int(seed),
       "ok": ok == "1", "seconds": int(secs), "run_dir": d,
       "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
try:
    rec["numbers"] = json.load(open(d + "/VERIFICATION.json"))["numbers"]
except Exception:
    rec["numbers"] = None
with open(path, "a") as f:
    f.write(json.dumps(rec) + "\n")
PY
    if [ "$ok" != "1" ]; then
      stage_end 05-final-c FAIL "a Final Prism run failed: ${wlkind} r${rate} s${seed}"
      stop_all "stage=05-final-c run=${wlkind}_r${rate}_s${seed} -- see $OUT/STOP"
    fi
  done
done
n=$(ls -d "$OUT"/05-final-c/raw/*/rate_*/seed_* 2>/dev/null | wc -l)
stage_end 05-final-c PASS "" "$OUT/05-final-c/raw"
bash "$SCRIPT_DIR/notify.sh" "final-sweep-complete-$(date +%s)" \
  "✅ SUCCESS | Final Prism Sweep Complete | ${n}/24" || true

# ------------------------------------------------------------ stage 06
stage_start 06-aggregate
# final_aggregate.py exits non-zero while the prototype arm is short of 24. On
# this server that is expected and is not an aggregation failure, so the Final
# arm's own completeness is what decides the verdict.
$PY "$SCRIPT_DIR/final_aggregate.py" --out-dir "$OUT" \
  > "$OUT/06-aggregate/aggregate.log" 2>&1
agg_rc=$?
$PY "$SCRIPT_DIR/final_only_aggregate.py" --out-dir "$OUT" \
  >> "$OUT/06-aggregate/aggregate.log" 2>&1
own_rc=$?
if [ "$own_rc" = "0" ]; then
  stage_end 06-aggregate PASS "final arm aggregated; prototype arm not run here (final_aggregate rc=$agg_rc)" "$OUT/06-aggregate"
else
  stage_end 06-aggregate FAIL "final-only aggregation failed"
  stop_all "stage=06-aggregate final-only aggregation failed"
fi

log "FINAL-ONLY CHAIN COMPLETE"
# Stand the watchdog down from inside a live session, so it never sees this
# session disappear without a terminal state and report a vanish that did not
# happen.
tmux kill-session -t "=${PRISM_PIPELINE_SESSION:-prism-final}-watchdog" 2>/dev/null || true
exit 0
