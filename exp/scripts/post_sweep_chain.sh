#!/bin/bash
# Everything that happens after the Final Prism sweep, automatically.
#
# Gate first: this chain starts ONLY on 24/24 valid Final runs with zero
# correctness violations. Anything else notifies and stops -- it never
# "compares what it has".
#
# Then, in order: historical prototype provenance, recomputed prototype
# metrics and the comparison, the report, secret scan, commit, push, and the
# handoff. Heavy analysis is deliberately confined here, after the benchmark.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
EVAL="$ROOT/exp/results/final-evaluation"
AGG="$EVAL/06-aggregate"
PY=/workspace/prism-exp/prism-venv/bin/python
cd "$ROOT"
log() { echo "[$(date -u +%FT%TZ)] [post-sweep] $*" | tee -a "$EVAL/post_sweep.log"; }
say() { bash "$SCRIPT_DIR/notify.sh" "post-$1-$(date +%s)" "$2" || true; }

stop() { log "STOPPED: $1"; say "stop" "⛔ STOPPED | Post-sweep chain | $1"; exit 1; }

# ---------------------------------------------------------------- 0. gate
# This chain is armed at the same moment the sweep starts, so on entry the
# stage STATUS files still describe the PREVIOUS attempt. Reading them
# immediately turns a stale FAIL into a stop before the sweep has even stamped
# RUNNING -- which is exactly what happened at 14:55:59Z. So: ignore every
# verdict older than this chain, and wait for the sweep to claim the stage.
# Overridable so the chain can be re-armed against a sweep that is already
# running: pass the epoch seconds of that sweep's start.
CHAIN_START=${PRISM_CHAIN_SINCE:-$(date +%s)}
newer_than_start() {   # newer_than_start <status.json> -- is its verdict this attempt's?
  [ -f "$1" ] || return 1
  $PY - "$1" "$CHAIN_START" <<'PY2'
import json, sys, datetime
try:
    rec = json.load(open(sys.argv[1]))
    t = rec.get("started_at")
    if not t:
        sys.exit(1)
    started = datetime.datetime.fromisoformat(t).timestamp()
    # 120 s of slack: the sweep stamps RUNNING a moment before or after us.
    sys.exit(0 if started >= float(sys.argv[2]) - 120 else 1)
except Exception:
    sys.exit(1)
PY2
}

log "waiting for the sweep to claim stage 05-final-c"
armed=0
for _ in $(seq 1 120); do
  if newer_than_start "$EVAL/05-final-c/STATUS.json"; then armed=1; break; fi
  [ -f "$EVAL/STOP" ] && stop "a STOP is in force before the sweep started: $(cat "$EVAL/STOP")"
  sleep 5
done
[ "$armed" = "1" ] || stop "the sweep never claimed stage 05-final-c"
log "sweep is running this attempt's stage 05-final-c; watching for its verdict"

while true; do
  [ -f "$EVAL/STOP" ] && stop "a STOP is in force: $(cat "$EVAL/STOP")"
  if newer_than_start "$EVAL/06-aggregate/STATUS.json"; then
    grep -q '"result": "PASS"' "$EVAL/06-aggregate/STATUS.json" 2>/dev/null && break
    grep -q '"result": "FAIL"' "$EVAL/06-aggregate/STATUS.json" 2>/dev/null \
      && stop "stage 06-aggregate reported FAIL"
  fi
  grep -q '"result": "FAIL"' "$EVAL/05-final-c/STATUS.json" 2>/dev/null \
    && stop "stage 05-final-c reported FAIL"
  sleep 60
done

MAN="$AGG/FINAL_ONLY_MANIFEST.json"
[ -f "$MAN" ] || stop "no FINAL_ONLY_MANIFEST.json"
read -r COMPLETE RUNS VIOL STAGED ABORT CLIENTERR PASSN <<EOF
$($PY - "$MAN" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
c = m["correctness"]
print(m["final_complete"], m["runs_found"], c["alg2_order_violations_total"],
      c["staged_return_failures_total"], c["aborted_total"],
      c["client_errors_total"], c["runs_with_verdict_PASS"])
PY
)
EOF
log "final_complete=$COMPLETE runs=$RUNS verdictPASS=$PASSN order_violations=$VIOL staged_return_failures=$STAGED aborted=$ABORT client_errors=$CLIENTERR"
[ "$COMPLETE" = "True" ] && [ "$RUNS" = "24" ] || stop "Final arm is $RUNS/24, not a complete sweep"
[ "$PASSN" = "24" ] || stop "only $PASSN/24 runs carry a PASS verification"
[ "$VIOL" = "0" ] && [ "$STAGED" = "0" ] && [ "$CLIENTERR" = "0" ] \
  || stop "correctness violations present (order=$VIOL staged=$STAGED client=$CLIENTERR)"
say "sweep" "✅ SUCCESS | Final Prism Sweep Complete | 24/24"
log "gate passed: 24/24 valid, zero correctness violations"

# ------------------------------------------- 1. prototype provenance
log "verifying historical prototype provenance"
if ! $PY "$SCRIPT_DIR/historical_prototype_verify.py" \
       >> "$EVAL/post_sweep.log" 2>&1; then
  stop "historical prototype provenance pass failed to run"
fi
read -r V P N <<EOF
$($PY -c "
import json;t=json.load(open('$AGG/HISTORICAL_PROTOTYPE_PROVENANCE.json'))['tally']
print(t.get('VERIFIED',0), t.get('PARTIALLY_VERIFIED',0), t.get('NOT_VERIFIED',0))")
EOF
log "provenance: VERIFIED=$V PARTIALLY=$P NOT=$N"
USABLE=$((V + P))
if [ "$USABLE" = "0" ]; then
  say "prov" "⚠️ APPROVAL REQUIRED | Historical Prototype provenance insufficient
0/24 conditions could be verified -- no comparison was fabricated."
  log "no usable prototype conditions; the comparison is deliberately not made"
else
  say "prov" "✅ SUCCESS | Historical Prototype Verified | ${V}/24
(PARTIALLY_VERIFIED ${P}, NOT_VERIFIED ${N})"
fi

# ------------------------------------------- 2. recompute + compare
if [ "$USABLE" != "0" ]; then
  log "recomputing prototype metrics and comparing"
  if $PY "$SCRIPT_DIR/historical_prototype_compare.py" \
       >> "$EVAL/post_sweep.log" 2>&1; then
    say "cmp" "✅ SUCCESS | Prism vs Prototype Comparison Complete"
  else
    say "cmp" "⚠️ APPROVAL REQUIRED | Historical Prototype provenance insufficient
the comparison produced no paired condition; nothing was forced to line up."
    log "comparison produced no usable pairs"
  fi
fi

# ------------------------------------------- 3. report
log "building the report"
$PY "$SCRIPT_DIR/final_report_build.py" >> "$EVAL/post_sweep.log" 2>&1 \
  || stop "report build failed"

# ------------------------------------------- 4. results index + handoff
$PY "$SCRIPT_DIR/final_only_handoff.py" >> "$EVAL/post_sweep.log" 2>&1 \
  || log "WARNING: handoff manifest writer failed (see post_sweep.log)"

# ------------------------------------------- 5. secret scan, commit, push
log "secret scan"
bash "$SCRIPT_DIR/final_secret_scan.sh" > "$EVAL/SECRET_SCAN_THIS_SERVER.txt" 2>&1
scan_rc=$?
if [ "$scan_rc" != "0" ]; then
  cat "$EVAL/SECRET_SCAN_THIS_SERVER.txt" >> "$EVAL/post_sweep.log"
  stop "secret scan failed -- nothing was pushed"
fi
log "secret scan clean"

BRANCH=${PRISM_PUSH_BRANCH:-exp/final-prism-sweep}
git checkout -q -B "$BRANCH" 2>>"$EVAL/post_sweep.log"
git add -A >> "$EVAL/post_sweep.log" 2>&1
if git diff --cached --quiet; then
  log "nothing to commit"
else
  git -c user.name="Prism Baseline Agent" -c user.email="causslab@gmail.com" \
      commit -q -m "Final Prism 24-condition sweep on a fresh A100 80GB x2 server

Runtime rebuilt to the freeze 6618671 and verified byte-identical to
patches/final_baseline_ready/prism_research_worktree.patch; the built source is
now checked, not just the patch file. tau=0.00035, c_i and calibration reused
unchanged. The released-prototype arm was not run on this server, so the
comparison against the historical prototype is across server instances and is
labelled as such." >> "$EVAL/post_sweep.log" 2>&1 || stop "commit failed"
fi
LOCAL=$(git rev-parse HEAD)
log "pushing $BRANCH ($LOCAL)"
if ! git push -u origin "$BRANCH" >> "$EVAL/post_sweep.log" 2>&1; then
  stop "push failed"
fi
git fetch -q origin "$BRANCH" >> "$EVAL/post_sweep.log" 2>&1
REMOTE=$(git rev-parse "origin/$BRANCH" 2>/dev/null)
log "local=$LOCAL remote=$REMOTE"
if [ "$LOCAL" != "$REMOTE" ]; then
  stop "remote SHA does not match local ($REMOTE != $LOCAL)"
fi
$PY - "$EVAL/HANDOFF_STATUS.json" "$LOCAL" "$BRANCH" "$V" "$P" "$N" <<'PY'
import json, sys, datetime
path, sha, branch, v, p, n = sys.argv[1:]
json.dump({
  "HANDOFF_COMPLETE": "YES",
  "pushed_branch": branch,
  "pushed_sha": sha,
  "remote_verified": True,
  "final_arm": "24/24 PASS",
  "prototype_arm_this_server": "NOT RUN (by instruction)",
  "historical_prototype_provenance": {"VERIFIED": int(v),
                                      "PARTIALLY_VERIFIED": int(p),
                                      "NOT_VERIFIED": int(n)},
  "runtime_freeze": "6618671",
  "selected_tau": 0.00035,
  "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
}, open(path, "w"), indent=2)
PY
log "HANDOFF_COMPLETE=YES  remote $REMOTE verified"
say "done" "✅ SUCCESS | Prism Final Baseline Complete | 24/24 + report pushed
branch $BRANCH @ ${LOCAL:0:12} (remote verified)"
exit 0
