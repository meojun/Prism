#!/bin/bash
# Report, handoff, secret scan, commit, push, and verify the remote SHA.
# Runs after the sweep, in the same session, so nothing races the benchmark
# and nothing has to be armed in advance.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
OUT="$ROOT/exp/results/4het-paired"
PY=/workspace/prism-exp/prism-venv/bin/python
export PRISM_EVAL_DIR="$OUT"
cd "$ROOT"
log() { echo "[$(date -u +%FT%TZ)] [4het-finalize] $*" | tee -a "$OUT/pipeline.log"; }
say() { bash "$SCRIPT_DIR/notify.sh" "p4het-fin-$1-$(date +%s)" "$2" || true; }
stop() { log "STOPPED: $1"; say stop "⛔ STOPPED | 4-HET Evaluation | $1"; exit 1; }

log "building the report"
$PY "$SCRIPT_DIR/p4het_report.py" >> "$OUT/pipeline.log" 2>&1 || stop "report build failed"

log "writing handoff state"
$PY "$SCRIPT_DIR/p4het_handoff.py" >> "$OUT/pipeline.log" 2>&1 \
  || log "WARNING: handoff writer failed (see pipeline.log)"

log "secret scan"
if ! bash "$SCRIPT_DIR/final_secret_scan.sh" > "$OUT/SECRET_SCAN.txt" 2>&1; then
  cat "$OUT/SECRET_SCAN.txt" >> "$OUT/pipeline.log"
  stop "secret scan failed -- nothing was pushed"
fi
log "secret scan clean"

BRANCH=${P4HET_BRANCH:-exp/4het-paired-evaluation}
git checkout -q -B "$BRANCH" 2>>"$OUT/pipeline.log"
git add -A >> "$OUT/pipeline.log" 2>&1
if git diff --cached --quiet; then
  log "nothing to commit"
else
  git -c user.name="Prism Baseline Agent" -c user.email="causslab@gmail.com" \
      commit -q -m "4-model paired Prism-vs-Prototype evaluation on A100 80GB x2

Both arms on one server over byte-identical canonical traces, paired per
condition. Runtime 6618671 with the built source verified byte-identical to the
freeze patch; tau=0.00035 and the four models' c_i carried over unchanged from
the A100 profile and frozen before any result was seen. The earlier 6-model
evaluation is retired to an exploratory/stress pilot: preserved, not
invalidated, and not mixed into these numbers." \
      >> "$OUT/pipeline.log" 2>&1 || stop "commit failed"
fi
LOCAL=$(git rev-parse HEAD)
log "pushing $BRANCH ($LOCAL)"
git push -u origin "$BRANCH" >> "$OUT/pipeline.log" 2>&1 || stop "push failed"
git fetch -q origin "$BRANCH" >> "$OUT/pipeline.log" 2>&1
REMOTE=$(git rev-parse "origin/$BRANCH" 2>/dev/null)
log "local=$LOCAL remote=$REMOTE"
[ "$LOCAL" = "$REMOTE" ] || stop "remote SHA does not match local ($REMOTE != $LOCAL)"

$PY - "$OUT/HANDOFF_STATUS.json" "$LOCAL" "$BRANCH" <<'PY'
import json, sys, datetime
path, sha, branch = sys.argv[1:]
json.dump({"HANDOFF_COMPLETE": "YES", "pushed_branch": branch,
           "pushed_sha": sha, "remote_verified": True,
           "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat()},
          open(path, "w"), indent=2)
PY
log "HANDOFF_COMPLETE=YES  remote $REMOTE verified"
say done "✅ SUCCESS | 4-HET Evaluation Complete | report pushed
branch $BRANCH @ ${LOCAL:0:12} (remote verified)"
exit 0
