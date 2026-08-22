#!/bin/bash
# Launch one baseline-readiness stage under tmux + heartbeat watchdog.
#
#   ./launch_baseline_stage.sh <stage-dir> <label> <inner-session> -- <command...>
#
# A benchmark must never depend on an agent session staying alive, so the
# command runs in its own tmux session, the watchdog runs in a second one, and
# every timeout is explicit.  The exact command is written to
# <stage-dir>/STAGE_CMD.sh first: that is both the quoting-safe way to hand it
# to tmux and the record of what was actually run.
#
# State: <stage-dir>/monitor/status.json (RUNNING|COMPLETE|FAIL) and
# <stage-dir>/pipeline.rc.  A stage whose status.json says COMPLETE must not be
# rerun.
set -euo pipefail

STAGE_DIR=${1:?usage: launch_baseline_stage.sh <stage-dir> <label> <inner-session> -- <command...>}
LABEL=${2:?}
INNER_SESSION=${3:?}
shift 3
[ "${1:-}" = "--" ] || { echo "FATAL: expected -- before the command" >&2; exit 1; }
shift

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
SERVER_TIMEOUT=${SERVER_TIMEOUT:-900}
BENCH_TIMEOUT=${BENCH_TIMEOUT:-1500}
NO_PROGRESS_TIMEOUT=${NO_PROGRESS_TIMEOUT:-180}

mkdir -p "$STAGE_DIR/monitor"
STAGE_DIR=$(readlink -f "$STAGE_DIR")

STATUS="$STAGE_DIR/monitor/status.json"
if [ -f "$STATUS" ] && grep -q '"state": "COMPLETE"' "$STATUS"; then
  echo "SKIP: $LABEL already COMPLETE ($STATUS); refusing to rerun a SUCCESS"
  exit 0
fi
rm -f "$STAGE_DIR/monitor/FAIL" "$STAGE_DIR/pipeline.rc"

PIPE_SESSION="stage-$LABEL"
MON_SESSION="mon-$LABEL"
for s in "$PIPE_SESSION" "$MON_SESSION" "$INNER_SESSION"; do
  tmux kill-session -t "$s" 2>/dev/null || true
done

CMD_FILE="$STAGE_DIR/STAGE_CMD.sh"
{
  echo "#!/bin/bash"
  echo "set -euo pipefail"
  echo "cd $ROOT"
  printf '%q ' "$@"
  echo
} > "$CMD_FILE"
chmod +x "$CMD_FILE"

tmux new-session -d -s "$PIPE_SESSION" \
  "cd $ROOT && bash exp/scripts/run_baseline_stage.sh $STAGE_DIR bash $CMD_FILE"
tmux new-session -d -s "$MON_SESSION" \
  "cd $ROOT && python3 exp/scripts/monitor_baseline_stage.py \
     --stage-dir $STAGE_DIR \
     --pipeline-session $PIPE_SESSION --inner-session $INNER_SESSION \
     --server-timeout $SERVER_TIMEOUT --benchmark-timeout $BENCH_TIMEOUT \
     --no-progress-timeout $NO_PROGRESS_TIMEOUT \
     >> $STAGE_DIR/monitor.log 2>&1"

echo "launched $LABEL"
echo "  stage dir : $STAGE_DIR"
echo "  command   : $CMD_FILE"
echo "  sessions  : $PIPE_SESSION (pipeline), $MON_SESSION (watchdog), $INNER_SESSION (server)"
echo "  status    : $STATUS"
