#!/bin/bash
# Run one benchmark stage to completion, blocking, under the existing tmux +
# watchdog harness. Returns non-zero if the stage did not finish cleanly.
#
#   ./final_stage.sh <stage-dir> <label> <inner-session> -- <command...>
#
# A stage whose monitor/status.json already says COMPLETE with pipeline.rc 0 is
# skipped: a SUCCESS is never rerun.
set -uo pipefail

STAGE_DIR=${1:?stage dir}; LABEL=${2:?label}; INNER=${3:?inner session}
shift 3
[ "${1:-}" = "--" ] || { echo "expected --" >&2; exit 2; }
shift

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
mkdir -p "$STAGE_DIR"
STAGE_DIR=$(readlink -f "$STAGE_DIR")

rc_file="$STAGE_DIR/pipeline.rc"
status="$STAGE_DIR/monitor/status.json"
# A run that wrote rc=0 and its result file is finished, whatever the monitor
# managed to record before the stage killed it.
if [ -f "$rc_file" ] && [ "$(cat "$rc_file")" = "0" ] \
   && ls "$STAGE_DIR"/*_e2e_*rep.json >/dev/null 2>&1; then
  echo "[final_stage] SKIP $LABEL: already COMPLETE"
  exit 0
fi

ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
. "$SCRIPT_DIR/proc_ownership.sh"

# A run that finished and passed every gate reports itself as a success, in the
# same shape as the failure path reports a stop. The key includes the run's own
# rc file timestamp so a re-attempt of the same label is a separate message and
# is never deduplicated against an earlier one.
notify_run_success() {
  local line key stamp v
  v="$STAGE_DIR/VERIFICATION.json"
  stamp=$(stat -c %Y "$STAGE_DIR/pipeline.rc" 2>/dev/null || echo 0)
  key="ok-$(printf '%s|%s|%s' "$STAGE_DIR" "$LABEL" "$stamp" | md5sum | cut -c1-20)"
  local counts
  counts=$($PY - "$v" <<'PY2' 2>/dev/null || echo "?/?"
import json, sys
try:
    n = json.load(open(sys.argv[1]))["numbers"]
    print(f"{n['completed']}/{n['offered_requests']}")
except Exception:
    print("?/?")
PY2
)
  case "$LABEL" in
    cal-*)
      local tl seed
      tl=${LABEL#cal-}; seed=${tl##*-s}; tl=${tl%-s*}
      line="✅ SUCCESS | Calibration | τ=$(echo "$tl" | tr 'p' '.') seed=${seed} | ${counts}" ;;
    protofresh-*|proto-*)
      local r; r=${LABEL#protofresh-}; r=${r#proto-}
      line="✅ SUCCESS | Prototype | $(echo "$r" | tr '-' ' ') | ${counts}" ;;
    finalc-*)
      local r; r=${LABEL#finalc-}
      line="✅ SUCCESS | Final Prism | $(echo "$r" | tr '-' ' ') | ${counts}" ;;
    *)
      line="✅ SUCCESS | ${LABEL} | ${counts}" ;;
  esac
  bash "$SCRIPT_DIR/notify.sh" "$key" "$line" || true
}
EVAL="$ROOT/exp/results/final-evaluation"
PY=/workspace/prism-exp/prism-venv/bin/python

# A gate that failed earlier must not be walked past on the next run.
if [ -f "$EVAL/STOP" ]; then
  echo "[final_stage] STOP in force: $(cat "$EVAL/STOP")" >&2
  exit 1
fi

# The runtime must still be the frozen one, checked before every expensive run.
frozen_blob=$(git -C "$ROOT" rev-parse "${PRISM_RUNTIME_FREEZE:-444a216}:patches/final_baseline_ready/prism_research_worktree.patch" 2>/dev/null)
now_blob=$(git -C "$ROOT" hash-object patches/final_baseline_ready/prism_research_worktree.patch 2>/dev/null)
if [ -n "$frozen_blob" ] && [ "$frozen_blob" != "$now_blob" ]; then
  echo "frozen source hash mismatch: $frozen_blob != $now_blob" > "$EVAL/STOP"
  bash "$SCRIPT_DIR/notify_stop.sh" "$LABEL" "-" "frozen source hash mismatch" || true
  echo "[final_stage] STOP: frozen source hash mismatch" >&2
  exit 1
fi

case "$LABEL" in
  cal-*)
    # tau calibration may not start until the c_i measurement has been checked.
    sanity="$EVAL/01-ci-profile/CI_SANITY.json"
    if [ ! -f "$sanity" ]; then
      $PY "$SCRIPT_DIR/final_ci_sanity.py"         --profile-dir "$EVAL/01-ci-profile" --out "$sanity"         --previous "$ROOT/exp/configs/v2/prefill_speed.json"         > "$EVAL/01-ci-profile/ci_sanity.log" 2>&1 || true
    fi
    if ! grep -q '"verdict": "PASS"' "$sanity" 2>/dev/null; then
      echo "C_I_SANITY_FAIL: see 01-ci-profile/CI_SANITY.json" > "$EVAL/STOP"
      bash "$SCRIPT_DIR/notify_stop.sh" "$LABEL" "-" "c_i sanity failed" || true
      echo "[final_stage] STOP: C_I_SANITY_FAIL" >&2
      exit 1
    fi
    ;;
  protofresh-*)
    : ;;                     # produced by the fairness orchestrator itself
  finalc-*|proto-*)
    # Neither arm of the comparison starts until the fairness branch has
    # settled which prototype results the comparison may use. That decision is
    # made by final_fairness_orchestrator.sh, which may have 24 prototype runs
    # to do first, so this waits for it rather than stopping the chain.
    waited=0
    while [ ! -f "$EVAL/FAIRNESS_GATE_PASS" ]; do
      if [ -f "$EVAL/STOP" ]; then
        echo "[final_stage] STOP while waiting on the fairness gate: $(cat "$EVAL/STOP")" >&2
        exit 1
      fi
      if [ "$waited" -ge "${FAIRNESS_WAIT_LIMIT:-43200}" ]; then
        echo "fairness gate never settled after ${waited}s" > "$EVAL/STOP"
        bash "$SCRIPT_DIR/notify_stop.sh" "$LABEL" "-" "fairness gate timed out" || true
        echo "[final_stage] STOP: fairness gate timed out" >&2
        exit 1
      fi
      [ $((waited % 600)) = 0 ] && echo "[final_stage] $LABEL waiting on the fairness gate (${waited}s)"
      sleep 30; waited=$((waited + 30))
    done
    ;;
esac

# The benchmark client must be able to reach its descriptor limit before the
# run starts. Under a low limit the client fails to open connections and the
# run measures the client rather than the system.
if ! $PY "$SCRIPT_DIR/check_client_fd.py" --mode preflight \
     --out "$STAGE_DIR/client_fd_preflight.json" >/dev/null 2>&1; then
  echo "[final_stage] STOP: client fd preflight failed" >&2
  cat "$STAGE_DIR/client_fd_preflight.json" 2>/dev/null >&2
  echo "client fd preflight failed before $LABEL" > "$EVAL/STOP"
  bash "$SCRIPT_DIR/notify_stop.sh" "$LABEL" "-" "client fd preflight failed" || true
  exit 1
fi

echo "[final_stage] RUN $LABEL -> $STAGE_DIR"
SERVER_TIMEOUT=${SERVER_TIMEOUT:-1200} \
BENCH_TIMEOUT=${BENCH_TIMEOUT:-1800} \
NO_PROGRESS_TIMEOUT=${NO_PROGRESS_TIMEOUT:-240} \
  bash "$SCRIPT_DIR/launch_baseline_stage.sh" "$STAGE_DIR" "$LABEL" "$INNER" -- "$@" \
  >/dev/null || { echo "[final_stage] launch failed"; exit 1; }

# Block until the harness reports one way or the other. The watchdog owns the
# timeouts; this loop only waits for its verdict.
deadline=$(( $(date +%s) + ${STAGE_HARD_LIMIT:-3600} ))
while true; do
  if [ -f "$rc_file" ]; then
    rc=$(cat "$rc_file")
    # Let the monitor observe the rc before it is killed. It polls every 5 s,
    # so killing it the instant the file appears froze status.json at RUNNING
    # on a run that had in fact finished.
    for _ in 1 2 3; do
      grep -q '"state": "\(COMPLETE\|FAIL\)"' "$status" 2>/dev/null && break
      sleep 3
    done
    break
  fi
  if [ -f "$STAGE_DIR/monitor/FAIL" ]; then
    sleep 15                      # let the wrapper write its rc
    rc=$(cat "$rc_file" 2>/dev/null || echo 1)
    break
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "[final_stage] $LABEL exceeded STAGE_HARD_LIMIT" >&2
    tmux kill-session -t "stage-$LABEL" 2>/dev/null || true
    tmux kill-session -t "mon-$LABEL" 2>/dev/null || true
    tmux kill-session -t "$INNER" 2>/dev/null || true
    rc=124
    break
  fi
  sleep 10
done

for s in "stage-$LABEL" "mon-$LABEL" "$INNER"; do
  tmux kill-session -t "$s" 2>/dev/null || true
done
# Only the process group this run started. The wildcard that was here matched
# every server on the machine, and two servers died to an unexplained SIGKILL on
# 2026-08-23 that it could not be ruled out for.
prism_kill_server "$STAGE_DIR" "final_stage teardown" 2>/dev/null || true
sleep 8
# A server that did not exit cleanly leaves /dev/shm/ipc_*_root behind, and the
# next server dies on the partial set: a killed smoke run left ipc_0_0 through
# ipc_1_3 without ipc_0_3, and cal-0p07-s0's server was killed at startup with
# KeyError: '/ipc_0_3_root'. Nothing else owns these names.
if ! pgrep -f "sglang.launch_multi_model_server" >/dev/null 2>&1; then
  # Read-only probe: pgrep only lists, it never signals.
  rm -f /dev/shm/ipc_*_root 2>/dev/null || true
fi

state=$(python3 - "$status" <<'PY' 2>/dev/null || echo UNKNOWN
import json, sys
try:
    print(json.load(open(sys.argv[1]))["state"])
except Exception:
    print("UNKNOWN")
PY
)
echo "[final_stage] $LABEL rc=$rc state=$state"

blocker=""
L="$STAGE_DIR/server-logs"
# One 'Too many open files' or connection failure and the numbers are the
# client's, not the system's. Checked before anything else, because it explains
# symptoms that otherwise look like server faults.
if ! $PY "$SCRIPT_DIR/check_client_fd.py" --mode postrun --run "$STAGE_DIR" \
     --out "$STAGE_DIR/CLIENT_FD_VALIDATION.json" >/dev/null 2>&1; then
  blocker="client fd exhaustion / connection failures"
  echo "INVALID_CLIENT_FD_EXHAUSTION" > "$STAGE_DIR/INVALID"
  rc=1
fi
grep -qE "torch\.OutOfMemoryError|CUDA out of memory|cuMemCreate" "$L/server.log" "$L/stdout.log" 2>/dev/null \
  && blocker="CUDA OOM"
# Algorithm 2's runtime invariants -- stale dispatched sequences, ordering and
# ownership identity -- are checked from the run's own logs before its numbers
# are allowed to count.
$PY "$SCRIPT_DIR/check_alg2_interaction.py" --run "$STAGE_DIR" \
  --out "$STAGE_DIR/ALG2_INTERACTION.json" > "$STAGE_DIR/alg2_interaction.log" 2>&1 || true
if [ -z "$blocker" ] && ! grep -q '"verdict": "PASS"' "$STAGE_DIR/ALG2_INTERACTION.json" 2>/dev/null; then
  failed=$($PY - "$STAGE_DIR/ALG2_INTERACTION.json" <<'PY2'
import json, sys
try:
    r = json.load(open(sys.argv[1]))
    print(",".join(c["check"] for c in r["checks"] if not c["pass"]) or "unreadable")
except Exception:
    print("interaction report missing")
PY2
)
  blocker="Algorithm 2 interaction gate FAIL: $failed"
fi
[ -z "$blocker" ] && grep -qE "NCCL error|ncclUnhandledCudaError|CUDA error:" "$L/server.log" 2>/dev/null \
  && blocker="fatal CUDA/NCCL"
[ -z "$blocker" ] && grep -q '"order_ok": false' "$L/server.log.gpu_scheduler.log" 2>/dev/null \
  && blocker="Algorithm 2 ordering violation"
# If the server disappeared without the harness asking it to, gather the
# evidence now, while /proc and the cgroup counters still mean something.
if grep -q "inner server session exited without result" \
     "$STAGE_DIR/monitor/FAIL" 2>/dev/null \
   || grep -q "Killed  " "$L/stdout.log" 2>/dev/null; then
  prism_capture_death "$STAGE_DIR" "server vanished during $LABEL" \
    >> "$EVAL/autopsy.log" 2>&1 || true
fi

[ -z "$blocker" ] && [ -f "$STAGE_DIR/monitor/FAIL" ] \
  && grep -q "no actual progress" "$STAGE_DIR/monitor/FAIL" 2>/dev/null \
  && blocker="no-progress / deadlock"
if [ -n "$blocker" ]; then
  echo "$blocker in $LABEL ($STAGE_DIR)" > "$EVAL/STOP"
  bash "$SCRIPT_DIR/notify_stop.sh" "$LABEL" "$STAGE_DIR" "$blocker" || true
  echo "[final_stage] STOP: $blocker -- diagnosing before standing down" >&2
  $PY "$SCRIPT_DIR/final_failure_autopsy.py" --run "$STAGE_DIR" --label "$LABEL" \
    --out "$STAGE_DIR/FAILURE_AUTOPSY.json" \
    >> "$EVAL/autopsy.log" 2>&1 || true
  prism_kill_server "$STAGE_DIR" "final_stage blocker path" 2>/dev/null || true
  sleep 5
  ( cd "$ROOT" && git add -A -- exp/results/final-evaluation >/dev/null 2>&1 \
    && git -c user.name="Prism Baseline Agent" -c user.email="causslab@gmail.com" \
       commit -q -m "STOP: $blocker in $LABEL (autopsy attached, no code changed)" \
    && git push origin exp/final-baseline-ready ) >> "$EVAL/autopsy.log" 2>&1 || true
  exit 1
fi

if [ "$rc" = "0" ] && [ "$state" = "COMPLETE" ]; then
  if $PY "$SCRIPT_DIR/final_run_verify.py" --run "$STAGE_DIR" --label "$LABEL" \
       --out "$STAGE_DIR/VERIFICATION.json" >> "$STAGE_DIR/verification.log" 2>&1; then
    notify_run_success
    exit 0
  fi
  why=$($PY -c "import json;r=json.load(open('$STAGE_DIR/VERIFICATION.json'));print(','.join(r['failed_gates']+r['gates_without_evidence']) or 'unknown')" 2>/dev/null || echo "verification unreadable")
  echo "per-run verification failed in $LABEL: $why" > "$EVAL/STOP"
  bash "$SCRIPT_DIR/notify_stop.sh" "$LABEL" "$STAGE_DIR" "verification: $why" || true
  echo "[final_stage] STOP: per-run verification failed -- $why" >&2
  exit 1
fi
exit 1
