#!/bin/bash
# Make sure the handoff happens however the chain ends.
#
# The pipeline runs the handoff itself when it reaches aggregation. It cannot
# run it when it stops early -- a stage that fails exits the chain. That is
# exactly the case where a handoff matters most: the server is being released
# in the morning and a stopped pipeline still has to be picked up elsewhere.
#
# So this waits for the chain to be over, by any route, and then runs the
# handoff if nothing else already did. It never touches a benchmark, and it
# never starts packaging while a server is up.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
EVAL="$ROOT/exp/results/final-evaluation"
SESSION=${PRISM_PIPELINE_SESSION:-prism-overnight}
PY=/workspace/prism-exp/prism-venv/bin/python
cd "$ROOT"

log() { echo "[$(date -u +%FT%TZ)] [handoff-sup] $*" >> "$EVAL/handoff_supervisor.log"; }
log "waiting for the chain to end (session $SESSION)"

# Wait for the session to exist before deciding it has ended.
for _ in $(seq 1 60); do
  tmux has-session -t "=$SESSION" 2>/dev/null && break
  $PY -c "import time;time.sleep(2)"
done

while true; do
  if tmux has-session -t "=$SESSION" 2>/dev/null; then
    $PY -c "import time;time.sleep(60)"
    continue
  fi
  # The session is gone. Give anything mid-teardown a moment, and never start
  # heavy packaging while a server is still up.
  $PY -c "import time;time.sleep(30)"
  waited=0
  while pgrep -f "sglang.launch_multi_model_server" >/dev/null 2>&1; do
    [ "$waited" -ge 600 ] && break
    $PY -c "import time;time.sleep(15)"; waited=$((waited + 15))
  done
  break
done

if [ -f "$EVAL/SAFE_TO_RELEASE.json" ]; then
  log "a release verdict already exists; the chain ran its own handoff"
  exit 0
fi

log "chain ended without a handoff; running it"
bash "$SCRIPT_DIR/final_handoff.sh" >> "$EVAL/handoff_console.log" 2>&1
rc=$?
log "handoff finished rc=$rc"
exit $rc
