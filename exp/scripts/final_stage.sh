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
if [ -f "$rc_file" ] && [ "$(cat "$rc_file")" = "0" ] \
   && [ -f "$status" ] && grep -q '"state": "COMPLETE"' "$status"; then
  echo "[final_stage] SKIP $LABEL: already COMPLETE"
  exit 0
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
pkill -f "sglang.launch_multi_model_server" 2>/dev/null || true
sleep 8

state=$(python3 - "$status" <<'PY' 2>/dev/null || echo UNKNOWN
import json, sys
try:
    print(json.load(open(sys.argv[1]))["state"])
except Exception:
    print("UNKNOWN")
PY
)
echo "[final_stage] $LABEL rc=$rc state=$state"
[ "$rc" = "0" ] && [ "$state" = "COMPLETE" ]
