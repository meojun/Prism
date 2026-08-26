#!/usr/bin/env bash
# Resume the experiment pipeline. Requires explicit confirmation before any
# expensive execution. Never overwrites a VALID authoritative condition and
# never deletes a preserved failure.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
PY=${PRISM_PYTHON:-$ROOT/prism-venv/bin/python}
STATE="$ROOT/exp/state/PRISM_FINAL_PIPELINE_STATE.json"
MODE=${1:---status}

show(){ "$PY" - "$STATE" "$ROOT" <<'PYEOF'
import json, sys
from pathlib import Path
s=json.load(open(sys.argv[1])); R=Path(sys.argv[2])
print("pipeline stage :", s.get("current_stage"))
print("FINAL_TAU      :", s.get("FINAL_TAU"))
print("RUN_CODE_COMMIT:", s.get("RUN_CODE_COMMIT"))
print("runtime tree   :", s.get("RUNTIME_SOURCE_TREE_HASH"))
print()
# Count only conditions inside the FROZEN grid. Excluded and diagnostic runs
# live in the same PROGRESS.jsonl and must not inflate completion.
fam=[("tau","exp/results/4het-tau-final",48,{8,10},{3,4}),
     ("many-model","exp/results/many-model-final",20,{2,4,6,8,10},{7,8}),
     ("final 4-HET","exp/results/4het-final",40,{2,4,6,8,10},{5,6})]
pend=0
for name,p,exp,rates,seeds in fam:
    f=R/p/"PROGRESS.jsonl"
    seen=set()
    if f.exists():
        for l in f.read_text().splitlines():
            if not l.strip(): continue
            r=json.loads(l)
            if not r.get("ok"): continue
            if r.get("rate") not in rates or r.get("seed") not in seeds: continue
            seen.add((r.get("arm"),r.get("workload"),r.get("rate"),r.get("seed")))
    ok=len(seen)
    print(f"  {name:<12} {ok:>3}/{exp} valid")
    pend += max(0, exp-ok)
print()
print("PENDING_CONDITIONS =", pend)
PYEOF
}

case "$MODE" in
  --status)      show ;;
  --verify-only) "$HERE/verify_environment.sh" && "$HERE/verify_artifacts.sh" ;;
  --resume|--from-stage)
     show
     echo
     echo "PERFORMANCE_EVALUATION_CLOSED = true in the frozen baseline."
     echo "Resuming will only fill genuinely missing conditions; VALID runs are skipped"
     echo "and preserved failures are never touched."
     read -r -p "Type RESUME to continue: " a
     [ "$a" = "RESUME" ] || { echo "aborted"; exit 1; }
     exec tmux new-session -d -s prism_final_auto \
       "bash $ROOT/exp/scripts/prism_final_orchestrator.sh; echo EXIT=\$? >> $ROOT/exp/state/orchestrator.log"
     ;;
  *) echo "usage: resume.sh [--status|--verify-only|--resume|--from-stage <stage>]"; exit 2 ;;
esac
