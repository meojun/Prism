#!/bin/bash
# Resume the 4-HET sweep after an INFRASTRUCTURE failure only.
#
# The rule that decides is deliberately narrow and mechanical:
#
#   a run that produced NO result file measured nothing, so retrying it cannot
#   launder a finding -- that is infrastructure, and it is retried.
#
#   a run that produced a result and then failed a gate HAS measured something,
#   and that is a finding. It is never retried, never averaged away, and the
#   chain stays stopped for a person to read.
#
# On top of that a denylist stops the chain outright for classes that are real
# findings even when they leave no result: ordering, ownership, staged-return,
# deadlock and CUDA OOM.
#
# Every attempt is preserved with its own INVALID note, every decision is
# logged and pushed to the phone, and the retry budget is per run.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
OUT="$ROOT/exp/results/4het-paired"
SHARED_EVAL="$ROOT/exp/results/final-evaluation"
STOP="$SHARED_EVAL/STOP"
STATE="$OUT/SUPERVISOR_ATTEMPTS.json"
LOG="$OUT/supervisor.log"
SESSION=${P4HET_SESSION:-p4het}
MAX_RETRIES=${P4HET_MAX_RETRIES:-2}
export PRISM_EVAL_DIR="$OUT"
cd "$ROOT"
mkdir -p "$OUT"
log() { echo "[$(date -u +%FT%TZ)] [supervisor] $*" | tee -a "$LOG"; }
say() { bash "$SCRIPT_DIR/notify.sh" "sup-$1-$(date +%s)" "$2" || true; }
[ -f "$STATE" ] || echo '{}' > "$STATE"

# Real findings: never auto-resumed, whatever else is true.
DENY='order violation|ownership|identity mismatch|staged|deadlock|no-progress|CUDA OOM|out of memory|stale dispatch|request loss'

log "watching session $SESSION (max $MAX_RETRIES infrastructure retries per run)"

while true; do
  if tmux has-session -t "=$SESSION" 2>/dev/null; then sleep 60; continue; fi

  # Session gone. Finished, stopped, or vanished?
  if [ -f "$OUT/aggregate/SUMMARY.json" ]; then
    log "sweep finished and aggregated; standing down"
    exit 0
  fi
  if [ ! -f "$STOP" ]; then
    log "session gone with no STOP and no aggregate -- not resuming blindly"
    say vanish "⛔ STOPPED | 4-HET Evaluation | sweep session vanished with no terminal state"
    exit 1
  fi

  reason=$(cat "$STOP")
  # final_stage records the failing run's directory in parentheses.
  rundir=$(printf '%s' "$reason" | sed -nE 's/.*\(([^)]+)\).*/\1/p' | head -1)
  log "STOP in force: $reason"
  log "failing run dir: ${rundir:-unknown}"

  if printf '%s' "$reason" | grep -qiE "$DENY"; then
    log "classified as a REAL FINDING -- not resuming"
    say finding "⛔ STOPPED | 4-HET Evaluation | correctness finding, not auto-resumed
$reason"
    exit 1
  fi

  if [ -z "$rundir" ] || [ ! -d "$rundir" ]; then
    log "cannot identify the failing run directory -- not resuming"
    say unknown "⛔ STOPPED | 4-HET Evaluation | failing run not identifiable, not auto-resumed"
    exit 1
  fi

  # Did it measure anything? A result file means a finding, not a flake.
  if ls "$rundir"/*_e2e_*rep.json >/dev/null 2>&1; then
    log "the run produced a result file -- this is a finding, not infrastructure"
    say measured "⛔ STOPPED | 4-HET Evaluation | run produced a result then failed a gate; not auto-resumed
$reason"
    exit 1
  fi

  runid=$(basename "$(dirname "$(dirname "$rundir")")")_$(basename "$(dirname "$rundir")")_$(basename "$rundir")
  n=$(python3 -c "
import json,sys
try: d=json.load(open('$STATE'))
except Exception: d={}
print(d.get('$runid',0))")
  if [ "$n" -ge "$MAX_RETRIES" ]; then
    log "$runid has already been retried $n time(s) -- budget exhausted"
    say exhausted "⛔ STOPPED | 4-HET Evaluation | ${runid} failed $((n+1))x on infrastructure; not retrying further
$reason"
    exit 1
  fi
  n=$((n + 1))
  python3 -c "
import json
try: d=json.load(open('$STATE'))
except Exception: d={}
d['$runid']=$n
json.dump(d, open('$STATE','w'), indent=2)"

  # Preserve the attempt with its own note; never overwrite evidence.
  keep="${rundir}.autoresume-attempt${n}"
  mv "$rundir" "$keep" 2>/dev/null && \
    printf '%s\n' "INVALID_INFRASTRUCTURE_ATTEMPT_${n}: the run produced no result file, so it measured nothing. Auto-resumed by exp/scripts/p4het_supervisor.sh under the infrastructure-only rule. STOP text was: $reason" > "$keep/INVALID"
  log "preserved attempt $n at $keep"

  # Same pre-run hygiene the sweep does, and only when nothing owns them.
  if ! pgrep -f "sglang.launch_multi_model_server" >/dev/null 2>&1; then
    rm -f /dev/shm/ipc_*_root /dev/shm/cuda.shm.* 2>/dev/null || true
  fi
  mv "$STOP" "${STOP}.autoresumed-$(date -u +%Y%m%dT%H%M%SZ)" 2>/dev/null || rm -f "$STOP"
  say retry "🔁 AUTO-RESUME | 4-HET Evaluation | ${runid} attempt $((n+1))
infrastructure failure, no result produced. Completed runs are not repeated."
  log "resuming: attempt $((n+1)) for $runid"
  P4HET_SESSION="$SESSION" bash "$SCRIPT_DIR/p4het_overnight.sh" >> "$LOG" 2>&1 || {
    log "relaunch failed"; say relaunch "⛔ STOPPED | 4-HET Evaluation | relaunch failed"; exit 1; }
  sleep 30
done
