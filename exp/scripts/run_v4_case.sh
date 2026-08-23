#!/bin/bash
# One paper-faithful-v4 run: (system, workload, rate, seed) -> metrics.
#
#   ./run_v4_case.sh <system> <workload> <rate> <seed> <trace.pkl> <outdir>
#
# v3 and v4 run through the SAME patched binary and differ only in the flags
# and environment set below, so nothing but the mechanism under test varies.
#
# systems -- ONLY the scheduler flags differ between them:
#   released-prototype  prototype global (simple-global) + prototype local
#   paper-alg1-only     paper KVPR global               + prototype local
#   paper-alg2-only     prototype global                + paper Moore-Hodgson
#   paper-migration-only paper KVPR + overlap/KV migration, Algorithm 2 off
#   paper-faithful      paper KVPR global               + paper Moore-Hodgson
#   paper-faithful-v3   literal absolute-tau KVPR + Moore-Hodgson + overlap loading
#
# workload is a label only (bursty|steady); the trace file decides the arrivals.
# GPUs, models, precision, kvcached, prompts, output lengths, seed, SLO scales,
# warm-up and measurement window are identical by construction.
set -euo pipefail

SCRIPT_DIR_EARLY=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$SCRIPT_DIR_EARLY/proc_ownership.sh"

# ---------------------------------------------------------------- client fds
# The benchmark client runs in THIS shell, not in the server's tmux command,
# and a tmux child inherits a soft nofile limit of 1024 while the hard limit is
# 524288. The server's launch raises its own limit inline; nothing raised this
# one, so the client ran out of descriptors and could not open connections --
# 1,971 requests in cal-0p00035-s42 failed with OSError Errno 24 before ever
# reaching the server, and every run in this pipeline shows thousands of them
# while the previous pipeline's runs show none.
#
# Raise it here, where the client actually starts, and refuse to run if it
# cannot be raised: a run under a low limit measures the client, not tau.
PRISM_CLIENT_NOFILE=${PRISM_CLIENT_NOFILE:-65535}
# Raise only. `ulimit -n` sets rather than raises, so an unconditional call
# would *lower* a shell that already had more than the requirement.
_soft0=$(ulimit -Sn)
if [ "$_soft0" != "unlimited" ] && [ "$_soft0" -lt "$PRISM_CLIENT_NOFILE" ]; then
  # -S: raise the soft limit only. Bare `ulimit -n` sets the hard limit too,
  # which would cap it at the requirement and make later raises impossible.
  ulimit -S -n "$PRISM_CLIENT_NOFILE" 2>/dev/null || true
fi
_soft=$(ulimit -Sn); _hard=$(ulimit -Hn)
if [ "$_soft" != "unlimited" ] && [ "$_soft" -lt "$PRISM_CLIENT_NOFILE" ]; then
  echo "FATAL: benchmark client nofile soft limit is $_soft, need $PRISM_CLIENT_NOFILE (hard=$_hard)" >&2
  exit 78
fi

SYSTEM=${1:?usage: run_v4_case.sh <system> <workload> <rate> <seed> <trace> <outdir>}
WORKLOAD=${2:?}
RATE=${3:?}
SEED=${4:?}
TRACE=${5:?}
OUTDIR=${6:?}
# benchmark.py runs from $PRISM_REPO/benchmark/multi-model, so a relative trace
# path resolves against the wrong directory and dies with FileNotFoundError
# after the server has already loaded all six models.
TRACE=$(readlink -f "$TRACE")
OUTDIR=$(readlink -f "$OUTDIR" 2>/dev/null || { mkdir -p "$OUTDIR"; readlink -f "$OUTDIR"; })

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/env.sh"

NGPU=${NGPU:-2}
CFG=${CFG:-$PRISM_EXP/configs/v2/6model_2gpu.json}
MAXMEM=${MAXMEM:-67.28}
TTFT_SCALE=${TTFT_SCALE:-5}
TPOT_SCALE=${TPOT_SCALE:-3}
KVPR_TAU=${KVPR_TAU:-0.35}
KVPR_WINDOW=${KVPR_WINDOW:-30}
KVPR_COOLDOWN=${KVPR_COOLDOWN:-30}
SLO_BASE=${SLO_BASE_FILE:-$PRISM_EXP/configs/v2/slo_base.json}
PREFILL_SPEED=${PREFILL_SPEED_FILE:-$PRISM_EXP/configs/v2/prefill_speed.json}

MODELS=($(python3 -c "import json;print(' '.join(m['model_name'] for m in json.load(open('$CFG'))))"))
NMODELS=${#MODELS[@]}
WORKERS=${WORKERS:-$(python3 -c "
import json,collections
c=collections.Counter(p['gpu_ids'][0] for m in json.load(open('$CFG'))
                      for p in m['init_placements'] if p['on'])
print(max(c.values())+1)")}

# The port is chosen at RUN TIME, not hard-coded. launch_multi_model_server
# derives one port per model engine from --port and refuses to start if --port
# is taken -- and it probes by binding 0.0.0.0, not 127.0.0.1. The 30000-40000
# range is where editor/tooling servers park dynamic ports, so a fixed base
# eventually collides and the run dies AFTER loading every model.
PORT=$(python3 "$SCRIPT_DIR/find_free_port.py" --from 41000 --span 24) || {
  echo "FATAL: no free port block"; exit 1; }
CONTROLLER=(--enable-controller)
case "$SYSTEM" in
  released-prototype) CONTROLLER+=(--policy simple-global) ;;
  paper-alg1-only)
      CONTROLLER+=(--policy kvpr-global --kvpr-tau "$KVPR_TAU"
                   --kvpr-rate-window "$KVPR_WINDOW" --slo-base-file "$SLO_BASE"
                   --kvpr-migration-cooldown "$KVPR_COOLDOWN"
                   --kvpr-tpot-slo-scale "$TPOT_SCALE") ;;
  paper-alg2-only)
      CONTROLLER+=(--policy simple-global --enable-moore-hodgson
                   --prefill-speed-file "$PREFILL_SPEED") ;;
  paper-faithful)
      CONTROLLER+=(--policy kvpr-global --kvpr-tau "$KVPR_TAU"
                   --kvpr-rate-window "$KVPR_WINDOW" --slo-base-file "$SLO_BASE"
                   --kvpr-migration-cooldown "$KVPR_COOLDOWN"
                   --kvpr-tpot-slo-scale "$TPOT_SCALE"
                   --enable-moore-hodgson --prefill-speed-file "$PREFILL_SPEED")
      ;;
  paper-faithful-v3)
      CONTROLLER+=(--policy kvpr-global-v3 --kvpr-tau "$KVPR_TAU"
                   --kvpr-rate-window "$KVPR_WINDOW" --slo-base-file "$SLO_BASE"
                   --kvpr-migration-cooldown "$KVPR_COOLDOWN"
                   --kvpr-tpot-slo-scale "$TPOT_SCALE"
                   --enable-moore-hodgson --prefill-speed-file "$PREFILL_SPEED"
                   --parallel-model-loading --overlap-migration)
      ;;
  paper-faithful-v3-alg1only)
      # v3 minus Algorithm 2: isolates what the KVPR placement costs.
      CONTROLLER+=(--policy kvpr-global-v3 --kvpr-tau "$KVPR_TAU"
                   --kvpr-rate-window "$KVPR_WINDOW" --slo-base-file "$SLO_BASE"
                   --kvpr-migration-cooldown "$KVPR_COOLDOWN"
                   --kvpr-tpot-slo-scale "$TPOT_SCALE"
                   --parallel-model-loading --overlap-migration)
      ;;
  paper-faithful-v3-alg2only)
      # v3 minus Algorithm 1: isolates what Moore-Hodgson costs.  The global
      # policy falls back to the prototype's, as in the released code.
      CONTROLLER+=(--policy simple-global
                   --enable-moore-hodgson --prefill-speed-file "$PREFILL_SPEED"
                   --parallel-model-loading --overlap-migration)
      ;;
  paper-faithful-v4)
      CONTROLLER+=(--policy kvpr-global-v4 --kvpr-tau "$KVPR_TAU"
                   --kvpr-rate-window "$KVPR_WINDOW" --slo-base-file "$SLO_BASE"
                   --kvpr-migration-cooldown "$KVPR_COOLDOWN"
                   --kvpr-tpot-slo-scale "$TPOT_SCALE"
                   --enable-moore-hodgson --prefill-speed-file "$PREFILL_SPEED"
                   --parallel-model-loading --overlap-migration)
      # Read by the ModelService process, which is forked from this env.
      # env.sh re-sources /workspace/.env with `set -a`, which clobbers a
      # caller's PRISM_V4_* choice; V5_* is not in that file, so it wins.
      export PRISM_V4_PAGELOCK=${V5_PAGELOCK:-${PRISM_V4_PAGELOCK:-1}}
      # Filling a target from the source GPU means the model service holds a
      # CUDA IPC mapping of the source weights.  torch.cuda.empty_cache() does
      # not release those -- it only returns this process's own allocator cache
      # -- so without torch.cuda.ipc_collect() the engine's deactivated weights
      # stay resident and the run walks into an OOM (measured: 80 GiB on GPU 0
      # against 60 for v3 and 43 for the prototype).  The release path collects
      # them explicitly; set PRISM_V4_P2P_MIGRATION=0 to fall back to the host
      # path.
      export PRISM_V4_P2P_MIGRATION=${V5_P2P:-${PRISM_V4_P2P_MIGRATION:-1}}
      ;;
  paper-faithful-v6)
      # v4 plus the paper's KV-cache migration.  Identical to the v4 arm in
      # every other respect so a v4/v6 pair isolates exactly the KV transfer.
      CONTROLLER+=(--policy kvpr-global-v4 --kvpr-tau "$KVPR_TAU"
                   --kvpr-rate-window "$KVPR_WINDOW" --slo-base-file "$SLO_BASE"
                   --kvpr-migration-cooldown "$KVPR_COOLDOWN"
                   --kvpr-tpot-slo-scale "$TPOT_SCALE"
                   --enable-moore-hodgson --prefill-speed-file "$PREFILL_SPEED"
                   --overlap-migration
                   --enable-kv-migration)
      # Initial placement is deliberately serialized. Concurrent startup
      # activation has produced model-service SIGSEGVs while loading different
      # models onto one GPU; this flag affects boot only, not migration overlap.
      export PRISM_V4_PAGELOCK=${V5_PAGELOCK:-${PRISM_V4_PAGELOCK:-1}}
      export PRISM_V4_P2P_MIGRATION=${V5_P2P:-${PRISM_V4_P2P_MIGRATION:-1}}
      # The policy runs in the controller process and the capture/inject run in
      # engine processes; an env var crosses that boundary, a parsed arg does
      # not.  --enable-kv-migration sets this too, but set it here as well so
      # the ModelService fork sees it regardless of parse order.
      export PRISM_V6_KV_MIGRATION=1
      ;;
  paper-migration-only)
      # Diagnostic D2: identical v6 migration runtime, with Algorithm 2
      # deliberately absent so the run isolates migration integration.
      CONTROLLER+=(--policy kvpr-global-v4 --kvpr-tau "$KVPR_TAU"
                   --kvpr-rate-window "$KVPR_WINDOW" --slo-base-file "$SLO_BASE"
                   --kvpr-migration-cooldown "$KVPR_COOLDOWN"
                   --kvpr-tpot-slo-scale "$TPOT_SCALE"
                   --overlap-migration
                   --enable-kv-migration)
      export PRISM_V4_PAGELOCK=${V5_PAGELOCK:-${PRISM_V4_PAGELOCK:-1}}
      export PRISM_V4_P2P_MIGRATION=${V5_P2P:-${PRISM_V4_P2P_MIGRATION:-1}}
      export PRISM_V6_KV_MIGRATION=1
      ;;
  *) echo "unknown system: $SYSTEM" >&2; exit 1 ;;
esac
# Any arm other than v4 must see the v3 code path, even if the shell that
# invoked us had the switches set.
case "$SYSTEM" in
  paper-faithful-v4|paper-faithful-v6|paper-migration-only) ;;
  *) unset PRISM_V4_PAGELOCK PRISM_V4_P2P_MIGRATION ;;
esac
# Likewise: only the v6 arm may see the KV-migration switch, whatever the
# invoking shell had set.
case "$SYSTEM" in
  paper-faithful-v6|paper-migration-only) export PRISM_V6_KV_TRACE="$OUTDIR/kv_transfers.jsonl" ;;
  *) unset PRISM_V6_KV_MIGRATION PRISM_V6_KV_TRACE ;;
esac
# Per-run raw record of every weight transfer.
export PRISM_V4_LOAD_TRACE="$OUTDIR/weight_transfers.jsonl"

EXP="${SYSTEM}_${WORKLOAD}_rate${RATE}_seed${SEED}"
LOGDIR=$OUTDIR/server-logs
mkdir -p "$OUTDIR" "$LOGDIR" "$OUTDIR/requests"
# tmux rewrites '.' to '_' in session names, so a has-session probe on the raw
# name never matches and the readiness loop declares a healthy server DIED --
# orphaning it while it still holds the GPU. Normalise first.
SESSION=$(echo "v4-${SYSTEM}-${WORKLOAD}-r${RATE}-s${SEED}" | tr '.' '_')

echo "### $EXP  port=$PORT trace=$(basename "$TRACE") workers=$WORKERS models=$NMODELS"

ARGS=(
  --model-config-file "$CFG"
  --host 127.0.0.1 --port "$PORT"
  --disable-cuda-graph --disable-radix-cache
  --log-file "$LOGDIR/server.log"
  --enable-elastic-memory --use-kvcached-v0 --enable-cpu-share-memory
  --max-mem-usage "$MAXMEM"
  --enable-gpu-scheduler
  "${CONTROLLER[@]}"
  --enable-model-service --enable-worker-pool
  --workers-per-gpu "$WORKERS" --num-model-service-workers "$NMODELS"
  --num-gpus "$NGPU"
)
VISIBLE=$(seq -s, 0 $((NGPU - 1)))

cleanup() {
  # NEVER `kill 0`. If SAMPLER is unset -- which it is on every early exit, i.e.
  # exactly when the server failed to come up -- "${SAMPLER:-0}" expands to 0,
  # and `kill 0` signals the ENTIRE PROCESS GROUP: this script, the sweep
  # driver, the watchdog, everything. Three separate multi-hour runs were lost
  # to this before supervisord's log made it visible ("terminated by SIGTERM;
  # not expected"). Only ever signal a PID we actually started.
  [ -n "${SAMPLER:-}" ] && kill "$SAMPLER" 2>/dev/null || true
  tmux kill-session -t "$SESSION" 2>/dev/null || true
  # Signal only the process group this run started. The pattern kill that used
  # to live here could match another run's server.
  prism_kill_server "$OUTDIR" "run_v4_case cleanup" 2>/dev/null || true
  sleep 3
}
trap cleanup EXIT

tmux kill-session -t "$SESSION" 2>/dev/null || true
# A killed run leaves /dev/shm/ipc_0_model_*_root behind under the same names
# the next launch wants; clear them so a stale segment cannot be inherited.
rm -f /dev/shm/ipc_[0-9]*_root /dev/shm/cuda.shm.* /dev/shm/ipc_0_model_*_root 2>/dev/null || true
# tmux keeps ONE server per user and every new-session inherits the environment
# that server was first started with -- not this shell's.  The v4 switches are
# read by the forked ModelService process, so they have to be written into the
# command itself, exactly like CUDA_VISIBLE_DEVICES.
V4ENV="export PRISM_V4_LOAD_TRACE='$PRISM_V4_LOAD_TRACE'"
[ -n "${PRISM_V4_PAGELOCK:-}" ] && V4ENV="$V4ENV PRISM_V4_PAGELOCK=$PRISM_V4_PAGELOCK"
[ -n "${PRISM_V4_P2P_MIGRATION:-}" ] && V4ENV="$V4ENV PRISM_V4_P2P_MIGRATION=$PRISM_V4_P2P_MIGRATION"
[ -n "${PRISM_V6_KV_MIGRATION:-}" ] && V4ENV="$V4ENV PRISM_V6_KV_MIGRATION=$PRISM_V6_KV_MIGRATION"
[ -n "${PRISM_V6_KV_TRACE:-}" ] && V4ENV="$V4ENV PRISM_V6_KV_TRACE='$PRISM_V6_KV_TRACE'"
[ -n "${PRISM_DIAG_ALG2:-}" ] && V4ENV="$V4ENV PRISM_DIAG_ALG2=$PRISM_DIAG_ALG2"
# Diagnostic only: per-request KV ownership reconciliation. Read by nothing
# but the log, and off unless explicitly set.
[ -n "${PRISM_KV_OWN_TRACE:-}" ] && V4ENV="$V4ENV PRISM_KV_OWN_TRACE=$PRISM_KV_OWN_TRACE"
tmux new-session -d -s "$SESSION" \
  "ulimit -n 65535 && export PRISM_ROOT='$PRISM_ROOT' PRISM_REPO='$PRISM_REPO' \
   PRISM_EXP='$PRISM_EXP' PYTHONPATH='$PRISM_REPO/python' && \
   export CUDA_VISIBLE_DEVICES=$VISIBLE && cd $PRISM_REPO/benchmark/multi-model && \
   source $SCRIPT_DIR/env.sh && export CUDA_VISIBLE_DEVICES=$VISIBLE && $V4ENV && \
   { python3 $SCRIPT_DIR/verify_sglang_source.py '$PRISM_REPO' && \
     python3 -m sglang.launch_multi_model_server ${ARGS[*]}; } \
   2>&1 | tee $LOGDIR/stdout.log"

echo -n "waiting for server"
READY=0
for _ in $(seq 1 600); do
    if curl -sf "http://127.0.0.1:$PORT/get_model_names" >/dev/null 2>&1; then READY=1; break; fi
    if ! tmux has-session -t "$SESSION" 2>/dev/null; then
        echo " -> DIED, see $LOGDIR/stdout.log"; exit 1
    fi
    # A model-service worker can segfault while the launcher and tmux session
    # remain alive.  Without this check the run waits the full 20-minute
    # readiness timeout with one model permanently stuck in `activating`.
    SERVER_PID=$(pgrep -f "^python3 -m sglang.launch_multi_model_server .*--port $PORT( |$)" | head -1 || true)
    if [ -n "$SERVER_PID" ] && ps --ppid "$SERVER_PID" -o stat= 2>/dev/null | grep -q '^Z'; then
        echo " -> CHILD_DIED, see $LOGDIR/stdout.log and server logs"; exit 1
    fi
    echo -n "."; sleep 2
done
[ "$READY" = 1 ] || { echo " -> timeout, see $LOGDIR/stdout.log"; exit 1; }
echo " -> ready"
# Ownership: from here on, teardown signals this pid/pgid and nothing else.
prism_record_server "$OUTDIR" "$PORT" "$EXP" || \
  echo "[proc] WARNING: could not record the server pid; teardown will be a no-op" >&2

( while true; do
    echo "$(date +%s.%N) $(nvidia-smi --query-gpu=index,memory.used,utilization.gpu \
        --format=csv,noheader,nounits | tr '\n' ';')"
    sleep 2
  done ) > "$LOGDIR/gpu_timeline.txt" 2>/dev/null &
SAMPLER=$!

cd "$PRISM_REPO/benchmark/multi-model"
# Evidence, per run, that the client started with enough descriptors.
PRISM_SOFT_BEFORE="$_soft0" python3 - "$OUTDIR" "$(ulimit -Sn)" "$(ulimit -Hn)" "$PRISM_CLIENT_NOFILE" <<'PYFD'
import json, os, sys
out, soft, hard, need = sys.argv[1:5]
json.dump({"required_nofile": int(need),
           "shell_soft_nofile": soft, "shell_hard_nofile": hard,
           "shell_soft_before_raise": os.environ.get("PRISM_SOFT_BEFORE", ""),
           "recorded_before_benchmark_start": True},
          open(os.path.join(out, "client_fd_limits.json"), "w"), indent=2)
PYFD
# The shell's limit is what the client inherits, but record what the client
# process itself actually got -- that is the number that decides the run.
( for _ in $(seq 1 40); do
    _bpid=$(pgrep -f "python3 benchmark.py --base-url http://127.0.0.1:$PORT" | head -1)
    if [ -n "$_bpid" ] && [ -r "/proc/$_bpid/limits" ]; then
      _lim=$(grep "Max open files" "/proc/$_bpid/limits" | tr -s ' ')
      python3 - "$OUTDIR" "$_bpid" "$_lim" <<'PYP'
import json, os, sys
out, pid, lim = sys.argv[1], sys.argv[2], sys.argv[3]
f = os.path.join(out, "client_fd_limits.json")
d = json.load(open(f)) if os.path.exists(f) else {}
parts = lim.split()
d["benchmark_pid"] = int(pid)
d["proc_limits_line"] = lim
try:
    d["proc_soft_nofile"], d["proc_hard_nofile"] = int(parts[-3]), int(parts[-2])
except Exception:
    pass
json.dump(d, open(f, "w"), indent=2)
PYP
      break
    fi
    sleep 0.5
  done ) &
set +e
timeout --signal=TERM --kill-after=30s "${BENCHMARK_TIMEOUT:-1800}" python3 benchmark.py \
  --base-url "http://127.0.0.1:$PORT" \
  --num-models "$NMODELS" --model-paths "${MODELS[@]}" \
  --exp-name "$EXP" --results-path "$OUTDIR" --request-path "$OUTDIR/requests" \
  --seed "$SEED" --disable-tqdm \
  --e2e-benchmark --real-trace "$TRACE" \
  --time-scale 1 --replication 1 --num-gpus "$NGPU" \
  --enable-elastic-memory \
  --ttft-slo-scale "$TTFT_SCALE" --tpot-slo-scale "$TPOT_SCALE" \
  > "$LOGDIR/bench.log" 2>&1
RC=$?
set -e
kill "$SAMPLER" 2>/dev/null || true
[ $RC -eq 0 ] || { echo "benchmark failed rc=$RC"; tail -40 "$LOGDIR/bench.log"; exit $RC; }

# Proof the intended scheduler actually ran, not merely that the flag parsed.
# -F is required: the markers are bracketed, and "[PAPER-ALG1]" as a regex is a
# character class whose "R-A" is an invalid range -- grep exits 2 and every
# count silently reads 0, indistinguishable from "the algorithm never ran".
GC="$LOGDIR/server.log.global_controller.log"
count_gc() { local n; n=$(grep -cF -- "$1" "$GC" 2>/dev/null || true); echo "${n:-0}"; }
count_gs() { local n; n=$(cat "$LOGDIR"/*gpu_scheduler*.log 2>/dev/null | grep -cF -- "$1" || true); echo "${n:-0}"; }
count_file() { local n; n=$(grep -cF -- "$1" "$2" 2>/dev/null || true); echo "${n:-0}"; }
{
  echo "system=$SYSTEM workload=$WORKLOAD rate=$RATE seed=$SEED"
  echo "max_mem_usage=$MAXMEM"
  # Recorded so downstream analysis can map model names to paths without
  # having to recover the launcher command line from a log.
  echo "model_config_file=$CFG"
  echo "flashinfer_workspace_size=${FLASHINFER_WORKSPACE_SIZE:-unset}"
  if [ "$SYSTEM" = "paper-faithful-v4" ] || [ "$SYSTEM" = "paper-faithful-v6" ] || [ "$SYSTEM" = "paper-migration-only" ]; then
    echo "alg1_log_lines=$(count_gc '[PAPER-ALG1-V4]')"
    echo "alg1_migrations=$(count_gc '"migration_decision": "MIGRATE"')"
    echo "v4_weight_transfers=$(wc -l < "$OUTDIR/weight_transfers.jsonl" 2>/dev/null || echo 0)"
    echo "v4_p2p_transfers=$(count_file '"bytes_p2p":' "$OUTDIR/weight_transfers.jsonl")"
    if [ "$SYSTEM" = "paper-faithful-v6" ] || [ "$SYSTEM" = "paper-migration-only" ]; then
      echo "v6_kv_transfers=$(wc -l < "$OUTDIR/kv_transfers.jsonl" 2>/dev/null || echo 0)"
      echo "v6_p2p_transfers=$(count_file '"transfer_path": "gpu-to-gpu-p2p"' "$OUTDIR/kv_transfers.jsonl")"
      echo "v6_resumed_requests=$(count_file '"event": "resume"' "$LOGDIR/server.log")"
    fi
  elif [ "$SYSTEM" = "paper-faithful-v3" ]; then
    echo "alg1_log_lines=$(count_gc '[PAPER-ALG1-V3]')"
    echo "alg1_migrations=$(count_gc '"migration_decision": "MIGRATE"')"
  else
    echo "alg1_log_lines=$(count_gc '[PAPER-ALG1]')"
    echo "alg1_migrations=$(count_gc '[PAPER-ALG1] MIGRATE')"
  fi
  echo "alg2_log_lines=$(count_gs '[PAPER-ALG2]')"
  echo "alg2_underadmission_warnings=$(count_gs '[PAPER-ALG2-WARN]')"
  if [ "$SYSTEM" = "paper-faithful-v3" ] || [ "$SYSTEM" = "paper-faithful-v4" ] || [ "$SYSTEM" = "paper-faithful-v6" ] || [ "$SYSTEM" = "paper-migration-only" ]; then
    echo "proto_migrations=0"
  else
    echo "proto_migrations=$(count_gc 'Reason: migrate model')"
  fi
  echo "activations=$(count_gc 'ACTION: activate')"
  echo "deactivations=$(count_gc 'ACTION: deactivate')"
  echo "idle_evictions=$(count_gc 'Reason: idle instance eviction')"
} > "$OUTDIR/scheduler_proof.txt"
cat "$OUTDIR/scheduler_proof.txt"
echo "### $EXP done"
