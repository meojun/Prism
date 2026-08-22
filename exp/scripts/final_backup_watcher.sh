#!/bin/bash
# Commit and push each stage's artifacts as it passes, and keep an off-repo
# backup. Runs beside the chain; it never touches a benchmark.
#
# Raw benchmark artifacts are large and mostly gitignored, so what is committed
# is the reports, freezes, configs, hashes and provenance -- and a manifest
# recording where the raw artifacts live on this machine.
set -uo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
EVAL="$ROOT/exp/results/final-evaluation"
BACKUP=${PRISM_BACKUP_DIR:-/workspace/prism-backups}
mkdir -p "$BACKUP"
cd "$ROOT"

seen=""
while true; do
  changed=0
  for f in "$EVAL"/*/STATUS.json; do
    [ -f "$f" ] || continue
    grep -q '"result": "PASS"' "$f" || continue
    stage=$(basename "$(dirname "$f")")
    case "$seen" in *"|$stage|"*) continue ;; esac
    seen="$seen|$stage|"
    changed=1
    echo "[backup] $stage passed; recording"
  done

  if [ "$changed" = "1" ]; then
    python3 "$ROOT/exp/scripts/final_raw_manifest.py" \
      --eval-dir "$EVAL" --out "$EVAL/RAW_ARTIFACT_MANIFEST.json" >/dev/null 2>&1
    git add -A -- exp/results/final-evaluation exp/scripts exp/workloads/final-evaluation/WORKLOAD_HASHES.json 2>/dev/null
    if ! git diff --cached --quiet; then
      git -c user.name="Prism Baseline Agent" -c user.email="causslab@gmail.com" \
        commit -q -m "Pipeline artifacts: $(echo "$seen" | tr '|' ' ' | xargs)" || true
    fi
    if ! git push origin exp/final-baseline-ready >/dev/null 2>&1; then
      echo "push failed; the chain stops before the next expensive stage" \
        > "$EVAL/STOP"
      echo "[backup] PUSH FAILED -- STOP written"
    fi
    ts=$(date -u +%Y%m%dT%H%M%SZ)
    tar czf "$BACKUP/final-evaluation-reports-$ts.tar.gz" \
      --exclude='raw' --exclude='*.pkl' --exclude='*.log' \
      exp/results/final-evaluation exp/results/final-baseline-ready 2>/dev/null
    ls -1t "$BACKUP"/final-evaluation-reports-*.tar.gz 2>/dev/null | tail -n +6 | xargs -r rm -f
  fi
  sleep 120
done
