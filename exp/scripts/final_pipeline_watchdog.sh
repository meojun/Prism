#!/bin/bash
# Watch the overnight pipeline. Observe only -- it never retries, patches or
# restarts anything. Its whole job is to make sure that if the chain dies
# silently, that fact reaches the phone instead of being discovered in the
# morning.
#
# Each tick it writes a heartbeat, and if the pipeline session is gone it
# decides which of two things happened: the chain finished (stage 06 passed),
# or it vanished. Only the second is reported as a stop.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
EVAL="$ROOT/exp/results/final-evaluation"
SESSION=${PRISM_PIPELINE_SESSION:-prism-overnight}
BEAT="$EVAL/pipeline_heartbeat"
PY=/workspace/prism-exp/prism-venv/bin/python

log() { echo "[$(date -u +%FT%TZ)] [watchdog] $*" >> "$EVAL/pipeline_watchdog.log"; }
log "watching session $SESSION"

last_stage() {
  local newest="" f
  for f in "$EVAL"/*/STATUS.json; do
    [ -f "$f" ] || continue
    [ -z "$newest" ] || [ "$f" -nt "$newest" ] && newest="$f"
  done
  [ -n "$newest" ] && basename "$(dirname "$newest")" || echo "unknown"
}

while true; do
  date -u +%FT%TZ > "$BEAT"
  if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    stage=$(last_stage)
    if grep -q '"result": "PASS"' "$EVAL/07-handoff/STATUS.json" 2>/dev/null; then
      log "session gone and 07-handoff passed; the chain completed"
      exit 0
    fi
    if [ -f "$EVAL/SAFE_TO_RELEASE.json" ]; then
      log "session gone with a release verdict recorded; the handoff reported itself"
      exit 0
    fi
    if grep -q '"result": "FAIL"' "$EVAL/07-handoff/STATUS.json" 2>/dev/null; then
      log "session gone after a handoff failure it already reported"
      exit 0
    fi
    if [ -f "$EVAL/STOP" ]; then
      log "session gone with a STOP already recorded at $stage; it reported itself"
      exit 0
    fi
    log "session gone at $stage with no terminal state; reporting"
    echo "the pipeline session vanished during $stage" > "$EVAL/STOP"
    bash "$SCRIPT_DIR/notify.sh" "watchdog-vanish-$(date +%s)" \
      "❌ FAILED | $stage | pipeline session vanished" || true
    bash "$SCRIPT_DIR/notify.sh" "watchdog-vanish-$(date +%s)-p" \
      "⛔ STOPPED | Prism Baseline Pipeline | stage=$stage | reason=pipeline session vanished" || true
    exit 1
  fi
  $PY -c "import time;time.sleep(60)"
done
