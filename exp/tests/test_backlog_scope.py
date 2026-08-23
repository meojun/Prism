#!/usr/bin/env python3
"""The backlog probe's scope, and the line it must not cross.

Splitting the backend queue per GPU changed what `models_to_skip` reads: the
cluster-wide depth of `backend:<model>` became this GPU's depth alone. That is a
change to an observation, not to transport, and it is the remaining candidate
for the throughput gap between D3 run 8 (18.49 req/s) and run 10 (13.75).

`PRISM_BACKLOG_SCOPE=global` restores the old quantity by summing the per-GPU
queues that together hold what the shared key used to hold. What must stay true
in both modes: requests live in `{prefix}:{gpu}:{model}` and no GPU consumes
another's entry.
"""

import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2] / "prism-research"
sys.path.insert(0, str(REPO / "python"))

from sglang.multi_model.scheduling.gpu.gpu_scheduler import GPUScheduler  # noqa: E402

PASS, FAIL = [], []
PREFIX = "backend_q"


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


class Redis:
    def __init__(self):
        self.q = {}
        self.reads = []

    def send_pyobj(self, key, obj):
        self.q.setdefault(key, []).append(obj)

    def get_queue_length(self, key):
        self.reads.append(key)
        return len(self.q.get(key, []))

    def pop_all(self, key):
        out, self.q[key] = self.q.get(key, []), []
        return out


def sched(gpu, redis, scope, num_gpus=2):
    g = GPUScheduler.__new__(GPUScheduler)
    g.gpu_id = gpu
    g.redis_client = redis
    g._backlog_scope = scope
    g._mh_runtime_gate = True
    g._mh_gate_lock = threading.Lock()
    g._mh_outstanding_prefills = {}
    g.server_args = SimpleNamespace(
        backend_generate_request_key_prefix=PREFIX, num_gpus=num_gpus,
        frontend_generate_request_key_prefix="frontend")
    return g


def fill(redis, gpu, model, n):
    for i in range(n):
        redis.send_pyobj(f"{PREFIX}:{gpu}:{model}", object())


def test_local_reads_only_this_gpu():
    print("local: this GPU's depth")
    r = Redis()
    fill(r, 0, "model_1", 3)
    fill(r, 1, "model_1", 7)
    check("GPU0 sees 3", sched(0, r, "local")._backend_backlog("model_1") == 3)
    check("GPU1 sees 7", sched(1, r, "local")._backend_backlog("model_1") == 7)


def test_global_reads_the_sum():
    print("global: the sum across GPUs -- the pre-split quantity")
    r = Redis()
    fill(r, 0, "model_1", 3)
    fill(r, 1, "model_1", 7)
    check("GPU0 sees 10", sched(0, r, "global")._backend_backlog("model_1") == 10)
    check("GPU1 also sees 10", sched(1, r, "global")._backend_backlog("model_1") == 10)
    check("both GPUs agree, as they did before the split",
          sched(0, r, "global")._backend_backlog("model_1")
          == sched(1, r, "global")._backend_backlog("model_1"))


def test_global_equals_what_the_shared_key_held():
    """Equivalence: the sum is exactly the old single-key depth."""
    print("global: equals the old shared-key depth exactly")
    r = Redis()
    for gpu, n in ((0, 4), (1, 6)):
        fill(r, gpu, "model_4", n)
    old_shared_depth = 4 + 6          # what backend:model_4 would have held
    check("sum matches the pre-split depth",
          sched(0, r, "global")._backend_backlog("model_4") == old_shared_depth)


def test_global_covers_every_gpu_exactly_once():
    print("global: reads each GPU's key once, and no other key")
    r = Redis()
    fill(r, 0, "model_1", 1)
    sched(0, r, "global", num_gpus=2)._backend_backlog("model_1")
    check("read both GPU keys",
          sorted(r.reads) == [f"{PREFIX}:0:model_1", f"{PREFIX}:1:model_1"])
    check("no shared per-model key was read",
          f"{PREFIX}:model_1" not in r.reads)


def test_scope_never_moves_requests():
    """The line that must not be crossed: transport stays GPU-local."""
    print("neither scope changes where requests live or who may take them")
    for scope in ("local", "global"):
        r = Redis()
        g0 = sched(0, r, scope)
        rq = SimpleNamespace(rid="model_1#1", model="model_1", alg2_seq=1,
                             gpu_scheduler_dispatch_time=None)
        g0._mh_outstanding_prefills[rq.rid] = {
            "seq": 1, "rid": rq.rid, "model": "model_1", "dispatch_time": 0.0,
            "backend_admit_time": None, "start_time": None,
            "predicted_exec_s": 0.01, "backend_admitted": False,
            "started": False}
        g0._send_to_backend_queue([rq])
        check(f"{scope}: the request went to GPU0's own key",
              [k for k in r.q if r.q[k]] == [f"{PREFIX}:0:model_1"])
        check(f"{scope}: no shared per-model key exists",
              f"{PREFIX}:model_1" not in r.q)


def test_default_is_local():
    print("default: local, so A is the current build unchanged")
    saved = os.environ.pop("PRISM_BACKLOG_SCOPE", None)
    try:
        check("unset means local",
              os.environ.get("PRISM_BACKLOG_SCOPE", "local") == "local")
    finally:
        if saved is not None:
            os.environ["PRISM_BACKLOG_SCOPE"] = saved


def test_single_gpu_is_identical_in_both_scopes():
    print("one GPU: the two scopes cannot differ")
    r = Redis()
    fill(r, 0, "model_1", 5)
    check("local == global",
          sched(0, r, "local", num_gpus=1)._backend_backlog("model_1")
          == sched(0, r, "global", num_gpus=1)._backend_backlog("model_1") == 5)


def main():
    for fn in (test_local_reads_only_this_gpu, test_global_reads_the_sum,
               test_global_equals_what_the_shared_key_held,
               test_global_covers_every_gpu_exactly_once,
               test_scope_never_moves_requests, test_default_is_local,
               test_single_gpu_is_identical_in_both_scopes):
        fn()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for n in FAIL:
        print("  FAILED:", n)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
