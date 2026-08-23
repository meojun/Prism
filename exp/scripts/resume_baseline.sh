#!/bin/bash
# Resume the evaluation on a new server, from the manifest rather than memory.
#
#   bash exp/scripts/resume_baseline.sh [--dry-run]
#
# Reads exp/final-handoff/resume_manifest.json for what has already passed and
# what has not, checks that this machine is the one the manifest describes, and
# then hands over to the chain, which skips every completed run. Calibration is
# never repeated: tau is read from the manifest.
#
# Fails closed. If the runtime freeze, the workloads or tau do not match what
# the manifest recorded, it stops instead of running a comparison against
# different code or different work.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
EVAL="$ROOT/exp/results/final-evaluation"
HANDOFF="$ROOT/exp/final-handoff"
cd "$ROOT"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

PY=${PRISM_PY:-}
for c in "$PY" "$ROOT/prism-venv/bin/python" /workspace/prism-exp/prism-venv/bin/python python3; do
  [ -n "$c" ] && command -v "$c" >/dev/null 2>&1 && { PY=$c; break; }
done
[ -n "$PY" ] || { echo "FATAL: no python interpreter found" >&2; exit 1; }

for f in "$HANDOFF/resume_manifest.json" "$HANDOFF/workloads_manifest.json" \
         "$HANDOFF/calibration_manifest.json" "$ROOT/exp/FINAL_BASELINE_MANIFEST.json"; do
  [ -f "$f" ] || { echo "FATAL: missing handoff manifest $f" >&2; exit 1; }
done

read -r FREEZE TAU NEXT_STAGE NEXT_RUN PROTO_PASS PROTO_PEND FINAL_PASS FINAL_PEND CAL <<EOF
$($PY - "$HANDOFF/resume_manifest.json" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
p, f = m["prototype_arm"]["counts"], m["final_arm"]["counts"]
print(m["runtime_freeze"], m["selected_tau"], m["NEXT_STAGE"], m["NEXT_RUN"],
      p["PASS"], p["PENDING"], f["PASS"], f["PENDING"],
      m["calibration"]["status"])
PY
)
EOF

echo "Prism baseline resume"
echo "  runtime freeze : $FREEZE"
echo "  selected tau   : $TAU"
echo "  calibration    : $CAL (never re-run)"
echo "  prototype      : $PROTO_PASS passed, $PROTO_PEND remaining"
echo "  final          : $FINAL_PASS passed, $FINAL_PEND remaining"
echo "  next stage     : $NEXT_STAGE"
echo "  next run       : $NEXT_RUN"
echo

fail=0
say() { printf '  %-6s %s\n' "$1" "$2"; [ "$1" = "FAIL" ] && fail=1; return 0; }

echo "checks"
# The runtime must be the code the manifest was written against.
blob_frozen=$(git -C "$ROOT" rev-parse "$FREEZE:patches/final_baseline_ready/prism_research_worktree.patch" 2>/dev/null)
blob_now=$(git -C "$ROOT" hash-object patches/final_baseline_ready/prism_research_worktree.patch 2>/dev/null)
if [ -n "$blob_frozen" ] && [ "$blob_frozen" = "$blob_now" ]; then
  say PASS "runtime matches freeze $FREEZE"
else
  say FAIL "runtime does not match freeze $FREEZE ($blob_frozen vs $blob_now)"
fi

if $PY "$SCRIPT_DIR/verify_workloads.py" --workloads "$ROOT/exp/workloads/final-evaluation" \
     --manifest "$HANDOFF/workloads_manifest.json" >/dev/null 2>&1; then
  say PASS "all 24 canonical workloads present and matching"
else
  say FAIL "workloads missing or altered -- run: bash exp/scripts/restore_workloads.sh"
fi

ci="$EVAL/01-ci-profile/prefill_speed_final_a100.json"
want_ci=$($PY -c "import json;print(json.load(open('$HANDOFF/calibration_manifest.json'))['c_i_sha256'])" 2>/dev/null)
got_ci=$(sha256sum "$ci" 2>/dev/null | cut -d' ' -f1)
if [ -n "$want_ci" ] && [ "$want_ci" = "$got_ci" ]; then
  say PASS "c_i profile matches the one tau was chosen against"
else
  say FAIL "c_i profile missing or changed (expected ${want_ci:0:12}, got ${got_ci:0:12})"
fi

if [ "$TAU" = "None" ] || [ -z "$TAU" ]; then
  say FAIL "no tau in the manifest"
else
  say PASS "tau read from the manifest, not from a person"
fi

if [ -f "$EVAL/STOP" ]; then
  say FAIL "a STOP is in force: $(cat "$EVAL/STOP")"
else
  say PASS "no STOP in force"
fi

echo
# The plan comes from the manifest and is the same wherever it is read. Whether
# this machine can carry it out is a separate question, answered above -- so a
# dry run reports both, and a machine that is not provisioned yet still gets a
# straight answer about what would run.
if [ "$DRY" = "1" ]; then
  agg=$($PY -c "import json;print(json.load(open('$HANDOFF/resume_manifest.json'))['aggregation']['status'])")
  echo "DRY RUN -- nothing started."
  echo "next = $NEXT_STAGE / $NEXT_RUN"
  echo "prototype remaining = $PROTO_PEND"
  echo "final remaining = $FINAL_PEND"
  echo "aggregation = $agg"
  echo
  if [ "$fail" = "0" ]; then
    echo "this machine is ready: bash exp/scripts/resume_baseline.sh"
  else
    echo "this machine is NOT ready yet -- see the FAIL lines above."
    echo "on a fresh server that normally means:"
    echo "  ./bootstrap.sh"
    echo "  bash exp/scripts/restore_workloads.sh"
  fi
  exit 0
fi

if [ "$fail" != "0" ]; then
  echo "REFUSING TO RESUME: the checks above must pass first."
  exit 1
fi

echo "starting the chain; it skips every run that already passed"
exec bash "$SCRIPT_DIR/final_overnight.sh"
