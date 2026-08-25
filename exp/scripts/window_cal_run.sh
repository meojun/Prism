#!/bin/bash
# Window calibration: 8 conditions x {30 s, 60 s} = 16 Prism runs.
#
# The ONLY intended difference between the paired arms is KVPR_WINDOW, which is
# already an environment variable in run_v4_case.sh -- no harness or runtime
# edit is needed and no other flag can drift. Both arms of a condition consume
# the SAME trace file, hash-verified against the frozen calibration manifest
# immediately before each run. Calibration seeds 3/4; seeds 5/6 are reserved as
# the untouched final hold-out and are NOT run here.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
OUT="$ROOT/exp/results/4het-window-calibration"
WL="$ROOT/exp/workloads/4het-cal"
MAN="$OUT/CALIBRATION_TRACE_MANIFEST.json"
CFG="$ROOT/exp/configs/v4het/4model_2gpu.json"
SLO="$ROOT/exp/configs/v4het/slo_base.json"
CI="$ROOT/exp/configs/v4het/prefill_speed_4het_a100.json"
PY=/workspace/prism-exp/prism-venv/bin/python
TAU=0.00035
COOLDOWN=30
SHARED_EVAL="$ROOT/exp/results/final-evaluation"
export PRISM_EVAL_DIR="$OUT"
mkdir -p "$OUT/raw"
log() { echo "[$(date -u +%FT%TZ)] [window-cal] $*" | tee -a "$OUT/pipeline.log"; }
stop_all() { bash "$SCRIPT_DIR/notify.sh" "wc-stop-$(date +%s)" \
  "⛔ STOPPED | window calibration | $1" || true; log "STOPPED: $1"; exit 1; }

CONDS="${WINDOW_CAL_CONDS:-steady:8:3 steady:8:4 steady:10:3 steady:10:4 bursty:8:3 bursty:8:4 bursty:10:3 bursty:10:4}"
WINDOWS="${WINDOW_CAL_WINDOWS:-30 60}"

[ -f "$SHARED_EVAL/STOP" ] && stop_all "a STOP is in force: $(cat "$SHARED_EVAL/STOP")"

WANT_SRC=$(cat "$ROOT/exp/analysis/estimator_correction/ESTIMATOR_RUNTIME.sha256")
SNAP=$(mktemp -d); trap 'rm -rf "$SNAP"' EXIT
PRISM_REPO="$ROOT/prism-research" bash "$SCRIPT_DIR/snapshot_source_patch.sh" "$SNAP" verify \
  >>"$OUT/pipeline.log" 2>&1 || stop_all "could not snapshot the built runtime source"
GOT_SRC=$(sha256sum "$SNAP/prism_research_worktree.patch" | cut -d\  -f1)
[ "$WANT_SRC" = "$GOT_SRC" ] || stop_all "built runtime is $GOT_SRC, expected $WANT_SRC"
log "runtime source verified: freeze 6618671 + estimator correction (${GOT_SRC:0:12})"

idx=0
for c in $CONDS; do
  IFS=: read -r kind rate seed <<< "$c"
  trace="$WL/${kind}_r${rate}_s${seed}.pkl"
  want=$($PY -c "
import json;m=json.load(open('$MAN'));print(m['files']['${kind}_r${rate}_s${seed}.pkl']['sha256'])")
  got=$(sha256sum "$trace" | cut -d' ' -f1)
  [ "$want" = "$got" ] || stop_all "trace hash mismatch for ${kind}_r${rate}_s${seed}"

  for W in $WINDOWS; do
    idx=$((idx + 1))
    d="$OUT/raw/prism-w${W}/${kind}/rate_${rate}/seed_${seed}"
    # re-hash for THIS arm so the pairing claim is evidence, not assumption
    got2=$(sha256sum "$trace" | cut -d' ' -f1)
    [ "$got2" = "$want" ] || stop_all "trace changed between arms for ${kind}_r${rate}_s${seed}"
    log "[$idx/16] window=${W}s $kind r$rate s$seed (trace ${got2:0:12} verified)"

    for _ in $(seq 1 30); do
      pgrep -f "sglang.launch_multi_model_server" >/dev/null 2>&1 || break
      sleep 2
    done
    pgrep -f "sglang.launch_multi_model_server" >/dev/null 2>&1 \
      || rm -f /dev/shm/ipc_*_root /dev/shm/cuda.shm.* 2>/dev/null || true

    t0=$(date +%s)
    STAGE_HARD_LIMIT=2400 \
    # The inner session name is NOT ours to choose: run_v4_case.sh builds it as
    # v4-${SYSTEM}-${WORKLOAD}-r${RATE}-s${SEED} (line 236). Passing anything
    # else makes the stage watchdog poll a session that never exists and kill a
    # healthy server. The window must stay out of it; it goes in the label.
    bash "$SCRIPT_DIR/final_stage.sh" "$d" "p4het-prism-w${W}-${kind}-r${rate}-s${seed}" \
      "$(echo "v4-paper-faithful-v6-${kind}-r${rate}-s${seed}" | tr '.' '_')" -- \
      env PRISM_ROOT=/workspace/prism-exp PRISM_REPO="$ROOT/prism-research" \
          PRISM_EXP="$ROOT/exp" PRISM_EVAL_DIR="$OUT" \
          CFG="$CFG" BENCHMARK_TIMEOUT=1500 KVPR_TAU="$TAU" \
          KVPR_WINDOW="$W" KVPR_COOLDOWN="$COOLDOWN" \
          PREFILL_SPEED_FILE="$CI" SLO_BASE_FILE="$SLO" \
          bash "$SCRIPT_DIR/run_v4_case.sh" paper-faithful-v6 "$kind" "$rate" "$seed" \
            "$trace" "$d" \
      >> "$OUT/sweep.log" 2>&1
    rc=$?; t1=$(date +%s)

    $PY - "$OUT/PROGRESS.jsonl" "$idx" "$W" "$kind" "$rate" "$seed" "$rc" "$((t1-t0))" "$d" <<'PYEOF'
import json, sys, datetime
path, idx, w, kind, rate, seed, rc, secs, d = sys.argv[1:]
rec = {"idx": int(idx), "window_s": int(w), "arm": f"prism-w{w}", "workload": kind,
       "rate": int(rate), "seed": int(seed), "ok": rc == "0",
       "seconds": int(secs), "run_dir": d,
       "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
try:
    rec["numbers"] = json.load(open(d + "/VERIFICATION.json"))["numbers"]
except Exception:
    rec["numbers"] = None
open(path, "a").write(json.dumps(rec) + "\n")
PYEOF
    [ "$rc" = "0" ] || stop_all "run w${W} ${kind}_r${rate}_s${seed} failed (rc=$rc)"
    [ "$idx" = "1" ] && log "gate run PASSED -- continuing"
  done
done
log "window calibration complete"
bash "$SCRIPT_DIR/notify.sh" "wc-done-$(date +%s)" \
  "✅ SUCCESS | window calibration complete | 16/16 runs" || true
exit 0
