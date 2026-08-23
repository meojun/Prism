#!/bin/bash
# Start the overnight pipeline under tmux, with its watchdog, and refuse to
# start at all if the watchdog does not come up. Safe to run from a terminal
# that is about to be closed: nothing here is a child of the invoking shell.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
EVAL="$ROOT/exp/results/final-evaluation"
SESSION=${PRISM_PIPELINE_SESSION:-prism-overnight}
WATCH="${SESSION}-watchdog"
cd "$ROOT"
log() { echo "[$(date -u +%FT%TZ)] [overnight] $*" | tee -a "$EVAL/pipeline.log"; }

if tmux has-session -t "$SESSION" 2>/dev/null; then
  log "FATAL: $SESSION is already running; refusing to start a second chain"
  exit 1
fi
if [ -f "$EVAL/STOP" ]; then
  log "FATAL: a STOP is in force: $(cat "$EVAL/STOP")"
  log "a stop is cleared by a person, never by this script"
  exit 1
fi

# 1. watchdog first, so the chain is never unobserved
tmux kill-session -t "$WATCH" 2>/dev/null || true
tmux new-session -d -s "$WATCH" \
  "PRISM_PIPELINE_SESSION=$SESSION bash '$SCRIPT_DIR/final_pipeline_watchdog.sh'"
ok=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if tmux has-session -t "$WATCH" 2>/dev/null \
     && pgrep -f "final_pipeline_watchdog.sh" >/dev/null 2>&1; then ok=1; break; fi
  python3 -c "import time;time.sleep(1)"
done
if [ "$ok" != "1" ]; then
  log "FATAL: the watchdog did not start; refusing to run the pipeline unobserved"
  bash "$SCRIPT_DIR/notify.sh" "overnight-nowatchdog-$(date +%s)" \
    "⛔ STOPPED | Prism Baseline Pipeline | stage=startup | reason=watchdog failed to start" || true
  exit 1
fi
log "watchdog up in tmux session $WATCH"

# 2. the chain
tmux new-session -d -s "$SESSION" \
  "bash '$SCRIPT_DIR/final_pipeline.sh' >> '$EVAL/pipeline_console.log' 2>&1"
ok=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if tmux has-session -t "$SESSION" 2>/dev/null; then ok=1; break; fi
  python3 -c "import time;time.sleep(1)"
done
if [ "$ok" != "1" ]; then
  log "FATAL: the pipeline session did not start"
  tmux kill-session -t "$WATCH" 2>/dev/null || true
  exit 1
fi
log "pipeline up in tmux session $SESSION"
