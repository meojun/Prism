#!/usr/bin/env python3
"""The KV batch must be waited on once, not once per request.

`transfer_capsule` ended every capsule with `torch.cuda.synchronize(target_gpu)`
-- a device-wide barrier that waits for everything queued on the target, not
just the copy it issued. On a GPU that is also serving several other models
that is a barrier against their prefill and decode work.

Measured on this pair (exp/scripts/microbench_kv_migration.py, 2026-08-22),
161 requests / 5.96 GB / identical copies / identical P2P path:

    target idle : 2.33 s, 161 synchronize calls costing 0.002 s   -> 2.56 GB/s
    target busy : 92.1 s, the same 161 calls costing 59.0 s       -> 0.065 GB/s
    weight path : 1.08 s under the same load, one synchronize     -> 5.46 GB/s

So the batch now runs on one stream and is waited on once. These tests pin the
wait count, and -- more importantly -- that deferring the wait did not cost
correctness: the tensors must still arrive bit-exact, having been read from
source memory that is only released after the wait.
"""

import sys
from pathlib import Path

import torch

REPO = Path(__file__).resolve().parents[2] / "prism-research"
sys.path.insert(0, str(REPO / "python"))

from sglang.multi_model import kv_migration_v6 as kvm  # noqa: E402

PASS, FAIL = [], []
SRC, DST = 0, 1
LAYERS, HEADS, DIM, DTYPE = 6, 4, 32, torch.bfloat16


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


def capsule(rid, tokens, seed):
    torch.manual_seed(seed)
    k = [torch.randn(tokens, HEADS, DIM, dtype=DTYPE, device=f"cuda:{SRC}")
         for _ in range(LAYERS)]
    v = [torch.randn(tokens, HEADS, DIM, dtype=DTYPE, device=f"cuda:{SRC}")
         for _ in range(LAYERS)]
    return kvm.RequestKVCapsule(
        rid=rid, model_name="m", origin_input_ids=[1] * tokens, output_ids=[],
        sampling_params=None, arrival_time=0.0, slo=None, k=k, v=v,
        source_gpu=SRC)


class CountSync:
    """Counts device-wide synchronize calls made during the block."""

    def __enter__(self):
        self.calls = []
        self._orig = torch.cuda.synchronize
        torch.cuda.synchronize = lambda device=None: (
            self.calls.append(device), self._orig(device))[1]
        return self

    def __exit__(self, *exc):
        torch.cuda.synchronize = self._orig
        return False


def test_one_wait_per_batch():
    print("synchronization count")
    capsules = [capsule(f"r{i}", 40 + i, seed=i) for i in range(8)]
    with CountSync() as counter:
        moved, skipped, record = kvm.migrate_request_kv(
            capsules, DST, tag="unit/sync")
    check("all eight requests moved", len(moved) == 8 and not skipped)
    check("no device-wide barrier is taken per request",
          len(counter.calls) < len(moved))
    check("the reported path is still gpu-to-gpu-p2p",
          record["transfer_path"] == "gpu-to-gpu-p2p")
    check("bytes are accounted as before",
          record["kv_bytes"] == sum(
              c.num_tokens * LAYERS * HEADS * DIM * 2 * 2 for c in moved))


def test_the_bytes_still_arrive_intact():
    """Deferring the wait must not let source memory be reused mid-copy."""
    print("bit-exactness after a deferred wait")
    expected, capsules = [], []
    for i in range(8):
        cap = capsule(f"r{i}", 64 + 7 * i, seed=100 + i)
        expected.append(([t.clone().cpu() for t in cap.k],
                         [t.clone().cpu() for t in cap.v]))
        capsules.append(cap)

    moved, _skipped, _record = kvm.migrate_request_kv(
        capsules, DST, tag="unit/exact")

    on_target = all(t.device.index == DST for c in moved for t in c.k + c.v)
    check("every tensor is on the target device", on_target)
    exact = all(
        torch.equal(moved[i].k[L].cpu(), expected[i][0][L])
        and torch.equal(moved[i].v[L].cpu(), expected[i][1][L])
        for i in range(len(moved)) for L in range(LAYERS)
    )
    check("every K and V tensor arrives bit-exact", exact)
    check("source_gpu is updated to the target",
          all(c.source_gpu == DST for c in moved))


def test_a_busy_target_does_not_serialize_the_batch():
    """The regression this exists to prevent, in miniature.

    With work queued on the target, a per-request device barrier makes the
    batch wait for that work once per request. One wait for the batch cannot.
    """
    print("a busy target")
    busy = torch.randn(2048, 2048, device=f"cuda:{DST}", dtype=DTYPE)
    capsules = [capsule(f"r{i}", 256, seed=200 + i) for i in range(16)]
    for _ in range(200):
        busy = busy @ busy.T / 1000.0          # queue real work on the target
    with CountSync() as counter:
        moved, _skipped, record = kvm.migrate_request_kv(
            capsules, DST, tag="unit/busy")
    torch.cuda.synchronize(DST)
    check("the batch still completes", len(moved) == 16)
    check("and still takes fewer barriers than it has requests",
          len(counter.calls) < 16)
    check("bandwidth is recorded", record["kv_gbps"] is not None)
    print(f"        {record['kv_bytes']/1e6:.1f} MB in {record['seconds']:.4f}s "
          f"= {record['kv_gbps']} GB/s, {len(counter.calls)} device barriers")


def test_empty_and_capped_batches_still_behave():
    print("edge cases")
    moved, skipped, record = kvm.migrate_request_kv([], DST, tag="unit/empty")
    check("an empty batch is a no-op", not moved and not skipped)
    check("and reports no path", record["transfer_path"] == "none")

    big = capsule("big", kvm.MAX_TOKENS + 1, seed=7)
    moved, skipped, record = kvm.migrate_request_kv([big], DST, tag="unit/cap")
    check("a request over the token cap is skipped, not silently truncated",
          not moved and len(skipped) == 1)
    check("and the skip is reported",
          record["requests_skipped_over_cap"] == 1)


def main():
    if not torch.cuda.is_available() or torch.cuda.device_count() < 2:
        print("SKIPPED: needs two visible GPUs")
        return
    test_one_wait_per_batch()
    test_the_bytes_still_arrive_intact()
    test_a_busy_target_does_not_serialize_the_batch()
    test_empty_and_capped_batches_still_behave()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for name in FAIL:
            print(f"  FAILED: {name}")
        raise SystemExit(1)
    print("ALL KV TRANSFER SYNCHRONIZATION TESTS PASSED")


if __name__ == "__main__":
    main()
