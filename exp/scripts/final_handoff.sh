#!/bin/bash
# Stage 7: package the successful baseline so another server can reproduce it.
#
# Runs only after the evaluation has passed. It changes no runtime algorithm or
# policy -- the runtime is already frozen and its patch blob is checked here to
# prove it did not move. What this produces is the manifest, the audit of what
# git does not carry, the archive of the raw evidence, the results index, the
# handoff document, and a verified push.
#
# Everything here is heavy (multi-GB reads, a clean clone, the unit suite), so
# it refuses to run while a server is up.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
EVAL="$ROOT/exp/results/final-evaluation"
HANDOFF="$EVAL/07-handoff"
PY=${PRISM_PY:-/workspace/prism-exp/prism-venv/bin/python}
FREEZE=${PRISM_RUNTIME_FREEZE:-6618671}
BRANCH=$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)
mkdir -p "$HANDOFF"
cd "$ROOT"

log() { echo "[$(date -u +%FT%TZ)] [handoff] $*" | tee -a "$EVAL/handoff.log"; }
fails=""
step() {  # step <name> <command...>
  local name=$1; shift
  log "-> $name"
  if "$@" >> "$HANDOFF/${name}.log" 2>&1; then
    log "   $name PASS"; return 0
  fi
  log "   $name FAIL (see $HANDOFF/${name}.log)"
  fails="$fails $name"; return 1
}

if pgrep -f "sglang.launch_multi_model_server" >/dev/null 2>&1; then
  log "FATAL: a server is still running; handoff work must not compete with it"
  exit 1
fi

# The evaluation must actually have succeeded. Handoff is packaging, not rescue.
if ! grep -q '"result": "PASS"' "$EVAL/06-aggregate/STATUS.json" 2>/dev/null; then
  log "FATAL: 06-aggregate did not pass; there is nothing to hand off"
  exit 1
fi

# 1. the runtime must be the one that was evaluated, byte for byte
frozen=$(git rev-parse "$FREEZE:patches/final_baseline_ready/prism_research_worktree.patch" 2>/dev/null)
now=$(git hash-object patches/final_baseline_ready/prism_research_worktree.patch 2>/dev/null)
if [ "$frozen" != "$now" ]; then
  log "FATAL: the runtime patch moved since the freeze ($frozen != $now)"
  fails="$fails runtime-unchanged"
else
  log "runtime unchanged since $FREEZE (patch blob $now)"
fi

# 2. what the evaluation actually used
step manifest $PY "$SCRIPT_DIR/final_handoff_manifest.py" \
  --root "$ROOT" --out "$ROOT/exp/final_baseline_manifest.json" \
  --runtime-freeze "$FREEZE"

# 3. what this machine provides that git does not
step dependency-audit $PY "$SCRIPT_DIR/final_local_dependency_audit.py" \
  --root "$ROOT" --out "$HANDOFF/LOCAL_DEPENDENCIES.json" \
  --report "$ROOT/exp/HANDOFF_LOCAL_DEPENDENCIES.md"

# 4. the environment, from the environment
step environment-capture bash "$SCRIPT_DIR/final_env_capture.sh" \
  "$ROOT/exp/HANDOFF_ENVIRONMENT.json"

# 5. the raw evidence, off the repo but hashed
step archive bash "$SCRIPT_DIR/final_archive_raw.sh"

# 6. one index over everything
step results-index $PY "$SCRIPT_DIR/final_results_index.py" \
  --root "$ROOT" --out "$ROOT/FINAL_RESULTS_INDEX.md"

# 7. nothing secret may be about to leave the machine
step secret-scan bash "$SCRIPT_DIR/final_secret_scan.sh" "$HANDOFF/SECRET_SCAN.txt"

# 8. large-file audit -- a remote will refuse a 100 MB blob, and an overnight
#    chain that discovers that at push time has already lost the evidence.
log "-> large-file audit"
git add -A >/dev/null 2>&1
big=$(git diff --cached --name-only -z | xargs -0 -I{} sh -c \
      '[ -f "{}" ] && s=$(stat -c %s "{}") && [ "$s" -gt 52428800 ] && echo "$s {}"' \
      2>/dev/null | sort -rn)
if [ -n "$big" ]; then
  log "   large-file audit FAIL: staged files over 50 MB"
  echo "$big" | tee "$HANDOFF/LARGE_FILES.txt" | head -5 | while read -r l; do log "     $l"; done
  fails="$fails large-file-audit"
else
  log "   large-file audit PASS: nothing staged over 50 MB"
fi

# 9. the document a person reads first
if [ ! -f "$ROOT/FINAL_BASELINE_HANDOFF.md" ]; then
  log "FATAL: FINAL_BASELINE_HANDOFF.md is missing"
  fails="$fails handoff-document"
fi

# 10. commit and push; the push is only real once the remote says so
if [ -z "$fails" ]; then
  git add -A >/dev/null 2>&1
  if git diff --cached --quiet; then
    log "nothing new to commit; the tree already matches"
  else
    git -c user.name="Prism Baseline Agent" -c user.email="causslab@gmail.com" \
      commit -q -m "Hand the final Prism baseline off to the next server

The manifest, the audit of what this machine provides that git does not, the
environment capture, the results index and the handoff document -- generated
from what the evaluation actually used, so another server can reproduce it
from this commit alone. No runtime algorithm or policy changed here." \
      >> "$HANDOFF/commit.log" 2>&1 || { fails="$fails commit"; log "commit FAILED"; }
  fi
  HANDOFF_SHA=$(git rev-parse HEAD)
  if git push origin "$BRANCH" >> "$HANDOFF/push.log" 2>&1; then
    remote=$(git ls-remote origin "refs/heads/$BRANCH" | awk '{print $1}')
    if [ "$remote" = "$HANDOFF_SHA" ]; then
      log "push verified: remote $BRANCH is $remote"
    else
      log "push FAILED verification: remote=$remote local=$HANDOFF_SHA"
      fails="$fails push-verification"
    fi
  else
    log "push FAILED"; fails="$fails push"
  fi
else
  HANDOFF_SHA=$(git rev-parse HEAD)
  log "skipping the push: earlier steps failed ($fails)"
fi

# 11. does it still assemble without this machine's untracked files?
if [ -z "$fails" ]; then
  step clean-clone bash "$SCRIPT_DIR/final_clean_clone_validate.sh"
fi

# 12. the release verdict
$PY - "$EVAL" "$FREEZE" "${HANDOFF_SHA:-unknown}" "$BRANCH" "$fails" <<'PY'
import json, sys, datetime
from pathlib import Path
ev, freeze, handoff_sha, branch, fails = sys.argv[1:6]
ev = Path(ev)
failed = [f for f in fails.split() if f]
def ok(p, key="verdict", want="PASS"):
    try:
        return json.loads((ev / p).read_text()).get(key) == want
    except Exception:
        return False
conditions = {
    "pipeline_success": ok("06-aggregate/STATUS.json", "result"),
    "aggregation_complete": (lambda m: bool(m) and m.get("final_complete")
                             and m.get("prototype_complete"))(
        json.loads((ev / "06-aggregate/AGGREGATION_MANIFEST.json").read_text())
        if (ev / "06-aggregate/AGGREGATION_MANIFEST.json").is_file() else {}),
    "artifacts_archived": (ev / "ARCHIVE_MANIFEST.json").is_file(),
    "manifest_generated": (ev.parents[2] / "exp/final_baseline_manifest.json").is_file(),
    "handoff_document": (ev.parents[2] / "FINAL_BASELINE_HANDOFF.md").is_file(),
    "clean_clone_validation": ok("CLEAN_CLONE_VALIDATION.json"),
    "github_push_verified": "push" not in failed and "push-verification" not in failed,
    "no_secrets_in_git": "secret-scan" not in failed,
}
safe = all(conditions.values()) and not failed
doc = {"decided_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
       "FINAL_RUNTIME_SHA": freeze,
       "HANDOFF_SHA": handoff_sha,
       "branch": branch,
       "runtime_identical_to_final": "runtime-unchanged" not in failed,
       "conditions": conditions,
       "failed_steps": failed,
       "SAFE_TO_RELEASE_SERVER": "YES" if safe else "NO",
       "reason": ("all handoff conditions hold" if safe else
                  "unmet: " + ", ".join(
                      [k for k, v in conditions.items() if not v] + failed))}
(ev / "SAFE_TO_RELEASE.json").write_text(json.dumps(doc, indent=2) + "\n")
print(json.dumps(doc, indent=2))
sys.exit(0 if safe else 1)
PY
safe_rc=$?

# 13. tell the phone, in the shape the verdict rules require
stamp=$(date +%s)
if [ "$safe_rc" = "0" ]; then
  tau=$($PY -c "import json;print(json.load(open('$EVAL/02-tau-calibration/FROZEN_TAU.json'))['tau'])" 2>/dev/null || echo "?")
  head=$($PY - "$ROOT" <<'PY' 2>/dev/null || echo "aggregation summary unavailable"
import csv, sys
from pathlib import Path
p = Path(sys.argv[1]) / "exp/results/final-evaluation/06-aggregate/group_improvement.csv"
rows = list(csv.DictReader(open(p))) if p.is_file() else []
for r in rows:
    if r.get("scope") == "overall":
        rel = float(r.get("relative_goodput_req_s") or 0) * 100
        print(f"Joint-SLO goodput {float(r['prototype_goodput_req_s']):.3g} -> "
              f"{float(r['final_goodput_req_s']):.3g} req/s ({rel:+.1f}%)")
        break
else:
    print("overall row not found")
PY
)
  bash "$SCRIPT_DIR/notify.sh" "handoff-complete-$stamp" "$(printf '%s\n%s\n%s\n%s\n%s\n%s\n%s' \
    "✅ SUCCESS | Prism Baseline COMPLETE | GitHub PUSHED | SAFE TO RELEASE SERVER" \
    "selected τ = $tau" \
    "$head" \
    "FINAL_RUNTIME_SHA = $FREEZE" \
    "HANDOFF_SHA = ${HANDOFF_SHA:0:12}" \
    "GitHub push verified against the remote ref" \
    "artifacts archived and hashed; SAFE TO RELEASE SERVER = YES")" || true
  log "HANDOFF COMPLETE -- SAFE TO RELEASE SERVER = YES"
  exit 0
fi

bash "$SCRIPT_DIR/notify.sh" "handoff-failed-$stamp" "$(printf '%s\n%s\n%s' \
  "❌ FAILED | Final Handoff | SERVER NOT SAFE TO RELEASE" \
  "failed: ${fails:-see SAFE_TO_RELEASE.json}" \
  "the evaluation itself is unaffected; its results stand")" || true
log "HANDOFF INCOMPLETE -- SAFE TO RELEASE SERVER = NO (${fails:-see SAFE_TO_RELEASE.json})"
exit 1
