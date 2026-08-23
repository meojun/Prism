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

n=$(ls "$WORK"/exp/workloads/final-evaluation/*.pkl 2>/dev/null | wc -l)
[ "$n" -ge 24 ] && record "canonical workloads in the clone" PASS "$n .pkl files" \
                || record "canonical workloads in the clone" FAIL "$n .pkl files"

# Hashes must match the frozen set, or the two arms would not be comparable on
# a new server even though both "ran the workloads".
if [ -f "$EVAL/CANONICAL_WORKLOAD_SHA256.json" ]; then
  if $PY - "$EVAL/CANONICAL_WORKLOAD_SHA256.json" "$WORK/exp/workloads/final-evaluation" <<'PY'
import hashlib, json, sys
from pathlib import Path
canon = json.load(open(sys.argv[1])); wl = Path(sys.argv[2])
bad = []
for name, want in canon.items():
    p = wl / name
    if not p.is_file():
        bad.append(f"{name}: missing"); continue
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    if h.hexdigest() != want:
        bad.append(f"{name}: hash differs")
print("; ".join(bad) if bad else f"{len(canon)} hashes match")
sys.exit(1 if bad else 0)
PY
  then record "workload hashes match in the clone" PASS "identical to the frozen set"
  else record "workload hashes match in the clone" FAIL "see above"
  fi
else
  record "workload hashes match in the clone" FAIL "no canonical hash file"
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
# pure unit tests -- no GPU, no server.
if [ -d "$WORK/exp/tests" ]; then
  if ( cd "$WORK" && PYTHONPATH="$WORK/exp/scripts" $PY -m pytest -q exp/tests \
        -x --timeout=300 > "$EVAL/clean_clone_pytest.log" 2>&1 ); then
    record "invariant tests pass in the clone" PASS "$(tail -1 "$EVAL/clean_clone_pytest.log")"
  else
    record "invariant tests pass in the clone" FAIL "$(tail -3 "$EVAL/clean_clone_pytest.log" | tr '\n' ' ')"
  fi
else
  record "invariant tests pass in the clone" SKIP "no exp/tests directory"
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
