#!/bin/bash
# Prove the handoff works from a clean checkout, not from this worktree.
#
# The question being answered is narrow and important: does the baseline still
# assemble if none of this machine's dirty or untracked files exist? A fresh
# clone of the pushed commit is made in a scratch directory, and the parts of
# the setup that do not need a GPU or a download are exercised against it.
#
# It does NOT re-run the evaluation and does NOT reinstall the environment --
# it reuses the venv and caches that already exist, because what is under test
# is the repository's completeness, not the machine's.
#
# Heavy enough to matter: run it only when no benchmark is running.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
EVAL="$ROOT/exp/results/final-evaluation"
PY=${PRISM_PY:-/workspace/prism-exp/prism-venv/bin/python}
WORK=${PRISM_CLONE_DIR:-/workspace/prism-handoff-validate}
OUT="$EVAL/CLEAN_CLONE_VALIDATION.json"
BRANCH=$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)
SHA=$(git -C "$ROOT" rev-parse HEAD)
cd "$ROOT"

if pgrep -f "sglang.launch_multi_model_server" >/dev/null 2>&1; then
  echo "FATAL: a server is running; this validation must not compete with it" >&2
  exit 1
fi

rm -rf "$WORK"
mkdir -p "$(dirname "$WORK")"
results=()
record() { results+=("$1|$2|$3"); echo "  [$2] $1 -- $3"; }

echo "cloning $BRANCH@${SHA:0:7} into $WORK"
if git clone --quiet --branch "$BRANCH" "$ROOT" "$WORK" 2>/dev/null; then
  record "clone from the pushed branch" PASS "$WORK"
else
  record "clone from the pushed branch" FAIL "git clone failed"
fi
git -C "$WORK" checkout --quiet "$SHA" 2>/dev/null \
  && record "checkout the handoff commit" PASS "${SHA:0:12}" \
  || record "checkout the handoff commit" FAIL "$SHA"

# The runtime is stored as a patch against a pinned upstream commit, because
# prism-research/ is deliberately not a subdirectory of this repository. If the
# patch does not apply to that commit, the handoff is broken.
BASE=$(sed -nE 's/^([0-9a-f]{40})$/\1/p' "$WORK/patches/final_baseline_ready/README.md" | head -1)
if [ -n "$BASE" ] && [ -d "$ROOT/prism-research/.git" ]; then
  TREE=$(mktemp -d)
  if git -C "$ROOT/prism-research" worktree add --quiet --detach "$TREE" "$BASE" 2>/dev/null; then
    if git -C "$TREE" apply --check "$WORK/patches/final_baseline_ready/prism_research_worktree.patch" 2>/dev/null; then
      record "runtime patch applies to its base commit" PASS "${BASE:0:12}"
    else
      record "runtime patch applies to its base commit" FAIL "git apply --check rejected it"
    fi
    git -C "$ROOT/prism-research" worktree remove --force "$TREE" 2>/dev/null
  else
    record "runtime patch applies to its base commit" SKIP "base commit $BASE not available locally"
  fi
  rm -rf "$TREE"
else
  record "runtime patch applies to its base commit" FAIL "base commit not recorded in the patch README"
fi

for f in bootstrap.sh setup/pins.env setup/requirements.lock.txt \
         .env.example FINAL_BASELINE_HANDOFF.md exp/final_baseline_manifest.json \
         exp/scripts/env.sh exp/scripts/handoff_preflight.py \
         exp/scripts/final_pipeline.sh exp/scripts/run_v4_case.sh \
         exp/configs/v2/6model_2gpu.json exp/configs/v2/slo_base.json; do
  [ -e "$WORK/$f" ] && record "present: $f" PASS "tracked" \
                    || record "present: $f" FAIL "missing from the clean clone"
done

for f in exp/final-handoff/workloads_manifest.json \
         exp/final-handoff/calibration_manifest.json \
         exp/final-handoff/resume_manifest.json \
         exp/scripts/restore_workloads.sh exp/scripts/verify_workloads.py \
         exp/scripts/bootstrap_final_baseline.sh exp/scripts/resume_baseline.sh \
         exp/FINAL_BASELINE_MANIFEST.json CURRENT_RESULTS_INDEX.md; do
  [ -e "$WORK/$f" ] && record "present: $f" PASS "tracked" \
                    || record "present: $f" FAIL "missing from the clean clone"
done

# The traces themselves are not distributed -- they carry ShareGPT text, some
# of it real leaked credentials. What has to survive is the recipe and the
# digests, and the fact that rebuilding from them reproduces the canonical
# files byte for byte.
n=$($PY -c "import json;print(len(json.load(open('$WORK/exp/final-handoff/workloads_manifest.json'))['files']))" 2>/dev/null || echo 0)
[ "$n" = "24" ] && record "24 workload digests in the clone" PASS "manifest carries all 24" \
                || record "24 workload digests in the clone" FAIL "$n digests"

# Verify the digests describe the traces this machine actually has, using only
# the clone's manifest and verifier.
if $PY "$WORK/exp/scripts/verify_workloads.py" \
     --workloads "$ROOT/exp/workloads/final-evaluation" \
     --manifest "$WORK/exp/final-handoff/workloads_manifest.json" >/dev/null 2>&1; then
  record "the clone's manifest verifies all 24 traces" PASS "24/24 SHA256 match"
else
  record "the clone's manifest verifies all 24 traces" FAIL "see verify_workloads output"
fi

# c_i and tau must be loadable from the clone alone.
if $PY -c "
import json,sys
c=json.load(open('$WORK/exp/final-handoff/calibration_manifest.json'))
assert c['SELECTED_TAU'] is not None, 'no tau'
assert c['runs_passed']==12, f\"calibration {c['runs_passed']}/12\"
p='$WORK/exp/results/final-evaluation/01-ci-profile/prefill_speed_final_a100.json'
d=json.load(open(p)); assert d, 'empty c_i'
print('tau', c['SELECTED_TAU'], 'c_i models', len(d))
" >/dev/null 2>&1; then
  record "tau and c_i load from the clone" PASS "no old-server path needed"
else
  record "tau and c_i load from the clone" FAIL "manifest or c_i not loadable"
fi

# Syntax only -- these must parse on a machine that has never run them.
bad=""
for f in "$WORK"/exp/scripts/*.sh; do bash -n "$f" 2>/dev/null || bad="$bad $(basename "$f")"; done
[ -z "$bad" ] && record "shell harness parses" PASS "all scripts" \
              || record "shell harness parses" FAIL "$bad"
if $PY - "$WORK" <<'PY'
import ast, glob, sys
bad = []
for f in sorted(glob.glob(f"{sys.argv[1]}/exp/scripts/*.py")):
    try:
        ast.parse(open(f).read())
    except SyntaxError as e:
        bad.append(f"{f.split('/')[-1]}:{e.lineno}")
print("; ".join(bad) if bad else "all modules parse")
sys.exit(1 if bad else 0)
PY
then record "python harness parses" PASS "all modules"
else record "python harness parses" FAIL "see above"
fi

# The invariant tests are the ones that encode the correctness fixes. They are
# standalone scripts run as `python <file>`, not a pytest suite -- pytest is not
# in the pinned stack and adding it to make a validation pass would change the
# environment to suit the check. A focused set is enough here: this validates
# the handoff, not the runtime.
FOCUSED="test_staged_return_dict_sampling_params test_staged_request_return \
         test_alg2_dispatch_seq_ownership test_gpu_scoped_backend_queue \
         test_deactivation_rollback_ownership test_alg2_migration_handoff"
bad=""; ran=0
for t in $FOCUSED; do
  f="$WORK/exp/tests/$t.py"
  [ -f "$f" ] || { bad="$bad $t(absent)"; continue; }
  ran=$((ran + 1))
  if ! ( cd "$WORK" && timeout 300 $PY "exp/tests/$t.py" ) \
       > "$EVAL/clean_clone_tests_$t.log" 2>&1; then
    bad="$bad $t"
  fi
done
[ -z "$bad" ] && record "invariant tests pass in the clone" PASS "$ran standalone suites" \
              || record "invariant tests pass in the clone" FAIL "failed:$bad"

# The resume command must work from the clone and name the right next run,
# without touching this machine's results.
dry=$( cd "$WORK" && PRISM_PY=$PY timeout 300 bash exp/scripts/resume_baseline.sh --dry-run 2>&1 )
echo "$dry" > "$EVAL/clean_clone_resume_dryrun.log"
want_next=$($PY -c "import json;m=json.load(open('$WORK/exp/final-handoff/resume_manifest.json'));print(f\"{m['NEXT_STAGE']} / {m['NEXT_RUN']}\")")
if echo "$dry" | grep -q "next = $want_next"; then
  record "resume dry-run names the right next run" PASS "$want_next"
else
  record "resume dry-run names the right next run" FAIL "$(echo "$dry" | tail -3 | tr '\n' ' ')"
fi

$PY - "$OUT" "$SHA" "$BRANCH" "$WORK" "${results[@]}" <<'PY'
import json, sys, datetime
out, sha, branch, work, *rows = sys.argv[1:]
checks = []
for r in rows:
    name, verdict, detail = r.split("|", 2)
    checks.append({"check": name, "verdict": verdict, "detail": detail})
failed = [c["check"] for c in checks if c["verdict"] == "FAIL"]
json.dump({"validated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "commit": sha, "branch": branch, "clone_dir": work,
           "verdict": "PASS" if not failed else "FAIL",
           "failed": failed, "checks": checks},
          open(out, "w"), indent=2)
print(f"\nVERDICT: {'PASS' if not failed else 'FAIL'}  ({out})")
for f in failed:
    print(f"  failed: {f}")
sys.exit(0 if not failed else 1)
PY
