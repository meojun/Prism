#!/bin/bash
# Stage 1: measure c_i for all six models on the frozen code and this A100 pair.
#
# One solo server per model, no queueing, profiled by profile_v2.py; c_i is its
# saturated prefill throughput (E3_prefill_saturated), the same estimator the
# existing prefill_speed.json was built from. Two models at a time, one per GPU.
# A model whose profile already exists is not reprofiled.
set -uo pipefail

OUT=${1:?output dir}
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/env.sh"
mkdir -p "$OUT/configs" "$OUT/logs" "$OUT/raw"

profile_one() {
  local slot=$1 path=$2 gpu=$3 port=$4 sat=$5
  local cfg="$OUT/configs/solo_${slot}.json" log="$OUT/logs/${slot}"
  if [ -s "$OUT/raw/${slot}.json" ]; then
    echo "[c_i] $slot already profiled"; return 0
  fi
  python3 - "$cfg" "$slot" "$path" <<'PY'
import json, sys
cfg, slot, path = sys.argv[1:]
json.dump([{"model_name": slot, "model_path": path, "tp_size": 1,
            "init_placements": [{"gpu_ids": [0], "on": True,
                                 "max_memory_pool_size": 40.0}]}],
          open(cfg, "w"), indent=2)
PY
  tmux kill-session -t "ci-$slot" 2>/dev/null || true
  fuser -k -n tcp "$port" 2>/dev/null || true
  rm -f /dev/shm/ipc_[0-9]*_root /dev/shm/cuda.shm.* 2>/dev/null || true
  tmux new-session -d -s "ci-$slot" \
    "export CUDA_VISIBLE_DEVICES=$gpu; cd '$PRISM_REPO/benchmark/multi-model'; \
     source '$SCRIPT_DIR/env.sh'; export CUDA_VISIBLE_DEVICES=$gpu; \
     python3 -m sglang.launch_multi_model_server --model-config-file '$cfg' \
       --host 127.0.0.1 --port $port --disable-cuda-graph --disable-radix-cache \
       --enable-elastic-memory --use-kvcached-v0 --enable-cpu-share-memory \
       --log-file '${log}.log' > '${log}_stdout.log' 2>&1"

  local ready=0
  for _ in $(seq 1 400); do
    curl -sf "http://127.0.0.1:$port/get_model_names" >/dev/null 2>&1 && { ready=1; break; }
    tmux has-session -t "ci-$slot" 2>/dev/null || break
    sleep 2
  done
  if [ "$ready" -ne 1 ]; then
    echo "[c_i] $slot: server never became ready" >&2
    tail -40 "${log}_stdout.log" >&2; return 1
  fi

  python3 "$SCRIPT_DIR/profile_v2.py" --url "http://127.0.0.1:$port" \
    --model "$slot" --model-path "$path" --per-bucket 40 \
    --sat-concurrency "$sat" --sat-rounds 6 -o "$OUT/raw/${slot}.json" \
    > "$OUT/logs/${slot}_profile.log" 2>&1
  local rc=$?
  tmux kill-session -t "ci-$slot" 2>/dev/null || true
  fuser -k -n tcp "$port" 2>/dev/null || true
  sleep 5
  [ "$rc" = "0" ] && [ -s "$OUT/raw/${slot}.json" ]
}

# (slot, path, saturation concurrency) -- the bigger models saturate at lower
# concurrency, as the earlier profiling round established.
run_pair() {
  profile_one "$1" "$2" 0 35401 "$3" & local a=$!
  profile_one "$4" "$5" 1 35402 "$6" & local b=$!
  wait $a; local ra=$?
  wait $b; local rb=$?
  return $(( ra != 0 || rb != 0 ))
}

set -e
run_pair model_1 meta-llama/Llama-3.2-1B      48  model_6 Qwen/Qwen2.5-7B-Instruct   8
run_pair model_2 Qwen/Qwen2.5-1.5B-Instruct   48  model_5 meta-llama/Llama-3.1-8B    8
run_pair model_3 meta-llama/Llama-3.2-3B      24  model_4 Qwen/Qwen2.5-3B-Instruct  24
set +e

python3 - "$OUT" <<'PY'
import json, sys
from pathlib import Path
out = Path(sys.argv[1])
speeds, provenance = {}, {}
for slot in [f"model_{i}" for i in range(1, 7)]:
    data = json.loads((out / "raw" / f"{slot}.json").read_text())
    est = data["c_i_estimators"]
    c_i = (est.get("E3_prefill_saturated") or est.get("E3_prefill_solo")
           or est.get("E1_ratio_sum_p_over_sum_ttft"))
    if not c_i:
        raise SystemExit(f"FATAL: no usable c_i for {slot}")
    speeds[slot] = c_i
    provenance[slot] = {
        "model_path": data["model_path"],
        "estimator_used": ("E3_prefill_saturated"
                           if est.get("E3_prefill_saturated") else "fallback"),
        "estimators": est,
        "n_sequential": data["n_sequential"],
        "n_saturated": data["n_saturated"],
        "slo_baseline": data["slo_baseline"],
    }
(out / "prefill_speed_final_a100.json").write_text(
    json.dumps(speeds, indent=2, sort_keys=True) + "\n")
(out / "prefill_speed_final_a100_provenance.json").write_text(
    json.dumps(provenance, indent=2, sort_keys=True) + "\n")
print(json.dumps(speeds, indent=2, sort_keys=True))
PY
