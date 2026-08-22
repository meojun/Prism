#!/usr/bin/env python3
"""Three protections that must not be confused with one another.

The elastic-v0 clamp on `available_size()` was added to stop KV pools taking
memory an incoming activation needed. It did that indirectly -- by bounding
every pool's reported availability by device-wide free memory -- and the
measured cost was that one GPU tightening cut every pool's prefill budget at
once, however empty that pool was.

That protection is now provided directly and in two places: the planner refuses
a target without weights plus the engine's reserve, and `load_gpu_model` waits
for the same thing immediately before the allocation that used to fail. So the
clamp is gone from the v0 path and availability is pool-local again.

These tests keep the three concerns apart:

  A  a tight GPU must not shrink a pool's prefill budget when that pool has room
  B  a tight GPU must still refuse a new activation that does not fit
  C  a pool that is genuinely out of KV must still behave as it always did
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2] / "prism-research"
sys.path.insert(0, str(REPO / "python"))

from sglang.srt.mem_cache import memory_pool as mp  # noqa: E402
from sglang.multi_model.scheduling.policy.kvpr_global_v4 import (  # noqa: E402
    KVPRGlobalPolicyV4,
)
from sglang.srt.model_executor import worker_pool_model_runner as wp  # noqa: E402
from sglang.srt.model_executor.worker_pool_model_runner import (  # noqa: E402
    WorkerPoolModelRunner,
)

PASS, FAIL = [], []

WEIGHTS = {
    "model_2": {"model_size": 3.0078125, "cell_size": 28672},
    "model_6": {"model_size": 14.283203125, "cell_size": 57344},
}
RESERVE = 6.459


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


class Allocator:
    def __init__(self, blocks):
        self.blocks = blocks
        self.block_mem_size = 1 << 16

    def available_size(self):
        return self.blocks


def pool(pool_local_blocks, device_free_blocks, v0=True):
    obj = mp.MHATokenToKVPool.__new__(mp.MHATokenToKVPool)
    obj.enable_elastic_memory = True
    obj.use_kvcached_v0 = v0
    obj.min_reserve_mem = RESERVE
    obj.kv_allocator = Allocator(pool_local_blocks)
    obj.last_available_size = None
    obj.physical_calls = []

    def fake_physical(reserve_gib):
        obj.physical_calls.append(reserve_gib)
        return device_free_blocks

    obj._physical_free_size = fake_physical
    return obj


# ---------------------------------------------------------------- A
def test_a_a_tight_gpu_does_not_shrink_a_pool_that_has_room():
    print("A: global GPU free is low, this pool has KV to spare")
    # The measured shape: the pool is nearly empty, the device is nearly full.
    p = pool(pool_local_blocks=573_041, device_free_blocks=12)
    size = p.available_size()
    check("the pool reports what it actually has", size == 573_041)
    check("so a prefill budget built from it is not zero", size > 0)
    check("and device-wide free memory is not even consulted",
          not p.physical_calls)

    # The run-7 numbers, as a regression: model_2 held 400 tokens of a
    # 933,888-token pool while the GPU was tight.
    run7 = pool(pool_local_blocks=933_488, device_free_blocks=0)
    check("model_2's pool would have admitted a prefill in run 7",
          run7.available_size() == 933_488)


def test_a_the_non_v0_path_is_untouched():
    print("A: the non-v0 elastic path keeps the bound it always had")
    p = pool(pool_local_blocks=500_000, device_free_blocks=900, v0=False)
    check("it is still bounded by physical memory", p.available_size() == 900)
    check("with its own 0.5 GB reserve", p.physical_calls[0] == 0.5)


# ---------------------------------------------------------------- B
def test_b_a_tight_gpu_still_refuses_an_activation_that_does_not_fit():
    print("B: the planner still refuses an activation without weights+reserve")
    policy = KVPRGlobalPolicyV4(
        num_gpus=2, gpu_mem=79.15, model_weights_info=WEIGHTS,
        workers_per_gpu=4, tau=0.07, rate_window=30.0,
        migration_cooldown=0.0, target_reserve_gib=RESERVE)

    need = policy._target_memory_required("model_6")
    check("the requirement is weights plus the engine's reserve",
          abs(need - (14.283203125 + RESERVE)) < 1e-9)

    tight = {0: 3.9, 1: 4.0}          # the GPU1 free memory of run 7
    clusters = policy._prepare_gpu_clusters(tight)
    target = policy._place_inactive_model(
        "model_6", 0, need, tight, clusters, {}, {}, {0: 0, 1: 0})
    check("a tight GPU is refused for a new activation", target is None)

    roomy = {0: 44.67, 1: 4.0}
    clusters = policy._prepare_gpu_clusters(roomy)
    check("a roomy one is still accepted",
          policy._place_inactive_model(
              "model_6", 0, need, roomy, clusters, {}, {}, {0: 0, 1: 0}) == 0)

    small = policy._target_memory_required("model_2")
    clusters = policy._prepare_gpu_clusters({0: 9.9, 1: 4.0})
    check("and a GPU that fits the small model but not the big one takes it",
          policy._place_inactive_model(
              "model_2", 0, small, {0: 9.9, 1: 4.0}, clusters, {}, {},
              {0: 0, 1: 0}) == 0)


def test_b_the_engine_still_waits_at_the_allocation_itself():
    print("B: and the engine still guards the allocation that used to OOM")
    obj = WorkerPoolModelRunner.__new__(WorkerPoolModelRunner)
    obj.model_config = SimpleNamespace(path="Qwen/Qwen2.5-7B-Instruct")
    obj.device, obj.gpu_id = "cuda", 0
    obj.min_reserve_mem = RESERVE
    obj.model_gpu_mem_usage = 14.283203125

    readings = [18.15, 18.15, 40.0]
    slept = []

    def free(device, gpu_id):
        return readings.pop(0) if len(readings) > 1 else readings[0]

    with patch.object(wp, "get_available_gpu_memory", free), \
            patch.object(wp.time, "sleep", lambda s: slept.append(s)):
        try:
            WorkerPoolModelRunner.load_gpu_model(
                obj, check_mem=True, use_model_service=False)
        except Exception:
            pass
    check("18.15 GB free is not enough for 14.28 + 6.46, so it waits",
          len(slept) >= 1)

    source = (REPO / "python/sglang/srt/model_executor/"
              "worker_pool_model_runner.py").read_text()
    body = source.split("def load_gpu_model", 1)[1]
    guard = body.index("min_reserve_mem")
    alloc = body.index("create_empty_gpu_model_from_cpu_model")
    check("and the wait sits before the allocation that used to fail",
          guard < alloc)


# ---------------------------------------------------------------- C
def test_c_a_pool_that_is_really_out_of_kv_still_says_so():
    print("C: genuine KV pressure is unchanged")
    empty = pool(pool_local_blocks=0, device_free_blocks=10 ** 9)
    check("a pool with no blocks reports none", empty.available_size() == 0)

    nearly = pool(pool_local_blocks=8, device_free_blocks=10 ** 9)
    check("a nearly empty pool reports what little it has",
          nearly.available_size() == 8)

    source = (REPO / "python/sglang/srt/managers/scheduler.py").read_text()
    check("retraction is still driven by the pool's own decode memory check",
          "check_decode_mem()" in source and "retract_decode()" in source)
    check("and the prefill budget still comes from the pool plus the tree cache",
          "self.token_to_kv_pool.available_size() + self.tree_cache.evictable_size()"
          in source)


# ------------------------------------------------- the run 4 reproduction
def test_the_activation_oom_condition_is_still_covered():
    """D2 run 4, replayed: the planner clears it, then the memory vanishes.

    At 09:44:57 the controller read 18.15 GB free on GPU1 and cleared model_2
    to activate; three seconds later the pools had taken all of it and
    `create_empty_gpu_model_from_cpu_model` died with 185.25 MiB free. That is
    the condition the clamp was added for. It has to stay covered without it:
    the planner refuses what it can see is infeasible, and for the race it
    cannot see, the engine blocks before the allocation rather than attempting
    it.
    """
    print("the run 4 activation-OOM condition, without the clamp")
    policy = KVPRGlobalPolicyV4(
        num_gpus=2, gpu_mem=79.15, model_weights_info=WEIGHTS,
        workers_per_gpu=4, tau=0.07, rate_window=30.0,
        migration_cooldown=0.0, target_reserve_gib=RESERVE)

    # What the planner could see at 09:44:57.
    free_then = {0: 10.0, 1: 18.15}
    need_small = policy._target_memory_required("model_2")   # 3.01 + 6.46
    need_big = policy._target_memory_required("model_6")     # 14.28 + 6.46
    clusters = policy._prepare_gpu_clusters(free_then)
    check("18.15 GB does clear the small model the planner cleared then",
          policy._place_inactive_model(
              "model_2", 0, need_small, free_then, clusters, {}, {},
              {0: 0, 1: 0}) is not None)
    check("but the same GPU is refused for one that does not fit",
          policy._place_inactive_model(
              "model_6", 0, need_big, free_then, clusters, {}, {},
              {0: 0, 1: 0}) is None)

    # The race the planner cannot see: cleared at 18.15 GB, 185 MiB by the
    # time the engine gets there.
    obj = WorkerPoolModelRunner.__new__(WorkerPoolModelRunner)
    obj.model_config = SimpleNamespace(path="Qwen/Qwen2.5-1.5B-Instruct")
    obj.device, obj.gpu_id = "cuda", 1
    obj.min_reserve_mem = RESERVE
    obj.model_gpu_mem_usage = 3.0078125
    # what the model-service branch reads once it is past the guard
    obj.model_path = "Qwen/Qwen2.5-1.5B-Instruct"
    obj.tp_size = 1
    obj.shared_cpu_models = {(obj.model_path, 1): ["cpu-ref"]}

    collapsed = [0.181]          # 185.25 MiB, as measured
    slept, allocated = [], []

    def free(device, gpu_id):
        return collapsed[0]

    def allocate(*a, **k):
        allocated.append(True)
        raise AssertionError("the allocation must not be attempted")

    with patch.object(wp, "get_available_gpu_memory", free),             patch.object(wp.time, "sleep", lambda s: slept.append(s)),             patch.object(wp, "create_empty_gpu_model_from_cpu_model", allocate):
        import threading
        done = threading.Event()

        def run():
            try:
                WorkerPoolModelRunner.load_gpu_model(
                    obj, check_mem=True, use_model_service=True)
            except Exception:
                pass
            done.set()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        done.wait(timeout=1.0)

        check("with 185 MiB free the engine waits instead of allocating",
              not allocated and len(slept) > 0)
        check("and it is still waiting, not past the guard", not done.is_set())

        # And it proceeds the moment the memory comes back -- the guard delays
        # the allocation, it does not cancel it.
        collapsed[0] = 40.0
        done.wait(timeout=3.0)
        check("once memory returns it goes ahead", allocated)


def main():
    test_a_a_tight_gpu_does_not_shrink_a_pool_that_has_room()
    test_a_the_non_v0_path_is_untouched()
    test_b_a_tight_gpu_still_refuses_an_activation_that_does_not_fit()
    test_b_the_engine_still_waits_at_the_allocation_itself()
    test_c_a_pool_that_is_really_out_of_kv_still_says_so()
    test_the_activation_oom_condition_is_still_covered()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for name in FAIL:
            print(f"  FAILED: {name}")
        raise SystemExit(1)
    print("ALL KV BUDGET / ACTIVATION GUARD TESTS PASSED")


if __name__ == "__main__":
    main()
