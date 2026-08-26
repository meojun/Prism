#!/usr/bin/env bash
# Validate frozen manifests, trace hashes and the authoritative result set.
# Read-only. Exits 0 = PASS.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
PY=${PRISM_PYTHON:-$ROOT/prism-venv/bin/python}
MAN="$ROOT/exp/manifests/prism_final"
echo "=== frozen configuration ==="
"$PY" - "$MAN" <<'PYEOF'
import json, sys, hashlib
from pathlib import Path
M=Path(sys.argv[1]); bad=0
fz=json.load(open(M/"FINAL_BASELINE_FROZEN.json"))
want=(M/"FINAL_BASELINE_FROZEN.sha256").read_text().strip()
got=hashlib.sha256((M/"FINAL_BASELINE_FROZEN.json").read_bytes()).hexdigest()
print(("  PASS  " if got==want else "  FAIL  ")+f"FINAL_BASELINE_FROZEN.json sha256 {got[:16]}")
bad += got!=want
c=fz["config"]
for k,v in (("FINAL_TAU",0.012859417696566448),("KVPR_WINDOW",60),("COOLDOWN",30)):
    okk = c[k]==v
    print(("  PASS  " if okk else "  FAIL  ")+f"{k} = {c[k]}")
    bad += not okk
print(("  PASS  " if fz["PERFORMANCE_EVALUATION_CLOSED"] else "  FAIL  ")+"PERFORMANCE_EVALUATION_CLOSED")
sys.exit(1 if bad else 0)
PYEOF
rc1=$?
echo "=== trace hashes ==="
"$PY" - "$ROOT" <<'PYEOF'
import json, sys, hashlib
from pathlib import Path
R=Path(sys.argv[1]); bad=0; n=0; unprov=False
SETS=[("tau",R/"exp/manifests/prism_final/TAU_CALIBRATION_TRACE_MANIFEST.json",R/"exp/workloads/4het-cal"),
      ("many_model",R/"exp/manifests/prism_final/MANY_MODEL_TRACE_MANIFEST.json",R/"exp/workloads/many_model_pf"),
      ("final_4het",R/"exp/manifests/prism_final/FINAL_4HET_TRACE_MANIFEST.json",R/"exp/workloads/4het-final")]
for name,man,wl in SETS:
    files=json.load(open(man))["files"]; absent=0; mism=0
    for fn,meta in files.items():
        q=wl/fn; n+=1
        if not q.exists(): absent+=1; continue
        h=hashlib.sha256()
        with open(q,"rb") as f:
            for b in iter(lambda: f.read(1<<20), b""): h.update(b)
        if h.hexdigest()!=meta["sha256"]: mism+=1
    okc=len(files)-absent-mism
    if mism:
        print(f"  FAIL  {name}: {mism} trace(s) DIFFER from the frozen manifest"); bad+=1
    elif absent==len(files):
        print(f"  ABSENT {name}: 0/{len(files)} traces present -- not provisioned yet, regenerate")
        globals()["unprov"]=True
    elif absent:
        print(f"  PARTIAL {name}: {okc}/{len(files)} present and matching, {absent} absent")
        globals()["unprov"]=True
    else:
        print(f"  PASS  {name}: {okc}/{len(files)} traces match the frozen manifest")
print(f"        {n} trace files checked")
sys.exit(1 if bad else (2 if unprov else 0))
PYEOF
rc2=$?
if [ "$rc2" = 2 ]; then
  echo
  echo "ARTIFACT_VERIFICATION = NOT_PROVISIONED"
  echo "  Traces are not committed. Regenerate them, then re-run this script:"
  echo "    see reports/prism/11_handoff/PRISM_SERVER_HANDOFF.md section 60"
  exit 2
fi
echo "=== authoritative results ==="
PRISM_PYTHON="$PY" "$PY" "$ROOT/exp/analysis/final_summary/audit.py" 2>&1 | sed 's/^/  /'
rc3=${PIPESTATUS[0]}
echo
if [ "$rc1" = 0 ] && [ "$rc2" = 0 ] && [ "$rc3" = 0 ]; then
  echo "ARTIFACT_VERIFICATION = PASS"; exit 0
else
  echo "ARTIFACT_VERIFICATION = FAIL"; exit 1
fi
