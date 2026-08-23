#!/usr/bin/env python3
"""GPU-scoped backend queues, and the two stalls they make impossible.

`alg2_seq` is per GPU; the backend queue was per model and shared by every GPU.
Any drain of `backend:<model>` removed entries another GPU had just dispatched
and was still accounting for, and neither drain path could retire a sequence on
the issuing GPU's ledger:

  seq 2060   GPU0 dispatched model_1#166 at 14:39:05.702; GPU1's deactivation
             verdict popped backend:model_1 between .679 and .714. Owner count
             1 -> 0. GPU0's frontier stopped there for the rest of the run.
  seq 1159   GPU0 dispatched model_4#140 at 01:22:58.376 and its own
  and 1160   deactivation drained the same key at .377, returning 65 requests to
             the frontend. Same violation, same GPU rather than the other one.

The key is now `{prefix}:{gpu_id}:{model}`, so an entry issued by GPU g is
unreachable by any other GPU. These tests hold that line.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2] / "prism-research"
sys.path.insert(0, str(REPO / "python"))

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


class Redis:
    """Enough of the client to observe which keys are touched."""

    def __init__(self):
        self.q = {}
        self.sent = []
        self.touched = []

    def send_pyobj(self, key, obj):
        self.touched.append(("push", key))
        if key.startswith("frontend"):
            self.sent.append((key, obj))
        else:
            self.q.setdefault(key, []).append(obj)

    def pop_all(self, key):
        self.touched.append(("pop_all", key))
        out, self.q[key] = self.q.get(key, []), []
        return out

    def recv_pyobj_non_block(self, key, count=1):
        self.touched.append(("recv", key))
        q = self.q.setdefault(key, [])
        out, self.q[key] = q[:count], q[count:]
        return out

    def get_queue_length(self, key):
        self.touched.append(("len", key))
        return len(self.q.get(key, []))

    def get_int(self, key):
        return None

    def close(self):
        pass


def reports_to(redis, gpu):
    """MigratedAwayReq objects routed to a specific GPU's ledger key."""
    return [o for o in redis.q.get(f"e2s:{gpu}", [])
            if type(o).__name__ == "MigratedAwayReq"]


def req(rid, model, seq):
    return SimpleNamespace(rid=rid, model=model, alg2_seq=seq,
                           gpu_scheduler_dispatch_time=None)


PREFIX = "backend_generate_request_q"


def key(gpu, model):
    return f"{PREFIX}:{gpu}:{model}"


# ------------------------------------------------------------- the schedulers
from sglang.multi_model.scheduling.gpu.gpu_scheduler import GPUScheduler  # noqa: E402
from sglang.srt.managers.scheduler import Scheduler  # noqa: E402


def sched(gpu_id, redis, model="model_1"):
    g = GPUScheduler.__new__(GPUScheduler)
    import threading
    g.gpu_id = gpu_id
    g.redis_client = redis
    # The configuration under test: Algorithm 2's runtime gate on, which is how
    # every calibration and evaluation run is launched.
    g._mh_runtime_gate = True
    g._mh_gate_lock = threading.Lock()
    g._mh_outstanding_prefills = {}
    g._model_states = {model: "activated"}
    g.server_args = SimpleNamespace(
        backend_generate_request_key_prefix=PREFIX,
        frontend_generate_request_key_prefix="frontend",
        enable_worker_pool=False)
    g.queue = SimpleNamespace(pop_model_requests=lambda m: [])
    g.resource_manager = SimpleNamespace(add_active_model=lambda *a, **k: None,
                                         remove_active_model=lambda *a, **k: None)
    g.worker_pool = None
    return g


def engine(gpu_id, redis, model="model_1"):
    e = Scheduler.__new__(Scheduler)
    e.gpu_id, e.model_name, e.tp_rank, e.tp_size = gpu_id, model, 0, 1
    e.redis_client = redis
    e.waiting_queue = []
    e._alg2_staged_generation_reqs = []
    e._alg2_pending_adoption = {}
    e._alg2_adoption_requested = set()
    e._alg2_runtime_gate = True
    e._alg2_admission_seq_key = f"alg2-next:{gpu_id}"
    e._kv_own_trace = False
    e.token_to_kv_pool = SimpleNamespace(free=lambda s: None)
    e.server_args = SimpleNamespace(
        backend_generate_request_key_prefix=PREFIX,
        frontend_generate_request_key_prefix="frontend",
        engine_to_gpu_scheduler_key_prefix="e2s")
    return e


def enqueue(gpu_scheduler, reqs):
    """Dispatch, exactly as the scheduler's loop does: record the sequence as
    outstanding on this GPU, then send to the backend queue."""
    for r in reqs:
        gpu_scheduler._mh_outstanding_prefills[r.rid] = {
            "seq": r.alg2_seq, "rid": r.rid, "model": r.model,
            "dispatch_time": 0.0, "backend_admit_time": None,
            "start_time": None, "predicted_exec_s": 0.01,
            "backend_admitted": False, "started": False,
        }
    gpu_scheduler._send_to_backend_queue(reqs)


# ============================================================ 1. key isolation
def test_each_gpu_writes_its_own_key():
    print("1. key isolation: each GPU enqueues into its own key")
    r = Redis()
    enqueue(sched(0, r), [req("model_1#a", "model_1", 100)])
    enqueue(sched(1, r), [req("model_1#b", "model_1", 200)])
    check("GPU0 key holds only its request",
          [x.rid for x in r.q.get(key(0, "model_1"), [])] == ["model_1#a"])
    check("GPU1 key holds only its request",
          [x.rid for x in r.q.get(key(1, "model_1"), [])] == ["model_1#b"])
    check("no shared per-model key was created",
          f"{PREFIX}:model_1" not in r.q)


def test_fetch_cannot_reach_the_other_gpu():
    print("1. key isolation: fetch")
    r = Redis()
    enqueue(sched(0, r), [req("model_1#a", "model_1", 100)])
    enqueue(sched(1, r), [req("model_1#b", "model_1", 200)])
    got = engine(0, r).redis_client.recv_pyobj_non_block(key(0, "model_1"), 10)
    check("GPU0 fetched only its own", [x.rid for x in got] == ["model_1#a"])
    check("GPU1's entry is untouched",
          [x.rid for x in r.q[key(1, "model_1")]] == ["model_1#b"])


def test_drain_cannot_reach_the_other_gpu():
    print("1. key isolation: engine backend drain")
    r = Redis()
    enqueue(sched(0, r), [req("model_1#a", "model_1", 100)])
    enqueue(sched(1, r), [req("model_1#b", "model_1", 200)])
    engine(0, r)._drain_backend_queue()
    check("GPU1's entry survives GPU0's drain",
          [x.rid for x in r.q.get(key(1, "model_1"), [])] == ["model_1#b"])
    check("GPU0's own entry was drained",
          r.q.get(key(0, "model_1"), []) == [])
    keys = {k for op, k in r.touched if op in ("recv", "pop_all")}
    check("GPU0 never named GPU1's key", key(1, "model_1") not in keys)


def test_pop_all_cannot_reach_the_other_gpu():
    print("1. key isolation: deactivation pop_all -- the seq 2060 path")
    r = Redis()
    enqueue(sched(0, r), [req("model_1#a", "model_1", 100)])
    enqueue(sched(1, r), [req("model_1#b", "model_1", 200)])
    g1 = sched(1, r)
    g1._handle_deactivate_result(SimpleNamespace(
        model_name="model_1", instance_idx=0, success=True))
    check("GPU0's entry survives GPU1's deactivation",
          [x.rid for x in r.q.get(key(0, "model_1"), [])] == ["model_1#a"])
    check("GPU1 drained only its own",
          r.q.get(key(1, "model_1"), []) == [])
    keys = {k for op, k in r.touched if op == "pop_all"}
    check("GPU1 never named GPU0's key", key(0, "model_1") not in keys)


def test_queue_length_probe_is_gpu_local():
    print("1. key isolation: the queue-length probe reads this GPU only")
    r = Redis()
    for i in range(5):
        enqueue(sched(1, r), [req(f"model_1#{i}", "model_1", 200 + i)])
    check("GPU0 sees an empty backlog",
          r.get_queue_length(key(0, "model_1")) == 0)
    check("GPU1 sees its five", r.get_queue_length(key(1, "model_1")) == 5)


# =================================================== 2. seq ownership regression
def test_case_a_cross_gpu_deactivation_cannot_orphan():
    """seq 2060: GPU0 enqueues while GPU1 deactivates the same model."""
    print("2. Case A: GPU0 enqueue overlapped with GPU1 same-model pop_all")
    r = Redis()
    g0, g1 = sched(0, r), sched(1, r)
    enqueue(g0, [req("model_1#166", "model_1", 2060)])          # the dispatch
    g1._handle_deactivate_result(SimpleNamespace(               # the race
        model_name="model_1", instance_idx=0, success=True))
    survivors = [x.alg2_seq for x in r.q.get(key(0, "model_1"), [])]
    check("seq 2060 is still in GPU0's queue", survivors == [2060])
    check("it was not returned to the frontend by GPU1",
          not any(getattr(o, "alg2_seq", None) == 2060 for _k, o in r.sent))
    # It can still be consumed by its own engine -- the point of keeping it.
    got = r.recv_pyobj_non_block(key(0, "model_1"), 10)
    check("GPU0's engine can still fetch it",
          [x.alg2_seq for x in got] == [2060])


def test_case_b_same_gpu_drain_still_retires_properly():
    """seqs 1159/1160: the same GPU dispatches and then drains."""
    print("2. Case B: same-GPU dispatch overlapped with its own drain")
    r = Redis()
    g0 = sched(0, r)
    enqueue(g0, [req("model_4#140", "model_4", 1159),
                 req("model_4#141", "model_4", 1160)])
    e0 = engine(0, r, "model_4")
    e0._drain_backend_queue()
    check("GPU0's own drain took them", r.q.get(key(0, "model_4"), []) == [])
    returned = [o for k_, o in r.sent if k_.startswith("frontend")]
    check("both went back to the frontend", len(returned) == 2)
    # And the drain reports to GPU0's ledger -- the issuing one, because it is
    # the same GPU. That is what makes retirement possible at all.
    reports = reports_to(r, 0)
    check("the drain reported them for retirement",
          any(sorted(getattr(x, "rids", [])) == ["model_4#140", "model_4#141"]
              for x in reports))
    check("and reported to GPU0's ledger -- the issuing one",
          reports and not reports_to(r, 1))


def test_the_other_gpu_can_never_be_the_actor():
    """The structural claim: actor GPU and issuing GPU can no longer differ."""
    print("2. actor GPU and issuing GPU can no longer differ")
    r = Redis()
    enqueue(sched(0, r), [req("x", "model_1", 500)])
    for gpu in (1,):
        sched(gpu, r)._handle_deactivate_result(SimpleNamespace(
            model_name="model_1", instance_idx=0, success=True))
        engine(gpu, r)._drain_backend_queue()
    check("seq 500 survived every other GPU's cleanup",
          [x.alg2_seq for x in r.q.get(key(0, "model_1"), [])] == [500])
    foreign = {k for op, k in r.touched
               if op in ("pop_all", "recv") and k == key(0, "model_1")}
    check("no foreign actor ever named GPU0's key", not foreign)


# ==================================================== 3. lifecycle invariant
def test_no_issued_seq_is_left_without_an_outcome():
    print("3. invariant: every issued seq ends completed or retired")
    r = Redis()
    g0 = sched(0, r)
    issued = [1159, 1160, 2060]
    enqueue(g0, [req(f"r{n}", "model_1", n) for n in issued])

    # Another GPU does everything it can to interfere.
    sched(1, r)._handle_deactivate_result(SimpleNamespace(
        model_name="model_1", instance_idx=0, success=True))
    engine(1, r, "model_1")._drain_backend_queue()

    still_owned = [x.alg2_seq for x in r.q.get(key(0, "model_1"), [])]
    check("all three are still owned by GPU0", sorted(still_owned) == issued)

    # GPU0 then resolves them itself: one consumed, two drained and reported.
    consumed = r.recv_pyobj_non_block(key(0, "model_1"), 1)
    engine(0, r, "model_1")._drain_backend_queue()
    reported = set()
    for o in reports_to(r, 0):
        reported.update(getattr(o, "rids", []))
    outcomes = {x.alg2_seq for x in consumed} | {
        int(rid[1:]) for rid in reported if rid.startswith("r")}
    check("each issued seq has exactly one outcome", outcomes == set(issued))
    check("nothing is left orphaned in the queue",
          r.q.get(key(0, "model_1"), []) == [])


# ======================================================== 4. migration semantics
def test_migration_still_reissues_a_target_local_seq():
    print("4. migration: the target issues its own sequence, as before")
    r = Redis()
    # Source dispatched it once.
    enqueue(sched(0, r), [req("model_4#140", "model_4", 1159)])
    # After migrating, the target dispatches the same rid with ITS sequence.
    enqueue(sched(1, r), [req("model_4#140", "model_4", 2110)])
    check("the source entry stayed in the source's queue",
          [x.alg2_seq for x in r.q[key(0, "model_4")]] == [1159])
    check("the target entry is in the target's queue",
          [x.alg2_seq for x in r.q[key(1, "model_4")]] == [2110])
    check("the two sequences are independent, as the paper's per-GPU "
          "Algorithm 2 requires", 1159 != 2110)


def test_adoption_does_not_touch_any_backend_queue():
    print("4. migration: adoption queues a placeholder, not a backend entry")
    r = Redis()
    before = dict(r.q)
    g1 = sched(1, r)
    g1._mh_runtime_gate = True
    g1._mh_gate_lock = __import__("threading").Lock()
    g1._mh_outstanding_prefills = {}
    from sglang.multi_model.scheduling.gpu.request_queue import RequestQueue
    g1.queue = RequestQueue({"model_4": 32768})
    from sglang.srt.managers.io_struct import AdoptResumedReq
    g1._handle_mh_adopt_resumed(AdoptResumedReq(
        rids=["model_4#140"], model="model_4", gpu_id=1, prefill_tokens=[10],
        arrival_times=[1000.0], slos=[5.0], request_time=0.0))
    check("no backend key was written", r.q == before)
    check("the placeholder is in the target's own queue",
          [w.req.rid for w in g1.queue._queue] == ["model_4#140"])


def main():
    for fn in (
        test_each_gpu_writes_its_own_key,
        test_fetch_cannot_reach_the_other_gpu,
        test_drain_cannot_reach_the_other_gpu,
        test_pop_all_cannot_reach_the_other_gpu,
        test_queue_length_probe_is_gpu_local,
        test_case_a_cross_gpu_deactivation_cannot_orphan,
        test_case_b_same_gpu_drain_still_retires_properly,
        test_the_other_gpu_can_never_be_the_actor,
        test_no_issued_seq_is_left_without_an_outcome,
        test_migration_still_reissues_a_target_local_seq,
        test_adoption_does_not_touch_any_backend_queue,
    ):
        fn()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for n in FAIL:
        print("  FAILED:", n)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
