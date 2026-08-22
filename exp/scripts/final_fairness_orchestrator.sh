#!/bin/bash
# Decide, without further human input, which prototype results the final
# comparison may use -- and if it may use none of them, produce a fresh
# prototype arm on the canonical workload set.
#
# Waits for tau calibration to pass, audits the prototype's workload
# provenance, then:
#
#   CASE A  24/24 recovered and verified  -> reuse the 22 good runs and let the
#           chain do its 2 correction runs on the same workloads.
#   CASE B  any cell unverified           -> the old prototype results are not
#           used in the paired comparison. The committed ShareGPT workloads
#           become the one canonical set, and the prototype arm is re-run in
#           full, 24 runs, on exactly those files. Nothing old is overwritten.
#
# Either way it ends by writing FAIRNESS_GATE_PASS, which is what the chain's
# stage 04 and 05 are waiting on. Safety stops are never overridden.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
EVAL="$ROOT/exp/results/final-evaluation"
WL="$ROOT/exp/workloads/final-evaluation"
PY=/workspace/prism-exp/prism-venv/bin/python
cd "$ROOT"

log() { echo "[$(date -u +%FT%TZ)] [fairness] $*" | tee -a "$EVAL/fairness_orchestrator.log"; }

# 1. wait for tau to be frozen (or for the chain to stop)
while true; do
  [ -f "$EVAL/STOP" ] && { log "STOP in force; standing down"; exit 1; }
  [ -f "$EVAL/02-tau-calibration/FROZEN_TAU.json" ] && break
  if [ -f "$EVAL/02-tau-calibration/TAU_REQUIRES_HUMAN_APPROVAL.json" ]; then
    log "tau needs human approval; standing down"; exit 1
  fi
  sleep 60
done
log "tau frozen; auditing prototype workload provenance"

# 2. audit
$PY "$SCRIPT_DIR/final_fairness_audit.py" \
  --final-workloads "$WL" \
  --out-json "$EVAL/FAIRNESS_MANIFEST.json" \
  --out-csv "$EVAL/FAIRNESS_MANIFEST.csv" >> "$EVAL/fairness_orchestrator.log" 2>&1
verdict=$($PY -c "import json;print(json.load(open('$EVAL/FAIRNESS_MANIFEST.json'))['verdict'])" 2>/dev/null || echo FAIL)
log "audit verdict: $verdict"

if [ "$verdict" = "PASS" ]; then
  $PY - "$EVAL" <<'PY'
import json, sys, datetime
from pathlib import Path
e = Path(sys.argv[1])
json.dump({"case": "A", "decided_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "action": "reuse the 22 surviving prototype runs; correct the 2 that "
                     "failed on the 384 MiB workspace, on the same workloads",
           "prototype_runs_reused": 22, "prototype_runs_rerun": 2},
          open(e / "FAIRNESS_DECISION.json", "w"), indent=2)
PY
  log "CASE A: reusing the prototype arm; the chain's 2 correction runs stand"
  touch "$EVAL/FAIRNESS_GATE_PASS"
  exit 0
fi

# 3. CASE B -- the old prototype arm cannot be used in the paired comparison
log "CASE B: prototype provenance not verifiable; re-running the prototype arm in full"
$PY - "$EVAL" "$WL" <<'PY'
import hashlib, json, sys, datetime
from pathlib import Path
e, wl = Path(sys.argv[1]), Path(sys.argv[2])
grid = ([("bursty", r, s) for r in (2, 4, 8, 14, 20) for s in (1, 2, 3)]
        + [("steady", r, s) for r in (4, 8, 20) for s in (1, 2, 3)])
def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()
canon = {f"{w}_r{r}_s{s}": sha(wl / f"{w}_r{r}_s{s}.pkl") for w, r, s in grid}
json.dump({"case": "B",
           "decided_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "reason": "prototype workload provenance could not be verified for "
                     "every cell; its .pkl traces are gone and its request "
                     "records lack arrival time, prompt length and per-request "
                     "SLO, so identity cannot be established without guessing",
           "action": "freeze the committed ShareGPT workloads as the single "
                     "canonical set and run the prototype arm fresh on them; "
                     "the historical prototype results are preserved and are "
                     "not used in the paired comparison",
           "canonical_workload_sha256": canon,
           "historical_prototype_results": "exp/results/final-prototype-vs-paper-faithful/raw/armA (preserved, unused)"},
          open(e / "FAIRNESS_DECISION.json", "w"), indent=2)
json.dump(canon, open(e / "CANONICAL_WORKLOAD_SHA256.json", "w"), indent=2, sort_keys=True)
print(f"canonical set frozen: {len(canon)} workloads")
PY

ok=1
for spec in "bursty 2" "bursty 4" "bursty 8" "bursty 14" "bursty 20" \
            "steady 4" "steady 8" "steady 20"; do
  set -- $spec; wlkind=$1; rate=$2
  for seed in 1 2 3; do
    d="$EVAL/04b-prototype-fresh/raw/${wlkind}/rate_${rate}/seed_${seed}"
    log "prototype fresh $wlkind r$rate s$seed"
    FAIRNESS_WAIT_LIMIT=1 STAGE_HARD_LIMIT=2400 \
    PRISM_FAIRNESS_BYPASS=1 \
    bash "$SCRIPT_DIR/final_stage.sh" "$d" "protofresh-${wlkind}-r${rate}-s${seed}" \
      v4-released-prototype-${wlkind}-r${rate}-s${seed} -- \
      env PRISM_ROOT=/workspace/prism-exp PRISM_REPO="$ROOT/prism-research" \
          PRISM_EXP="$ROOT/exp" BENCHMARK_TIMEOUT=1500 \
          bash "$SCRIPT_DIR/run_v4_case.sh" released-prototype "$wlkind" "$rate" "$seed" \
            "$WL/${wlkind}_r${rate}_s${seed}.pkl" "$d" \
      >> "$EVAL/fairness_orchestrator.log" 2>&1 || ok=0
    [ "$ok" = "1" ] || break 2
  done
done

if [ "$ok" != "1" ]; then
  echo "a fresh prototype run failed; see 04b-prototype-fresh" > "$EVAL/STOP"
  log "STOP: a fresh prototype run failed"
  exit 1
fi

$PY - "$EVAL" <<'PY'
import json, sys
from pathlib import Path
e = Path(sys.argv[1])
m = json.load(open(e / "FAIRNESS_MANIFEST.json"))
m["verdict"] = "PASS"
m["resolution"] = ("CASE B: the prototype arm was re-run in full on the "
                   "canonical ShareGPT workloads, so both arms now run the "
                   "identical files; the historical prototype results are "
                   "preserved and excluded from the paired comparison")
m["paired_comparison_prototype_source"] = "exp/results/final-evaluation/04b-prototype-fresh/raw"
json.dump(m, open(e / "FAIRNESS_MANIFEST.json", "w"), indent=2, default=str)
PY
log "CASE B complete: 24 fresh prototype runs on the canonical workloads"
touch "$EVAL/FAIRNESS_GATE_PASS"
