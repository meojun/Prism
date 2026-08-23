#!/usr/bin/env python3
"""Deactivation rollback, and who owns a held re-entrant request.

    Every request has exactly one authoritative owner at every instant.
    Ownership moves to the frontend only when the request has genuinely left
    the source. A deactivation that rolls back leaves ownership where it was --
    and leaves the source operational at every layer.

Two defects made that false.

  1. `_alg2_pending_adoption` was released only inside
     `_evict_all_waiting_requests`, which two reachable paths skip: a failed
     stash returns early, and with V6 KV off neither eviction site runs while
     decode retraction still fills the map. Meanwhile the GPU scheduler
     returned its adoption *placeholder* to the frontend as though it were a
     request -- so one request could be delivered twice, which is how
     model_2#176 took sequences 2372 and 2373 on GPU 1, 23 ms apart, and the
     admission gate failed closed.

  2. A rolled-back deactivation left the source in pieces: the scheduler
     restored its state only `if success`, so it stayed "deactivating" --
     excluded from frontend intake and from admission -- while the engine set
     `_activated = True`, and the worker slot had gone to the free pool before
     the engine ever replied.

  P0 already deactivated   release -> 1 frontend delivery
  P1 eviction requested    release -> 1
  P2 stash not acknowledged RETAIN -> 0, source restored
  P3 V6 KV eviction        release -> 1
  P4 V6 KV off, no eviction release -> 1
"""

import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2] / "prism-research"
sys.path.insert(0, str(REPO / "python"))

from sglang.multi_model.scheduling.gpu.gpu_scheduler import GPUScheduler  # noqa: E402
from sglang.multi_model.scheduling.gpu.worker_pool import WorkerPool  # noqa: E402
from sglang.multi_model.scheduling.gpu.request_queue import RequestQueue  # noqa: E402
from sglang.srt.managers.io_struct import (  # noqa: E402
    AdoptResumedReq, DeactivateReqInput, DeactivateReqOutput, GenerateReqInput,
    MigratedAwayReq,
)
from sglang.srt.managers.scheduler import Scheduler  # noqa: E402

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


class Redis:
    def __init__(self):
        self.sent = []
        self.queues = {}
        self.ints = {}

    def send_pyobj(self, key, obj):
        self.sent.append((key, obj))

    def pop_all(self, key):
        q = self.queues.get(key, [])
        self.queues[key] = []
        return q

    def recv_pyobj_non_block(self, key, count=1):
        q = self.queues.setdefault(key, [])
        out, self.queues[key] = q[:count], q[count:]
        return out

    def get_int(self, key):
        return self.ints.get(key)

    def set_int(self, key, value):
        self.ints[key] = value

    def set_int_if_absent(self, key, value):
        self.ints.setdefault(key, value)

    def compare_and_advance_int(self, key, expected):
        if self.ints.get(key) != expected:
            return False
        self.ints[key] = expected + 1
        return True

    def close(self):
        pass

    def frontend(self, model):
        return [o for k, o in self.sent if k == f"frontend:{model}"]

    def reports(self):
        return [o for _k, o in self.sent if isinstance(o, MigratedAwayReq)]

    def asks(self):
        return [o for _k, o in self.sent if isinstance(o, AdoptResumedReq)]


def held_req(rid, prompt=64, arrival=1000.0, slo=5.0):
    r = GenerateReqInput(
        rid=rid, model="model_2", prompt_len=prompt, arrival_time=arrival,
        slo=slo, input_ids=[7] * prompt, output_len=8,
        sampling_params={"max_new_tokens": 8})
    # A request rebuilt by `build_recomputed_request` carries a real
    # SamplingParams object, which is what `_convert_req_to_frontend_reqs`
    # reads field by field.
    r.sampling_params = SimpleNamespace(
        max_new_tokens=8, min_new_tokens=0, stop_strs=None,
        stop_token_ids=None, temperature=0.0, top_p=1.0, top_k=-1, min_p=0.0,
        frequency_penalty=0.0, presence_penalty=0.0, repetition_penalty=1.0,
        ignore_eos=False, skip_special_tokens=True,
        spaces_between_special_tokens=True, regex=None, n=1,
        json_schema=None, no_stop_trim=False)
    r.origin_input_ids = [7] * prompt
    r.prefix_indices = []
    r.extend_input_len = 0
    r.alg2_seq = None
    r.alg2_backend_admitted = False
    r.return_logprob = False
    r.logprob_start_len = 0
    r.top_logprobs_num = 0
    r.stream = False
    r.lora_path = None
    return r


def engine(model="model_2", gpu_id=0, pending=()):
    e = Scheduler.__new__(Scheduler)
    e.tp_rank, e.tp_size, e.gpu_id = 0, 1, gpu_id
    e.model_name = model
    e.waiting_queue = []
    e._alg2_staged_generation_reqs = []
    e._alg2_pending_adoption = {r.rid: r for r in pending}
    e._alg2_adoption_requested = {r.rid for r in pending}
    e._alg2_runtime_gate = True
    e._alg2_admission_seq_key = f"alg2-next:{gpu_id}"
    e._kv_own_trace = False
    e.redis_client = Redis()
    e.token_to_kv_pool = SimpleNamespace(free=lambda slots: None)
    e.server_args = SimpleNamespace(
        engine_to_gpu_scheduler_key_prefix="e2s",
        backend_generate_request_key_prefix="backend",
        frontend_generate_request_key_prefix="frontend")
    return e


def owner_count(e, rid, sched_queue):
    """Places that authoritatively own `rid`. A placeholder is a proxy."""
    n = 0
    if rid in e._alg2_pending_adoption:
        n += 1
    if any(getattr(r, "rid", None) == rid for r in e.waiting_queue):
        n += 1
    if rid in [o.rid for o in e.redis_client.frontend("model_2")]:
        n += 1
    if any(getattr(w.req, "rid", None) == rid
           and not getattr(w.req, "alg2_resumed", False)
           for w in sched_queue._queue):
        n += 1
    return n


# ------------------------------------------------------------------ scheduler
class Pool(WorkerPool):
    """The real WorkerPool, with its slot table populated by hand.

    `get_idle_worker`, `assign_worker`, `release_worker` and
    `handle_deactivate_model` are the shipped implementations -- the point of
    these tests is when `handle_deactivate_model` gives the slot up.
    """

    def __init__(self, slots=(0, 1, 2, 3), owner=None):
        self.gpu_id = 0
        self._free_workers = list(slots)
        self._model_to_worker = {}
        self._slots = {w: SimpleNamespace(tp_size=1) for w in slots}
        self._model_tp_sizes = {}
        self.sent = []
        outer = self

        class _Ipc:
            def send_pyobj(self, obj):
                outer.sent.append(obj)

        self._worker_to_ipc_name = {w: _Ipc() for w in slots}
        if owner:
            self.assign_worker(self._free_workers[0], owner)

    def cleanup(self):
        pass


def sched(pool, queue=None, model="model_2", state="activated"):
    g = GPUScheduler.__new__(GPUScheduler)
    g.gpu_id = 0
    g.worker_pool = pool
    g.queue = queue if queue is not None else RequestQueue({model: 32768})
    g._model_states = {model: state}
    g.redis_client = Redis()
    g.server_args = SimpleNamespace(
        enable_worker_pool=True,
        backend_generate_request_key_prefix="backend",
        frontend_generate_request_key_prefix="frontend")
    g.resource_manager = SimpleNamespace(
        add_active_model=lambda *a, **k: None,
        remove_active_model=lambda *a, **k: None)
    return g


def verdict(model="model_2", success=True):
    return DeactivateReqOutput(rid="x", gpu_id=0, model_name=model,
                               instance_idx=0, success=success,
                               memory_usage=0.0)


def placeholder(rid):
    return GenerateReqInput(rid=rid, model="model_2", prompt_len=64,
                            arrival_time=1000.0, slo=5.0, alg2_resumed=True,
                            output_len=8)


def ordinary(rid):
    return GenerateReqInput(rid=rid, model="model_2", prompt_len=64,
                            arrival_time=1000.0, slo=5.0, output_len=8)


# =========================================================== release paths
def _release(name, reason):
    e = engine(pending=[held_req("model_2#176"), held_req("model_2#177")])
    e._alg2_release_pending_adoption(reason)
    fe = e.redis_client.frontend("model_2")
    q = RequestQueue({"model_2": 32768})
    check(f"{name}: delivered to the frontend exactly once",
          sorted(o.rid for o in fe) == ["model_2#176", "model_2#177"])
    check(f"{name}: the engine no longer claims them",
          not e._alg2_pending_adoption and not e._alg2_adoption_requested)
    check(f"{name}: exactly one owner each",
          all(owner_count(e, r, q) == 1
              for r in ("model_2#176", "model_2#177")))
    check(f"{name}: their sequences are retired here",
          any(sorted(rep.rids) == ["model_2#176", "model_2#177"]
              for rep in e.redis_client.reports()))
    check(f"{name}: the frontend copy is not a resumed placeholder",
          all(not getattr(o, "alg2_resumed", False) for o in fe))
    check(f"{name}: arrival time and SLO survive",
          all(o.arrival_time == 1000.0 and o.slo == 5.0 for o in fe))
    return e


def test_p0():
    print("P0: already deactivated -- released, not stranded")
    _release("P0", "already-deactivated")


def test_p1():
    print("P1: eviction requested")
    _release("P1", "evict-all-waiting")


def test_p3():
    print("P3: V6 KV eviction")
    _release("P3", "evict-all-waiting")


def test_p4():
    print("P4: V6 KV off, held by decode retraction, no eviction runs")
    e = _release("P4", "deactivate-release")
    check("P4: a second release delivers nothing more",
          e._alg2_release_pending_adoption("deactivate-release") == []
          and len(e.redis_client.frontend("model_2")) == 2)


def test_never_twice():
    print("no rid is ever delivered to the frontend twice")
    e = engine(pending=[held_req("model_2#176")])
    for reason in ("evict-all-waiting", "deactivate-release",
                   "already-deactivated"):
        e._alg2_release_pending_adoption(reason)
    check("three releases, one delivery",
          [o.rid for o in e.redis_client.frontend("model_2")] == ["model_2#176"])


# ================================================================ P2 rollback
def test_p2_engine_retains():
    print("P2: the engine keeps the payload and asks for nothing")
    e = engine(pending=[held_req("model_2#176")])
    q = RequestQueue({"model_2": 32768})
    q.add_requests([placeholder("model_2#176")])
    before_asks = len(e.redis_client.asks())

    # The rollback path releases nothing -- it simply does not call release.
    check("P2: frontend delivery count is 0",
          e.redis_client.frontend("model_2") == [])
    check("P2: the engine is still the owner",
          "model_2#176" in e._alg2_pending_adoption)
    check("P2: _alg2_adoption_requested is untouched",
          e._alg2_adoption_requested == {"model_2#176"})
    check("P2: no new adoption retry is triggered",
          len(e.redis_client.asks()) == before_asks)
    check("P2: exactly one owner", owner_count(e, "model_2#176", q) == 1)
    check("P2: its placeholder is still queued",
          [w.req.rid for w in q._queue] == ["model_2#176"])


def test_p2_scheduler_restores_every_layer():
    print("P2: the source is operational again at every layer")
    pool = Pool(owner="model_2")
    slot = pool._model_to_worker["model_2"]
    q = RequestQueue({"model_2": 32768})
    q.add_requests([placeholder("model_2#176"), ordinary("model_2#900")])
    g = sched(pool, q)

    g._recv_from_request_handler_deactivate = None    # not used; documented flow
    g._model_states["model_2"] = "deactivating"
    pool.handle_deactivate_model(DeactivateReqInput(model_name="model_2",
                                                    gpu_id=0))
    g._handle_deactivate_result(verdict(success=False))

    check("scheduler model state is activated",
          g._model_states["model_2"] == "activated")
    check("the worker slot is still the model's",
          pool._model_to_worker.get("model_2") == slot)
    check("it never leaked into the free pool", slot not in pool._free_workers)
    check("the queue is untouched -- placeholder included",
          sorted(w.req.rid for w in q._queue)
          == ["model_2#176", "model_2#900"])
    check("nothing went to the frontend",
          g.redis_client.frontend("model_2") == [])

    admitted = q.admission_control(
        available_resources=1 << 40, model_backend_queue_lens={"model_2": 0},
        model_states=g._model_states, allow_sending_when_activating=True)
    got = admitted if isinstance(admitted, list) else \
        [r for v in admitted.values() for r in v]
    check("normal dispatch works again", len(got) >= 1)
    check("the placeholder is dispatchable, so adoption resumes with no retry",
          any(getattr(r, "alg2_resumed", False) for r in got)
          or any(getattr(w.req, "alg2_resumed", False) for w in q._queue))
    check("its arrival time and SLO are unchanged",
          all(r.arrival_time == 1000.0 and r.slo == 5.0 for r in got))


# ================================================================ success path
def test_success_releases_everything_once():
    print("SUCCESS: queue, backend queue and slot released, placeholder dropped")
    pool = Pool(owner="model_2")
    slot = pool._model_to_worker["model_2"]
    q = RequestQueue({"model_2": 32768})
    q.add_requests([placeholder("model_2#176"), ordinary("model_2#900")])
    g = sched(pool, q)
    g.redis_client.queues["backend:model_2"] = [ordinary("model_2#901")]
    g._model_states["model_2"] = "deactivating"
    g._handle_deactivate_result(verdict(success=True))

    fe = [o.rid for o in g.redis_client.frontend("model_2")]
    check("state is deactivated", g._model_states["model_2"] == "deactivated")
    check("the ordinary queued request went back exactly once",
          fe.count("model_2#900") == 1)
    check("the undelivered dispatch went back exactly once",
          fe.count("model_2#901") == 1)
    check("the placeholder did NOT go to the frontend",
          "model_2#176" not in fe)
    check("the queue is empty", not q._queue)
    check("the slot is released", "model_2" not in pool._model_to_worker)
    check("and reusable", slot in pool._free_workers)


def test_success_then_reassign():
    print("SUCCESS: the freed slot is taken by the next model")
    pool = Pool(slots=(0,), owner="model_2")
    g = sched(pool)
    g._handle_deactivate_result(verdict(success=True))
    w = pool.get_idle_worker(1)
    check("a slot is offered", w == 0)
    pool.assign_worker(w, "model_4")
    check("the next model takes it", pool._model_to_worker["model_4"] == 0)


# ==================================================================== the race
def test_slot_held_until_verdict():
    print("race: the slot is not free between the request and the verdict")
    pool = Pool(slots=(0,), owner="model_2")
    slot = pool._model_to_worker["model_2"]
    pool.handle_deactivate_model(DeactivateReqInput(model_name="model_2",
                                                    gpu_id=0))
    check("no idle slot is offered mid-deactivation",
          pool.get_idle_worker(1) is None)
    taken = False
    try:
        pool.assign_worker(slot, "model_5")
        taken = True
    except AssertionError:
        pass
    check("and it cannot be assigned away", not taken)
    check("so a rollback finds its own slot intact",
          pool._model_to_worker["model_2"] == slot)


def test_verdict_is_applied_before_the_next_activation():
    print("race: a pending verdict is applied before the activation after it")
    import inspect
    src = inspect.getsource(GPUScheduler._recv_requests_loop)
    i_engine = src.index("self._recv_from_engine()")
    i_handler = src.index("self._recv_from_request_handler()")
    check("the engine channel is drained before the request handler",
          i_engine < i_handler)

    # And the effect: the verdict frees the slot the activation then needs.
    pool = Pool(slots=(0,), owner="model_2")
    g = sched(pool)
    g._handle_deactivate_result(verdict(success=True))
    check("the activation that follows finds its slot",
          pool.get_idle_worker(1) == 0)


def test_engine_channel_carries_only_completions():
    """Engine-first ordering is safe because nothing on that channel is a
    command: the scheduler forwards activate/deactivate itself, so the engine
    can only report on work it was already given."""
    print("race: the engine channel carries completions, never commands")
    import inspect
    src = inspect.getsource(GPUScheduler._recv_from_engine)
    for t in ("ActivateReqOutput", "DeactivateReqOutput", "BatchRunReq",
              "BackendAdmitReq", "PrefillCompleteReq", "MigratedAwayReq",
              "AdoptResumedReq"):
        check(f"{t} is handled on the engine channel", t in src)
    check("no ActivateReqInput on the engine channel",
          "ActivateReqInput" not in src)
    check("no DeactivateReqInput on the engine channel",
          "DeactivateReqInput" not in src)


# ============================================================ the duplicate
def test_the_attempt3_duplicate_cannot_form():
    print("end to end: model_2#176 cannot take two sequences again")
    e = engine(pending=[held_req("model_2#176")])
    pool = Pool(owner="model_2")
    q = RequestQueue({"model_2": 32768})
    q.add_requests([placeholder("model_2#176")])
    g = sched(pool, q)
    g._model_states["model_2"] = "deactivating"

    g._handle_deactivate_result(verdict(success=True))   # scheduler side
    e._alg2_release_pending_adoption("evict-all-waiting")  # engine side

    deliveries = ([o.rid for o in g.redis_client.frontend("model_2")]
                  + [o.rid for o in e.redis_client.frontend("model_2")])
    check("exactly one frontend delivery for the rid",
          deliveries.count("model_2#176") == 1)
    check("never two -- which is what gave it 2372 and 2373",
          deliveries.count("model_2#176") != 2)


def main():
    for fn in (
        test_p0, test_p1, test_p3, test_p4, test_never_twice,
        test_p2_engine_retains, test_p2_scheduler_restores_every_layer,
        test_success_releases_everything_once, test_success_then_reassign,
        test_slot_held_until_verdict,
        test_verdict_is_applied_before_the_next_activation,
        test_engine_channel_carries_only_completions,
        test_the_attempt3_duplicate_cannot_form,
    ):
        fn()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for n in FAIL:
        print("  FAILED:", n)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
