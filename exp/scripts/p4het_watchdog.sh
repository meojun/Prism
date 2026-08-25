#!/bin/bash
# Watch the 4-HET paired sweep. Observe only: it never retries, patches or
# restarts anything. Its job is to make sure a silent death reaches the phone.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
OUT="$ROOT/exp/results/4het-paired"
SHARED_EVAL="$ROOT/exp/results/final-evaluation"
SESSION=${P4HET_SESSION:-p4het}
export PRISM_EVAL_DIR="$OUT"
log() { echo "[$(date -u +%FT%TZ)] [4het-watchdog] $*" >> "$OUT/watchdog.log"; }
mkdir -p "$OUT"
log "watching session $SESSION"

appeared=0
for _ in $(seq 1 60); do
  tmux has-session -t "=$SESSION" 2>/dev/null && { appeared=1; break; }
  sleep 2
done
if [ "$appeared" != "1" ]; then
  log "the sweep session never appeared"
  bash "$SCRIPT_DIR/notify.sh" "p4het-nostart-$(date +%s)" \
    "⛔ STOPPED | 4-HET Evaluation | the sweep session never started" || true
  exit 1
fi
log "session up; watching"

while true; do
  date -u +%FT%TZ > "$OUT/heartbeat"
  if ! tmux has-session -t "=$SESSION" 2>/dev/null; then
    if [ -f "$OUT/aggregate/SUMMARY.json" ]; then
      log "session gone with an aggregate written; the sweep completed"
      exit 0
    fi
    if [ -f "$SHARED_EVAL/STOP" ]; then
      log "session gone with a STOP recorded; it reported itself"
      exit 0
    fi
    log "session vanished with no terminal state; reporting"
    bash "$SCRIPT_DIR/notify.sh" "p4het-vanish-$(date +%s)" \
      "⛔ STOPPED | 4-HET Evaluation | the sweep session vanished" || true
    exit 1
  fi
  sleep 60
done
