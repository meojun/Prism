#!/bin/bash
# Compose and send the STOP line, with as much of the run's state as exists.
#
#   notify_stop.sh <label> <stage_dir|-> <reason>
#
# Best effort throughout: a missing artifact costs a field, never the send, and
# never the caller's exit status.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
EVAL="${PRISM_EVAL_DIR:-$ROOT/exp/results/final-evaluation}"

LABEL=${1:-pipeline}
DIR=${2:--}
REASON=${3:-stopped}

stage="$LABEL"; tau=""; seed=""; progress=""; autopsy=""
# cal-0p07-s0 / finalc-bursty-r20-s3 / proto-steady-r20-s3
case "$LABEL" in
  cal-*) tau=$(echo "$LABEL" | sed -E 's/^cal-([0-9p]+)-s.*/\1/; s/p/./')
         seed=$(echo "$LABEL" | sed -E 's/.*-s([0-9]+)$/\1/')
         stage="tau-calibration" ;;
  finalc-*) stage="final-C"; seed=$(echo "$LABEL" | sed -E 's/.*-s([0-9]+)$/\1/') ;;
  proto*)   stage="prototype"; seed=$(echo "$LABEL" | sed -E 's/.*-s([0-9]+)$/\1/') ;;
esac

if [ "$DIR" != "-" ] && [ -d "$DIR" ]; then
  progress=$(python3 - "$DIR" <<'PY' 2>/dev/null
import glob, json, os, sys
d = sys.argv[1]
try:
    f = glob.glob(os.path.join(d, "*_e2e_*rep.json"))
    if f:
        r = json.load(open(f[0]))
        r = r[0] if isinstance(r, list) else r
        done, ab = r.get("completed"), r.get("aborted")
        if done is not None:
            print(f"{done}/{done + (ab or 0)}")
            raise SystemExit
    s = json.load(open(os.path.join(d, "monitor", "status.json")))
    ev = str(s.get("last_actual_progress_event") or "")
    if "completed_responses=" in ev:
        c = ev.split("completed_responses=")[1].split(",")[0]
        a = ev.split("arrivals=")[1].split(",")[0] if "arrivals=" in ev else "?"
        print(f"{c}/{a}")
except Exception:
    pass
PY
)
  [ -f "$DIR/FAILURE_AUTOPSY.json" ] && autopsy="autopsy ready"
  [ -f "$DIR/INVALID" ] && REASON="$REASON ($(cat "$DIR/INVALID" 2>/dev/null | head -1))"
fi

line="🔴 Prism STOPPED | $stage"
[ -n "$tau" ]      && line="$line | τ=$tau${seed:+ seed=$seed}"
[ -z "$tau" ] && [ -n "$seed" ] && line="$line | seed=$seed"
[ -n "$progress" ] && line="$line | $progress"
line="$line | $REASON"
[ -n "$autopsy" ]  && line="$line | $autopsy"

# The key must be unique per ATTEMPT, not per failure kind. Keying on
# label+reason silently swallowed a real STOP: cal-0p07-s0 failed the same way
# at 08:44 and again at 10:18, and the second was logged DUP and never reached
# the phone. Identity is therefore the absolute run directory, whatever makes
# this attempt distinct within it, and the moment the STOP was created --
# so re-entering the same hook for the same stop is still deduplicated, while a
# fresh attempt is always a fresh notification.
if [ "$DIR" != "-" ] && [ -d "$DIR" ]; then
  run_id=$(cd "$DIR" 2>/dev/null && pwd -P)
  # Whatever exists to distinguish this attempt: the run's own rc/start marker,
  # else the directory's mtime.
  attempt=$(stat -c %Y "$DIR/pipeline.rc" 2>/dev/null \
            || stat -c %Y "$DIR/STAGE_CMD.sh" 2>/dev/null \
            || stat -c %Y "$DIR" 2>/dev/null || echo 0)
else
  run_id="$EVAL/$LABEL"
  attempt=0
fi
# The STOP file's creation time pins the event itself.
stop_at=$(stat -c %Y "$EVAL/STOP" 2>/dev/null || date +%s)
key="stop-$(echo -n "$run_id|$attempt|$stop_at|$REASON" | md5sum | cut -c1-20)"
PRISM_NTFY_PRIORITY=high bash "$SCRIPT_DIR/notify.sh" "$key" "$line"
exit 0
