#!/bin/bash
# Run one baseline-readiness stage command and record its true exit status.
#
#   ./run_baseline_stage.sh <stage-dir> <command...>
#
# pipeline.rc is read by the watchdog and by every downstream gate, so it must
# never report success for a run that did not finish. Two ways it used to:
# `tee` masking the command's status, and the EXIT trap recording `$?` of some
# unrelated last command when the session is killed from outside. Both are
# closed below -- a killed stage records 143, not 0.
set -uo pipefail

STAGE_DIR=${1:?stage dir}
shift
mkdir -p "$STAGE_DIR/monitor"
PIPELINE_LOG="$STAGE_DIR/pipeline.log"
RC_FILE="$STAGE_DIR/pipeline.rc"
STAGE_RC=""

trap 'printf "%s\n" "${STAGE_RC:-$?}" > "$RC_FILE"' EXIT
for sig in TERM HUP INT; do
  trap "STAGE_RC=143; exit 143" "$sig"
done

echo "[$(date -u +%FT%TZ)] stage command: $*" | tee -a "$PIPELINE_LOG"
"$@" 2>&1 | tee -a "$PIPELINE_LOG"
STAGE_RC=${PIPESTATUS[0]}
echo "[$(date -u +%FT%TZ)] stage complete rc=$STAGE_RC" | tee -a "$PIPELINE_LOG"
exit "$STAGE_RC"
