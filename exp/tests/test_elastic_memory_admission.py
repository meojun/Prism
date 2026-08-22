#!/usr/bin/env python3
"""Elastic KV admission: pool-local for v0, physically bounded for non-v0.

The v0 path was clamped to device-wide free memory for a while (ad8a76c,
824caa7) to stop the pools taking memory an incoming activation needed. That
protection now sits where it belongs -- the planner refuses a target without
weights plus the engine's reserve, and `load_gpu_model` waits for the same
thing immediately before the allocation that used to fail -- so the clamp is
gone and v0 reports what its own allocator has.

Its cost had been measured: `_physical_free_size` is device-wide, so one GPU
tightening made every pool on it report as nearly full and cut every
PrefillAdder budget at once. In the ownership trace model_2 reported 360,847
tokens used while its live requests held 400, with 573,041 still available.

The non-v0 elastic path keeps the bound it has always had.
"""

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2] / "prism-research"
sys.path.insert(0, str(REPO / "python"))

from sglang.srt.mem_cache import memory_pool as mp  # noqa: E402

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


class Allocator:
    def __init__(self, blocks):
        self.blocks = blocks

    def available_size(self):
        return self.blocks


def pool(virtual_blocks, physical_blocks, *, v0=True, elastic=True,
         min_reserve_mem=6.459):
    """`min_reserve_mem` is carried but must NOT gate admission: an activation
    waits for its own weights in load_gpu_model, which is where that reserve
    belongs."""
    """An MHATokenToKVPool with CUDA and the allocator stubbed out."""
    obj = mp.MHATokenToKVPool.__new__(mp.MHATokenToKVPool)
    obj.enable_elastic_memory = elastic
    obj.use_kvcached_v0 = v0
    obj.min_reserve_mem = min_reserve_mem
    obj.kv_allocator = Allocator(virtual_blocks)
    obj.last_available_size = None
    obj.calls = []

    def fake_physical_free_size(reserve_gib):
        obj.calls.append(reserve_gib)
        return physical_blocks

    obj._physical_free_size = fake_physical_free_size
    return obj


def test_v0_is_pool_local():
    print("kvcached v0 admission")
    p = pool(virtual_blocks=1_000_000, physical_blocks=1_200)
    check("v0 reports what its own allocator has",
          p.available_size() == 1_000_000)
    check("and does not consult device-wide free memory", not p.calls)

    # The shape that stalled D3 run 7: the device is full, this pool is not.
    tight = pool(virtual_blocks=573_041, physical_blocks=0)
    check("a pool with room is not zeroed by a full device",
          tight.available_size() == 573_041)


def test_the_allocator_still_binds_when_it_is_smaller():
    print("the allocator's own limit")
    p = pool(virtual_blocks=64, physical_blocks=1_000_000)
    check("a small pool is not inflated by free device memory",
          p.available_size() == 64)
    p = pool(virtual_blocks=64, physical_blocks=1_000_000, v0=False)
    check("nor on the non-v0 path", p.available_size() == 64)


def test_non_v0_elastic_is_unchanged():
    print("the non-v0 elastic path")
    p = pool(virtual_blocks=1_000_000, physical_blocks=900, v0=False)
    check("still bounded by physical memory", p.available_size() == 900)
    check("still uses its own 0.5 GB reserve", p.calls and p.calls[0] == 0.5)


def test_the_check_is_rate_limited():
    print("cost on the admission path, non-v0")
    p = pool(virtual_blocks=1_000_000, physical_blocks=1_200, v0=False)
    for _ in range(200):
        p.available_size()
    check("200 admission checks do not mean 200 CUDA memory queries",
          len(p.calls) < 200)
    before = len(p.calls)
    time.sleep(mp.PHYSICAL_MEM_CHECK_FREQ * 3)
    p.available_size()
    check("but the reading is refreshed once it is stale",
          len(p.calls) > before)


def test_physical_free_size_is_a_non_negative_count():
    """Below the reserve the raw expression goes negative -- and float."""
    print("physical free size arithmetic")

    class Alloc:
        block_mem_size = 1 << 16

    obj = mp.MHATokenToKVPool.__new__(mp.MHATokenToKVPool)
    obj.layer_num = 32
    obj.kv_allocator = Alloc()

    import torch
    from unittest.mock import patch

    with patch.object(torch.cuda, "mem_get_info", lambda: (1 << 30, 80 << 30)):
        size = obj._physical_free_size(6.459)   # reserve exceeds free memory
    check("less free memory than the reserve means zero admissible blocks",
          size == 0)
    check("and the result is an int, not a float", isinstance(size, int))

    with patch.object(torch.cuda, "mem_get_info", lambda: (40 << 30, 80 << 30)):
        size = obj._physical_free_size(6.459)
    check("a GPU with real headroom still reports blocks",
          isinstance(size, int) and size > 0)


def test_non_elastic_is_untouched():
    print("the non-elastic path")
    p = pool(virtual_blocks=10, physical_blocks=10, elastic=False)
    p.free_slots = list(range(7))
    check("falls through to the base pool", p.available_size() == 7)
    check("and never queries physical memory", not p.calls)


def main():
    test_v0_is_pool_local()
    test_the_allocator_still_binds_when_it_is_smaller()
    test_non_v0_elastic_is_unchanged()
    test_the_check_is_rate_limited()
    test_physical_free_size_is_a_non_negative_count()
    test_non_elastic_is_untouched()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for name in FAIL:
            print(f"  FAILED: {name}")
        raise SystemExit(1)
    print("ALL ELASTIC MEMORY ADMISSION TESTS PASSED")


if __name__ == "__main__":
    main()
