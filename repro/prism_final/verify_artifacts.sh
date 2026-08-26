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
R=Path(sys.argv[1]); bad=0; n=0
SETS=[("tau",R/"exp/results/4het-window-calibration/CALIBRATION_TRACE_MANIFEST.json",R/"exp/workloads/4het-cal"),
      ("many_model",R/"exp/manifests/prism_final/MANY_MODEL_TRACE_MANIFEST.json",R/"exp/workloads/many_model_pf"),
      ("final_4het",R/"exp/manifests/prism_final/FINAL_4HET_TRACE_MANIFEST.json",R/"exp/workloads/4het-final")]
for name,man,wl in SETS:
    files=json.load(open(man))["files"]; miss=0
    for fn,meta in files.items():
        p=wl/fn; n+=1
        if not p.exists(): miss+=1; bad+=1; continue
        h=hashlib.sha256(); 
        with open(p,"rb") as f:
            for b in iter(lambda: f.read(1<<20), b""): h.update(b)
        if h.hexdigest()!=meta["sha256"]: miss+=1; bad+=1
    print(("  PASS  " if miss==0 else "  FAIL  ")+f"{name}: {len(files)-miss}/{len(files)} traces match the frozen manifest")
print(f"        {n} trace files checked")
sys.exit(1 if bad else 0)
PYEOF
rc2=$?
echo "=== authoritative results ==="
"$PY" "$ROOT/exp/analysis/final_summary/audit.py" 2>&1 | sed 's/^/  /'
rc3=${PIPESTATUS[0]}
echo
if [ "$rc1" = 0 ] && [ "$rc2" = 0 ] && [ "$rc3" = 0 ]; then
  echo "ARTIFACT_VERIFICATION = PASS"; exit 0
else
  echo "ARTIFACT_VERIFICATION = FAIL"; exit 1
fi
