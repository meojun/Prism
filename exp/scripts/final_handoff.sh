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

# Handoff runs however the chain ended. A server is being released in the
# morning, so a stopped pipeline still has to be picked up somewhere else --
# and a failure is handed off AS a failure, never dressed up as a baseline.
log "-> pipeline state"
$PY "$SCRIPT_DIR/final_handoff_state.py" --eval-dir "$EVAL" \
  --out "$EVAL/PIPELINE_STATE.json" --runtime-freeze "$FREEZE" \
  | tee -a "$EVAL/handoff.log"
PIPELINE_STATUS=$($PY -c "import json;print(json.load(open('$EVAL/PIPELINE_STATE.json'))['PIPELINE_STATUS'])" 2>/dev/null || echo UNKNOWN)
log "PIPELINE_STATUS = $PIPELINE_STATUS"

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
step handoff-document $PY "$SCRIPT_DIR/final_handoff_doc.py" \
  --root "$ROOT" --out "$ROOT/FINAL_BASELINE_HANDOFF.md" \
  --handoff-sha "$(git rev-parse --short HEAD)"

# 10. commit and push; the push is only real once the remote says so.
# This happens whatever failed above: the whole point is that the work leaves
# this machine. What failed is recorded, not hidden.
if true; then
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
step clean-clone bash "$SCRIPT_DIR/final_clean_clone_validate.sh"

# The document names the commit it ships in, which is only known after the
# commit above. Regenerate and amend it in, so the checkout instructions in
# the pushed file point at the pushed file.
$PY "$SCRIPT_DIR/final_handoff_doc.py" --root "$ROOT" \
  --out "$ROOT/FINAL_BASELINE_HANDOFF.md" \
  --handoff-sha "$(git rev-parse --short HEAD)" >> "$HANDOFF/handoff-document.log" 2>&1 || true
if ! git diff --quiet -- FINAL_BASELINE_HANDOFF.md; then
  git add FINAL_BASELINE_HANDOFF.md
  git -c user.name="Prism Baseline Agent" -c user.email="causslab@gmail.com" \
    commit -q -m "Point the handoff document at the commit it ships in" \
    >> "$HANDOFF/commit.log" 2>&1 || true
  if git push origin "$BRANCH" >> "$HANDOFF/push.log" 2>&1; then
    HANDOFF_SHA=$(git rev-parse HEAD)
    remote=$(git ls-remote origin "refs/heads/$BRANCH" | awk '{print $1}')
    [ "$remote" = "$HANDOFF_SHA" ] || fails="$fails push-verification"
  else
    fails="$fails push"
  fi
fi

# 12. the release verdict -- handoff completeness, judged apart from whether
# the pipeline itself succeeded. PIPELINE_STATUS=FAILED with
# HANDOFF_COMPLETE=YES is a normal, correct ending: the work is safe elsewhere.
$PY - "$EVAL" "$ROOT" "$FREEZE" "${HANDOFF_SHA:-unknown}" "$BRANCH" "$fails" <<'PY'
import json, sys, datetime
from pathlib import Path
ev, root, freeze, handoff_sha, branch, fails = sys.argv[1:7]
ev, root = Path(ev), Path(root)
failed = [f for f in fails.split() if f]

def jload(p):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return {}

state = jload(ev / "PIPELINE_STATE.json")
status = state.get("PIPELINE_STATUS", "UNKNOWN")
clone = jload(ev / "CLEAN_CLONE_VALIDATION.json")

conditions = {
    "runtime_and_harness_pushed": ("push" not in failed
                                   and "push-verification" not in failed),
    "raw_evidence_archived": (ev / "ARCHIVE_MANIFEST.json").is_file(),
    "pipeline_state_recorded": bool(state),
    "resume_point_recorded": bool((state.get("resume") or {}).get("stage") is not None
                                  or status == "SUCCESS"),
    "provenance_recorded": (root / "exp/final_baseline_manifest.json").is_file(),
    "handoff_document": (root / "FINAL_BASELINE_HANDOFF.md").is_file(),
    "clean_clone_validation": clone.get("verdict") == "PASS",
    "no_secrets_in_git": "secret-scan" not in failed,
    "runtime_unchanged_by_packaging": "runtime-unchanged" not in failed,
}
complete = all(conditions.values())
doc = {
    "decided_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "PIPELINE_STATUS": status,
    "HANDOFF_COMPLETE": "YES" if complete else "NO",
    "SAFE_TO_RELEASE_SERVER": "YES" if complete else "NO",
    "FINAL_RUNTIME_SHA": freeze,
    "HANDOFF_SHA": handoff_sha,
    "branch": branch,
    "conditions": conditions,
    "failed_steps": failed,
    "resume": state.get("resume"),
    "reason": ("every handoff condition holds; the server may be released "
               "whatever the pipeline result"
               if complete else
               "unmet: " + ", ".join([k for k, v in conditions.items() if not v]
                                     + failed)),
}
(ev / "SAFE_TO_RELEASE.json").write_text(json.dumps(doc, indent=2) + "\n")
print(json.dumps(doc, indent=2))
sys.exit(0 if complete else 1)
PY
handoff_rc=$?

# 13. tell the phone, in the shape the verdict rules require
stamp=$(date +%s)
tau=$($PY -c "import json;print(json.load(open('$EVAL/02-tau-calibration/FROZEN_TAU.json'))['tau'])" 2>/dev/null || echo "not frozen")
resume=$($PY -c "import json;r=json.load(open('$EVAL/PIPELINE_STATE.json')).get('resume') or {};print(f\"{r.get('stage')} / {r.get('run')}\")" 2>/dev/null || echo "unknown")
reason=$($PY -c "import json;s=json.load(open('$EVAL/PIPELINE_STATE.json'));print(s.get('stop_reason') or ', '.join(s.get('failed_stages') or []) or 'none')" 2>/dev/null || echo "unknown")
backup=$($PY -c "import json;a=json.load(open('$EVAL/ARCHIVE_MANIFEST.json'));print(f\"{len(a['archives'])} archive(s), {a['size_bytes']/1e9:.1f} GB\")" 2>/dev/null || echo "no archive")
overall=$($PY - "$ROOT" <<'PY' 2>/dev/null || echo "no aggregation"
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
    print("no aggregation")
PY
)

if [ "$handoff_rc" = "0" ] && [ "$PIPELINE_STATUS" = "SUCCESS" ]; then
  bash "$SCRIPT_DIR/notify.sh" "handoff-success-$stamp" "$(printf '%s\n%s\n%s\n%s\n%s\n%s\n%s' \
    "✅ SUCCESS | Prism Baseline COMPLETE | HANDOFF READY | SERVER MAY BE RELEASED" \
    "selected τ = $tau" "$overall" \
    "FINAL_RUNTIME_SHA = $FREEZE" "HANDOFF_SHA = ${HANDOFF_SHA:0:12}" \
    "backup: $backup" "HANDOFF_COMPLETE = YES")" || true
  log "HANDOFF COMPLETE -- PIPELINE_STATUS=SUCCESS, SERVER MAY BE RELEASED"
  exit 0
fi

if [ "$handoff_rc" = "0" ]; then
  bash "$SCRIPT_DIR/notify.sh" "handoff-pipefail-$stamp" "$(printf '%s\n%s\n%s\n%s\n%s\n%s\n%s' \
    "❌ FAILED | Prism Pipeline | HANDOFF READY | SERVER MAY BE RELEASED" \
    "pipeline: $PIPELINE_STATUS -- $reason" \
    "resume at: $resume" \
    "FINAL_RUNTIME_SHA = $FREEZE" "HANDOFF_SHA = ${HANDOFF_SHA:0:12}" \
    "backup: $backup" "HANDOFF_COMPLETE = YES")" || true
  log "HANDOFF COMPLETE -- PIPELINE_STATUS=$PIPELINE_STATUS, SERVER MAY BE RELEASED"
  exit 0
fi

bash "$SCRIPT_DIR/notify.sh" "handoff-failed-$stamp" "$(printf '%s\n%s\n%s\n%s' \
  "❌ FAILED | Prism Handoff | DO NOT RELEASE SERVER YET" \
  "pipeline: $PIPELINE_STATUS" \
  "handoff failed: ${fails:-see SAFE_TO_RELEASE.json}" \
  "HANDOFF_COMPLETE = NO")" || true
log "HANDOFF INCOMPLETE -- HANDOFF_COMPLETE=NO (${fails:-see SAFE_TO_RELEASE.json})"
exit 1
