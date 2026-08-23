#!/bin/bash
# Record the environment the evaluation actually ran in, and say where it has
# drifted from the lockfile.
#
# The point is not to replace setup/requirements.lock.txt with a pip freeze --
# re-resolving this stack is what breaks it. The point is to catch the packages
# that were installed by hand during the work and would otherwise be missing on
# the next server, and to record the system-level versions the wheels assume.
#
#   final_env_capture.sh <out.json>
set -uo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
OUT=${1:-$ROOT/exp/HANDOFF_ENVIRONMENT.json}
VENV=${PRISM_VENV:-/workspace/prism-exp/prism-venv}
PY="$VENV/bin/python"
cd "$ROOT"

[ -x "$PY" ] || { echo "FATAL: no interpreter at $PY" >&2; exit 1; }

FREEZE_TXT=$(mktemp)
"$VENV/bin/pip" freeze --disable-pip-version-check 2>/dev/null | sort > "$FREEZE_TXT"

"$PY" - "$OUT" "$ROOT" "$FREEZE_TXT" "$VENV" <<'PY'
import json, os, platform, re, subprocess, sys
from pathlib import Path

out, root, freeze_txt, venv = sys.argv[1:5]
root = Path(root)

def sh(cmd):
    r = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True)
    return r.stdout.strip()

installed = {}
for line in Path(freeze_txt).read_text().splitlines():
    if "==" in line:
        name, ver = line.split("==", 1)
        installed[name.strip().lower().replace("_", "-")] = ver.strip()
    elif line.strip():
        installed[line.strip()] = None      # editable / VCS install

locked = {}
lock = root / "setup/requirements.lock.txt"
if lock.is_file():
    for line in lock.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            name, ver = line.split("==", 1)
            locked[name.strip().lower().replace("_", "-")] = ver.strip()

extra = {k: v for k, v in installed.items() if k not in locked}
missing = {k: v for k, v in locked.items() if k not in installed}
mismatch = {k: {"locked": locked[k], "installed": installed[k]}
            for k in locked if k in installed and installed[k] != locked[k]}

versions = {}
for mod in ("torch", "sglang", "transformers", "vllm", "flashinfer", "kvcached",
            "redis", "aiohttp", "numpy"):
    versions[mod] = sh(f"'{venv}/bin/python' -c \"import {mod};"
                       f"print(getattr({mod},'__version__','unknown'))\" 2>/dev/null")
cuda = sh(f"'{venv}/bin/python' -c \"import torch;print(torch.version.cuda)\" 2>/dev/null")

doc = {
    "captured_utc": __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).isoformat(),
    "note": ("setup/requirements.lock.txt remains the source of truth for "
             "rebuilding this stack; the lists below say where this machine "
             "drifted from it, which is what a new server needs to know"),
    "python": platform.python_version(),
    "python_executable": f"{venv}/bin/python",
    "platform": platform.platform(),
    "torch_cuda": cuda,
    "driver": sh("nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1"),
    "nvcc": sh("nvcc --version 2>/dev/null | tail -2 | head -1"),
    "gpus": sh("nvidia-smi --query-gpu=index,name,memory.total,compute_cap "
               "--format=csv,noheader").splitlines(),
    "key_module_versions": versions,
    "system_packages": {p: sh(f"dpkg -s {p} 2>/dev/null | sed -n 's/^Version: //p'")
                        for p in ("libnuma1", "build-essential", "redis-server",
                                  "tmux", "zstd", "git")},
    "installed_package_count": len(installed),
    "locked_package_count": len(locked),
    "installed_but_not_locked": extra,
    "locked_but_not_installed": missing,
    "version_mismatch_against_lockfile": mismatch,
    "editable_or_vcs_installs": [k for k, v in installed.items() if v is None],
}
Path(out).write_text(json.dumps(doc, indent=2, sort_keys=False) + "\n")
print(f"wrote {out}")
print(f"  {len(installed)} installed, {len(locked)} locked")
print(f"  not in the lockfile: {len(extra)}")
print(f"  in the lockfile but absent: {len(missing)}")
print(f"  version mismatches: {len(mismatch)}")
if extra:
    print("  packages a new server would not get from the lockfile alone:")
    for k, v in sorted(extra.items())[:40]:
        print(f"    {k}=={v}")
PY
rc=$?
rm -f "$FREEZE_TXT"
exit $rc
