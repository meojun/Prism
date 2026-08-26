#!/bin/bash
# Generic frozen-protocol stage runner.
#
# One arm x one condition list, all parameters supplied by the caller and never
# derived from results. Trace hash is verified against the stage manifest before
# every run, and the lifecycle validity gate is applied after every run.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
PY=/workspace/prism-exp/prism-venv/bin/python

: "${STAGE:?}" "${OUT:?}" "${WL:?}" "${MANIFEST:?}" "${CFG:?}" "${SLO:?}"
: "${CONDS:?}" "${ARMS:?}" "${KVPR_TAU_VAL:?}" "${DURATION_TAG:=}"
KVPR_WINDOW_VAL=${KVPR_WINDOW_VAL:-60}
KVPR_COOLDOWN_VAL=${KVPR_COOLDOWN_VAL:-30}
CI=${CI:-$ROOT/exp/configs/v4het/prefill_speed_4het_a100.json}
SHARED_EVAL="$ROOT/exp/results/final-evaluation"
export PRISM_EVAL_DIR="$OUT"
mkdir -p "$OUT/raw"
log(){ echo "[$(date -u +%FT%TZ)] [$STAGE] $*" | tee -a "$OUT/pipeline.log"; }
stop_all(){ bash "$SCRIPT_DIR/notify.sh" "$STAGE-stop-$(date +%s)" "⛔ STOPPED | $STAGE | $1" || true
            log "STOPPED: $1"; echo "$1" > "$OUT/STOP_REASON"; exit 1; }

[ -f "$SHARED_EVAL/STOP" ] && stop_all "a STOP is in force: $(cat "$SHARED_EVAL/STOP")"
WANT_SRC=$(cat "$ROOT/patches/lifecycle_containment/WORKTREE_PATCH_SHA256")
SNAP=$(mktemp -d); trap 'rm -rf "$SNAP"' EXIT
PRISM_REPO="$ROOT/prism-research" bash "$SCRIPT_DIR/snapshot_source_patch.sh" "$SNAP" verify \
  >>"$OUT/pipeline.log" 2>&1 || stop_all "could not snapshot the runtime source"
GOT_SRC=$(sha256sum "$SNAP/prism_research_worktree.patch" | cut -d\  -f1)
[ "$WANT_SRC" = "$GOT_SRC" ] || stop_all "runtime source drift: $GOT_SRC != $WANT_SRC"
log "runtime verified ${GOT_SRC:0:12} | tau=$KVPR_TAU_VAL window=$KVPR_WINDOW_VAL cooldown=$KVPR_COOLDOWN_VAL"

total=$(( $(echo $ARMS | wc -w) * $(echo $CONDS | wc -w) )); idx=0
for arm in $ARMS; do
  case "$arm" in
    prism)     SYS=paper-faithful-v6; LBL_PREFIX="${STAGE}-prism" ;;
    # final_stage.sh derives the arm from the LABEL prefix and only recognises
    # protofresh-* | proto-* | p4het-proto-*. A label it does not recognise falls
    # through to arm=prism, which then demands that Algorithm 2 ran -- and the
    # released prototype does not implement Algorithm 2 at all. The prefix is
    # that harness's convention, not ours to choose.
    prototype) SYS=released-prototype; LBL_PREFIX="proto-${STAGE}" ;;
    *) stop_all "unknown arm $arm" ;;
  esac
  # Pre-flight: assert the label really does classify as the intended arm,
  # using the exact case statement final_stage.sh applies. Harness validation
  # only -- no runtime semantics are involved.
  probe="${LBL_PREFIX}-probe-r0-s0"
  case "$probe" in protofresh-*|proto-*|p4het-proto-*) derived=prototype ;; *) derived=prism ;; esac
  [ "$derived" = "$arm" ] || stop_all "label prefix '$LBL_PREFIX' classifies as '$derived' but the arm is '$arm'"
  log "arm=$arm system=$SYS label-prefix=$LBL_PREFIX (classifies as $derived)"
  for c in $CONDS; do
    IFS=: read -r kind rate seed <<< "$c"; idx=$((idx+1))
    trace="$WL/${kind}_r${rate}_s${seed}.pkl"
    want=$($PY -c "
import json;m=json.load(open('$MANIFEST'));print(m['files']['${kind}_r${rate}_s${seed}.pkl']['sha256'])")
    got=$(sha256sum "$trace" | cut -d' ' -f1)
    [ "$want" = "$got" ] || stop_all "trace hash mismatch ${kind}_r${rate}_s${seed}"

    d="$OUT/raw/$arm/${kind}/rate_${rate}/seed_${seed}"
    if [ -f "$d/VERIFICATION.json" ] && \
       $PY -c "import json,sys;v=json.load(open('$d/VERIFICATION.json'));sys.exit(0 if int(v['rc'])==0 and v['verdict']=='PASS' else 1)" 2>/dev/null; then
      log "[$idx/$total] $arm $kind r$rate s$seed already VALID -- skipping"; continue
    fi
    log "[$idx/$total] $arm($SYS) $kind r$rate s$seed (trace ${got:0:12} verified)"
    for _ in $(seq 1 30); do pgrep -f "sglang.launch_multi_model_server" >/dev/null 2>&1 || break; sleep 2; done
    pgrep -f "sglang.launch_multi_model_server" >/dev/null 2>&1 || rm -f /dev/shm/ipc_*_root /dev/shm/cuda.shm.* 2>/dev/null || true

    t0=$(date +%s)
    STAGE_HARD_LIMIT=${STAGE_HARD_LIMIT:-2700} \
    bash "$SCRIPT_DIR/final_stage.sh" "$d" "${LBL_PREFIX}-${kind}-r${rate}-s${seed}" \
      "$(echo "v4-${SYS}-${kind}-r${rate}-s${seed}" | tr '.' '_')" -- \
      env PRISM_ROOT=/workspace/prism-exp PRISM_REPO="$ROOT/prism-research" \
          PRISM_EXP="$ROOT/exp" PRISM_EVAL_DIR="$OUT" \
          CFG="$CFG" BENCHMARK_TIMEOUT=${BENCH_TIMEOUT:-1800} KVPR_TAU="$KVPR_TAU_VAL" \
          KVPR_WINDOW="$KVPR_WINDOW_VAL" KVPR_COOLDOWN="$KVPR_COOLDOWN_VAL" \
          PREFILL_SPEED_FILE="$CI" SLO_BASE_FILE="$SLO" \
          bash "$SCRIPT_DIR/run_v4_case.sh" "$SYS" "$kind" "$rate" "$seed" "$trace" "$d" \
      >> "$OUT/sweep.log" 2>&1
    rc=$?; t1=$(date +%s)

    $PY - "$OUT/PROGRESS.jsonl" "$idx" "$arm" "$SYS" "$kind" "$rate" "$seed" "$rc" "$((t1-t0))" "$d" \
         "$KVPR_TAU_VAL" "$KVPR_WINDOW_VAL" "$KVPR_COOLDOWN_VAL" <<'PYEOF'
import json,sys,datetime,subprocess
p,idx,arm,sys_,kind,rate,seed,rc,secs,d,tau,win,cd = sys.argv[1:]
rec={"idx":int(idx),"arm":arm,"system":sys_,"workload":kind,"rate":int(rate),
     "seed":int(seed),"tau":float(tau),"window_s":float(win),"cooldown_s":float(cd),
     "ok":rc=="0","seconds":int(secs),"run_dir":d,
     "finished_at":datetime.datetime.now(datetime.timezone.utc).isoformat()}
try: rec["numbers"]=json.load(open(d+"/VERIFICATION.json"))["numbers"]
except Exception: rec["numbers"]=None
g=subprocess.run(["/workspace/prism-exp/prism-venv/bin/python",
    "/workspace/prism-exp/exp/scripts/lifecycle_validity_gate.py",d],
    capture_output=True,text=True)
rec["lifecycle_gate"]="PASS" if g.returncode==0 else "FAIL"
rec["lifecycle_findings"]=[l.strip(" -") for l in g.stdout.splitlines() if l.strip().startswith("-")]
open(p,"a").write(json.dumps(rec)+"\n")
PYEOF
    [ "$rc" = "0" ] || stop_all "run $arm ${kind}_r${rate}_s${seed} failed (rc=$rc)"
    $PY "$SCRIPT_DIR/lifecycle_validity_gate.py" "$d" >/dev/null 2>&1 \
      || stop_all "LIFECYCLE VALIDITY FAILURE in $arm ${kind}_r${rate}_s${seed}"
  done
done
log "$STAGE complete ($total runs)"
bash "$SCRIPT_DIR/notify.sh" "$STAGE-done-$(date +%s)" "✅ $STAGE complete | $total runs" || true
exit 0
