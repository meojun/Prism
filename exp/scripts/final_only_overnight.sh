#!/bin/bash
# Start the Final-only sweep under tmux with its watchdog, and refuse to start
# at all if the watchdog does not come up. Nothing here is a child of the
# invoking shell, so closing the SSH session does not touch it.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
EVAL="$ROOT/exp/results/final-evaluation"
SESSION=${PRISM_PIPELINE_SESSION:-prism-final}
WATCH="${SESSION}-watchdog"
cd "$ROOT"
log() { echo "[$(date -u +%FT%TZ)] [final-only-launch] $*" | tee -a "$EVAL/pipeline.log"; }

if tmux has-session -t "=$SESSION" 2>/dev/null; then
  log "FATAL: $SESSION is already running; refusing to start a second sweep"
  exit 1
fi
if [ -f "$EVAL/STOP" ]; then
  log "FATAL: a STOP is in force: $(cat "$EVAL/STOP")"
  log "a stop is cleared by a person, never by this script"
  exit 1
fi

tmux kill-session -t "$WATCH" 2>/dev/null || true
tmux new-session -d -s "$WATCH" \
  "PRISM_PIPELINE_SESSION=$SESSION bash '$SCRIPT_DIR/final_pipeline_watchdog.sh'"
ok=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if tmux has-session -t "=$WATCH" 2>/dev/null \
     && pgrep -f "final_pipeline_watchdog.sh" >/dev/null 2>&1; then ok=1; break; fi
  python3 -c "import time;time.sleep(1)"
done
if [ "$ok" != "1" ]; then
  log "FATAL: the watchdog did not start; refusing to run the sweep unobserved"
  bash "$SCRIPT_DIR/notify.sh" "final-only-nowatchdog-$(date +%s)" \
    "⛔ STOPPED | Final Prism Sweep | stage=startup | reason=watchdog failed to start" || true
  exit 1
fi
log "watchdog up in tmux session $WATCH"

tmux new-session -d -s "$SESSION" \
  "PRISM_PIPELINE_SESSION=$SESSION bash '$SCRIPT_DIR/final_only_sweep.sh' >> '$EVAL/pipeline_console.log' 2>&1"
ok=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if tmux has-session -t "=$SESSION" 2>/dev/null; then ok=1; break; fi
  python3 -c "import time;time.sleep(1)"
done
if [ "$ok" != "1" ]; then
  log "FATAL: the sweep session did not start"
  tmux kill-session -t "$WATCH" 2>/dev/null || true
  exit 1
fi
log "sweep up in tmux session $SESSION"

# The post-sweep chain waits on the sweep's own terminal state and then runs
# the historical-prototype provenance pass, the comparison, the report and the
# push. It is started here, not chained inside the sweep, so that heavy
# analysis is a separate process the benchmark never shares a session with --
# and so that killing the sweep never leaves the analysis half-done.
CHAIN="${SESSION}-post"
if ! tmux has-session -t "=$CHAIN" 2>/dev/null; then
  tmux new-session -d -s "$CHAIN" \
    "bash '$SCRIPT_DIR/post_sweep_chain.sh' >> '$EVAL/post_sweep_console.log' 2>&1"
  log "post-sweep chain armed in tmux session $CHAIN"
else
  log "post-sweep chain already armed in $CHAIN"
fi
