#!/bin/bash
# Prepare a new GPU server to continue the Prism baseline.
#
#   git clone <repo> prism-exp && cd prism-exp
#   git checkout <HANDOFF_SHA>
#   bash exp/scripts/bootstrap_final_baseline.sh
#
# Additive by design. It checks what is already there and creates only what is
# missing; it does not upgrade system packages or re-resolve the pinned Python
# stack, because re-resolving is what breaks this stack.
#
# Ends by telling you exactly what is still missing, if anything.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
cd "$ROOT"
missing=""
note() { printf '  %-5s %s\n' "$1" "$2"; }
need() { missing="$missing\n  - $1"; }

echo "== 1. environment =="
if [ ! -x "$ROOT/prism-venv/bin/python" ] && [ ! -x /workspace/prism-exp/prism-venv/bin/python ]; then
  note MISS "no pinned virtualenv"
  echo "        run ./bootstrap.sh -- it builds it from setup/pins.env and"
  echo "        setup/requirements.lock.txt, and downloads the six models"
  need "virtualenv: ./bootstrap.sh"
else
  VENV=$([ -x "$ROOT/prism-venv/bin/python" ] && echo "$ROOT/prism-venv" || echo /workspace/prism-exp/prism-venv)
  note OK "virtualenv at $VENV"
  "$VENV/bin/python" - <<'PY' 2>/dev/null || { echo "        the venv exists but does not import the stack"; }
import torch, sglang
print(f"        torch {torch.__version__}, sglang {sglang.__version__}, "
      f"cuda {torch.version.cuda}, gpus {torch.cuda.device_count()}")
PY
fi

echo "== 2. secrets =="
ENVFILE=${PRISM_ENV_FILE:-/workspace/.env}
if [ -r "$ENVFILE" ] && grep -q '^[[:space:]]*HF_TOKEN=' "$ENVFILE"; then
  note OK "$ENVFILE carries HF_TOKEN"
else
  note MISS "$ENVFILE has no HF_TOKEN (the Llama models are gated)"
  echo "        cp .env.example $ENVFILE && chmod 600 $ENVFILE && \$EDITOR $ENVFILE"
  need "HF_TOKEN in $ENVFILE"
fi

echo "== 3. directories =="
for d in exp/results/final-evaluation exp/workloads/final-evaluation; do
  mkdir -p "$ROOT/$d" && note OK "$d"
done

echo "== 4. redis =="
if redis-cli ping 2>/dev/null | grep -q PONG; then
  note OK "redis answers PING"
else
  if command -v redis-server >/dev/null 2>&1; then
    note MISS "redis installed but not running -- start it: redis-server --daemonize yes"
    need "redis running"
  else
    note MISS "redis-server not installed (the GPU scheduler's queues live in it)"
    need "redis-server installed and running"
  fi
fi

echo "== 5. descriptor limit =="
hard=$(ulimit -Hn)
if [ "$hard" = "unlimited" ] || [ "${hard:-0}" -ge 65535 ] 2>/dev/null; then
  note OK "hard nofile = $hard (the client raises its soft limit to 65535)"
else
  note MISS "hard nofile = $hard, below the 65535 the client needs"
  need "raise the hard nofile limit to at least 65535"
fi

echo "== 6. FlashInfer workspace =="
fw=$(bash -c "source '$SCRIPT_DIR/env.sh' >/dev/null 2>&1; echo \$FLASHINFER_WORKSPACE_SIZE")
if [ "${fw:-0}" -ge 1073741824 ] 2>/dev/null; then
  note OK "FLASHINFER_WORKSPACE_SIZE = $fw (1 GiB)"
else
  note MISS "FLASHINFER_WORKSPACE_SIZE = ${fw:-unset}; below 1 GiB the server dies mid-run"
  need "FlashInfer workspace >= 1 GiB (set in exp/scripts/env.sh)"
fi

echo "== 7. shared memory =="
stale=$(ls /dev/shm/ipc_* 2>/dev/null | wc -l)
if [ "$stale" = "0" ]; then note OK "no stale kvcached segments"; else
  note MISS "$stale stale /dev/shm/ipc_* segments from a crashed server"
  echo "        remove them with: rm -f /dev/shm/ipc_*   (only when no server runs)"
  need "clear stale /dev/shm segments"
fi

echo "== 8. c_i profile =="
CI="$ROOT/exp/results/final-evaluation/01-ci-profile/prefill_speed_final_a100.json"
WANT=$(python3 -c "import json;print(json.load(open('$ROOT/exp/final-handoff/calibration_manifest.json'))['c_i_sha256'])" 2>/dev/null)
if [ -f "$CI" ] && [ "$(sha256sum "$CI" | cut -d' ' -f1)" = "$WANT" ]; then
  note OK "c_i matches the profile tau was chosen against"
else
  note MISS "c_i profile absent or changed"
  echo "        it is committed; a checkout restores it. Re-measure ONLY if the"
  echo "        GPUs differ from 2x A100-SXM4-80GB -- re-measuring changes c_i"
  echo "        and therefore changes what tau means."
  need "c_i profile at $CI"
fi

echo "== 9. canonical workloads =="
if python3 "$SCRIPT_DIR/verify_workloads.py" --workloads "$ROOT/exp/workloads/final-evaluation" \
     --manifest "$ROOT/exp/final-handoff/workloads_manifest.json" >/dev/null 2>&1; then
  note OK "all 24 canonical workloads present and matching"
else
  note MISS "workloads absent or not matching"
  echo "        rebuild and verify: bash exp/scripts/restore_workloads.sh"
  need "24 canonical workloads (bash exp/scripts/restore_workloads.sh)"
fi

echo
if [ -z "$missing" ]; then
  echo "bootstrap complete. Next:"
  echo "  source exp/scripts/env.sh"
  echo "  python exp/scripts/handoff_preflight.py"
  echo "  bash exp/scripts/resume_baseline.sh --dry-run"
  exit 0
fi
echo "still missing:"
printf "$missing\n"
echo
echo "fix those, then re-run this script."
exit 1
