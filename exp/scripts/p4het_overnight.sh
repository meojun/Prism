#!/bin/bash
# Start the 4-HET paired evaluation under tmux with its watchdog, and refuse to
# start at all if the watchdog does not come up. Nothing here is a child of the
# invoking shell: closing the terminal or dropping SSH does not touch it.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
OUT="$ROOT/exp/results/4het-paired"
SHARED_EVAL="$ROOT/exp/results/final-evaluation"
SESSION=${P4HET_SESSION:-p4het}
WATCH="${SESSION}-watchdog"
mkdir -p "$OUT"
cd "$ROOT"
log() { echo "[$(date -u +%FT%TZ)] [4het-launch] $*" | tee -a "$OUT/pipeline.log"; }

if tmux has-session -t "=$SESSION" 2>/dev/null; then
  log "FATAL: $SESSION is already running; refusing to start a second sweep"
  exit 1
fi
if [ -f "$SHARED_EVAL/STOP" ]; then
  log "FATAL: a STOP is in force: $(cat "$SHARED_EVAL/STOP")"
  log "a stop is cleared by a person, never by this script"
  exit 1
fi

tmux kill-session -t "$WATCH" 2>/dev/null || true
tmux new-session -d -s "$WATCH" \
  "P4HET_SESSION=$SESSION bash '$SCRIPT_DIR/p4het_watchdog.sh'"
ok=0
for _ in $(seq 1 10); do
  if tmux has-session -t "=$WATCH" 2>/dev/null \
     && pgrep -f "p4het_watchdog.sh" >/dev/null 2>&1; then ok=1; break; fi
  sleep 1
done
if [ "$ok" != "1" ]; then
  log "FATAL: the watchdog did not start; refusing to run the sweep unobserved"
  bash "$SCRIPT_DIR/notify.sh" "p4het-nowatchdog-$(date +%s)" \
    "⛔ STOPPED | 4-HET Evaluation | watchdog failed to start" || true
  exit 1
fi
log "watchdog up in tmux session $WATCH"

tmux new-session -d -s "$SESSION" \
  "bash '$SCRIPT_DIR/p4het_paired_sweep.sh' >> '$OUT/console.log' 2>&1"
ok=0
for _ in $(seq 1 10); do
  tmux has-session -t "=$SESSION" 2>/dev/null && { ok=1; break; }
  sleep 1
done
[ "$ok" = "1" ] || { log "FATAL: the sweep session did not start"
                     tmux kill-session -t "$WATCH" 2>/dev/null || true; exit 1; }
log "sweep up in tmux session $SESSION"
