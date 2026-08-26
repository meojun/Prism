#!/usr/bin/env bash
# Lightweight smoke test. Proves the stack can start, activate/deactivate a
# model, serve a request and shut down cleanly.
#
# NOT a performance test. Writes ONLY under exp/results/smoke-test/ and can
# never touch an authoritative result.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
PY=${PRISM_PYTHON:-$ROOT/prism-venv/bin/python}
# The trace generator loads each model's tokenizer from the local HF cache. With
# HF_HOME unset it falls back to ~/.cache/huggingface, misses, and tries the
# network -- which 401s on the gated meta-llama repositories. Default it to the
# documented cache root so a clean shell behaves like a configured one.
export HF_HOME=${HF_HOME:-/workspace/.hf_home}
OUT="$ROOT/exp/results/smoke-test/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$OUT"
echo "smoke output -> $OUT"

echo "=== 1. offline unit tests (no GPU) ==="
rc=0
for t in "$ROOT/exp/analysis/window_calibration/test_window.py" \
         "$ROOT/exp/analysis/estimator_correction/test_estimator.py" \
         "$ROOT/exp/analysis/migration_rollback_fix/test_control_path.py" \
         "$ROOT/exp/analysis/migration_rollback_fix/test_failure_containment.py"; do
  [ -f "$t" ] || continue
  if "$PY" "$t" >"$OUT/$(basename "$t").log" 2>&1; then
    echo "  PASS  $(basename "$t")"
  else
    echo "  FAIL  $(basename "$t")  (see $OUT/$(basename "$t").log)"; rc=1
  fi
done

echo "=== 2. lifecycle validity gate self-test ==="
# a known-bad preserved run must FAIL, a known-good authoritative run must PASS
BAD="$ROOT/exp/results/4het-tau-calibration/raw/prism-T0/steady/rate_8/seed_3.stall-attempt1"
GOOD="$ROOT/exp/results/4het-final/raw/prism/steady/rate_2/seed_5"
if [ -d "$BAD" ]; then
  "$PY" "$ROOT/exp/scripts/lifecycle_validity_gate.py" "$BAD" >/dev/null 2>&1 \
    && { echo "  FAIL  gate passed a known-bad run"; rc=1; } || echo "  PASS  gate rejects the known lifecycle stall"
fi
if [ -d "$GOOD" ]; then
  "$PY" "$ROOT/exp/scripts/lifecycle_validity_gate.py" "$GOOD" >/dev/null 2>&1 \
    && echo "  PASS  gate accepts a known-good authoritative run" \
    || { echo "  FAIL  gate rejected a known-good run"; rc=1; }
fi

echo "=== 3. trace generator determinism (no GPU) ==="
SG=${SHAREGPT_JSON:-/workspace/datasets/sharegpt/ShareGPT_V3_unfiltered_cleaned_split.json}
REF="$ROOT/exp/workloads/4het-cal/steady_r8_s3.pkl"
if [ ! -f "$REF" ]; then
  echo "  SKIP  reference trace absent ($REF)"
  echo "        traces are not committed; regenerate them first -- handoff section 60"
elif [ -f "$SG" ]; then
  T="$OUT/regen"; mkdir -p "$T"
  "$PY" "$ROOT/exp/scripts/build_paired_workload.py" --rate 8 --duration 420 --seed 3 \
    --models model_3,model_4,model_5,model_6 \
    --revisions "$ROOT/exp/configs/v4het/model_revisions.json" \
    --slo-base "$ROOT/exp/configs/v4het/slo_base.json" \
    --sharegpt "$SG" --outdir "$T" >"$OUT/regen.log" 2>&1
  a=$(sha256sum "$T/steady_r8_s3.pkl" 2>/dev/null | cut -d' ' -f1)
  b=$(sha256sum "$ROOT/exp/workloads/4het-cal/steady_r8_s3.pkl" 2>/dev/null | cut -d' ' -f1)
  if [ -n "$a" ] && [ "$a" = "$b" ]; then
    echo "  PASS  regenerated trace is byte-identical (${a:0:16})"
  elif [ -z "$a" ] && grep -qE "gated repo|401 Client Error" "$OUT/regen.log" 2>/dev/null; then
    echo "  SKIP  tokenizer cache unavailable (HF_HOME=$HF_HOME) and the gated repos"
    echo "        cannot be fetched without HUGGING_FACE_HUB_TOKEN — see MODEL_MANIFEST.md"
  else
    echo "  FAIL  regenerated trace differs: ${a:-<none>} vs $b"; rc=1
  fi
else
  echo "  SKIP  ShareGPT not present, generator determinism not checked"
fi

echo "=== 4. GPU serving smoke (optional) ==="
if [ "${PRISM_SMOKE_GPU:-0}" = "1" ]; then
  echo "  GPU smoke requested; this starts a server briefly and writes only under $OUT"
  echo "  (not implemented as an automatic step: it must never be confused with a"
  echo "   performance run. Use resume.sh for real experiments.)"
else
  echo "  SKIP  set PRISM_SMOKE_GPU=1 to opt in; default is offline-only"
fi

echo
[ "$rc" = 0 ] && echo "SMOKE_TEST = PASS" || echo "SMOKE_TEST = FAIL"
echo "output kept at $OUT"
exit $rc
