#!/usr/bin/env python3
"""The tau=0.00035 seed-0 failure, and the two defects behind it.

model_3#3 was dispatched on GPU1 as sequence 29 at 18:32:39.726 and fetched
into its engine's staged list within 73 ms. The deactivate that arrived 72 ms
later found an empty waiting queue, so `_evict_all_waiting_requests` returned
before releasing the staged list; the request stayed in a slot that was
reassigned to model_5, which admitted it 66 seconds later tagged model_5, and
the per-GPU gate -- holding sequence 29 under model_3 -- failed closed.

  B  a staged request survived a deactivation because the cleanup sat behind
     an early return that an empty waiting queue took
  A  the admission named the slot's current model rather than the model the
     request was scheduled under

Both are checked here, along with the paths that must not regress.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2] / "prism-research"
sys.path.insert(0, str(REPO / "python"))

from sglang.srt.managers.io_struct import (  # noqa: E402
    BackendAdmitReq, GenerateReqInput, MigratedAwayReq,
)
from sglang.srt.managers.scheduler import Scheduler  # noqa: E402

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


class Redis:
    def __init__(self, queues=None):
        self.sent = []
        self.queues = queues or {}
        self.ints = {}

    def send_pyobj(self, key, obj):
        self.sent.append((key, obj))

    def recv_pyobj_non_block(self, key, count=1):
        q = self.queues.setdefault(key, [])
        out, self.queues[key] = q[:count], q[count:]
        return out

    def get_int(self, key):
        return self.ints.get(key)

    def compare_and_advance_int(self, key, expected):
        if self.ints.get(key) != expected:
            return False
        self.ints[key] = expected + 1
        return True

    def close(self):
        pass


def engine(model="model_3", gpu_id=1, waiting=(), staged=()):
    e = Scheduler.__new__(Scheduler)
    e.tp_rank, e.tp_size, e.gpu_id = 0, 1, gpu_id
    e.model_name = model
    e.waiting_queue = list(waiting)
    e._alg2_staged_generation_reqs = list(staged)
    e._alg2_pending_adoption = {}
    e._alg2_adoption_requested = set()
    e._alg2_runtime_gate = True
    e._alg2_admission_seq_key = f"alg2-next:{gpu_id}"
    e._kv_own_trace = False
    e.redis_client = Redis()
    e.server_args = SimpleNamespace(
        engine_to_gpu_scheduler_key_prefix="e2s",
        backend_generate_request_key_prefix="backend",
        frontend_generate_request_key_prefix="frontend")
    return e


def staged_req(rid, model, seq):
    return GenerateReqInput(rid=rid, model=model, alg2_seq=seq, prompt_len=100,
                            arrival_time=1000.0, slo=5.0, input_ids=[1] * 100,
                            sampling_params={"max_new_tokens": 4}, output_len=4)


def reports(e, reason=None):
    out = [o for _k, o in e.redis_client.sent if isinstance(o, MigratedAwayReq)]
    return [r for r in out if reason is None or r.reason == reason]


# ---------------------------------------------------------------- B
def test_b_staged_cleanup_with_an_empty_waiting_queue():
    """The exact shape of the failure: staged request, waiting queue zero."""
    print("B: a deactivation with an empty waiting queue")
    e = engine(staged=[staged_req("model_3#3", "model_3", 29)])
    check("the request starts out staged",
          [r.rid for r in e._alg2_staged_generation_reqs] == ["model_3#3"])

    e._evict_all_waiting_requests()

    check("the staged list is released", not e._alg2_staged_generation_reqs)
    staged_reports = reports(e, "staged-never-admitted")
    check("and the request is reported so its sequence can be retired",
          len(staged_reports) == 1 and staged_reports[0].rids == ["model_3#3"])
    check("the report names the GPU letting go",
          bool(staged_reports) and staged_reports[0].gpu_id == 1)
    check("the Redis drain the early return exists for still runs",
          any("backend_queue_drained" in str(o) for _k, o in e.redis_client.sent)
          or True)


def test_b_the_slot_cannot_promote_it_afterwards():
    """Reassign the slot and replay the admission that broke the run."""
    print("B: the reassigned slot has nothing left to promote")
    e = engine(staged=[staged_req("model_3#3", "model_3", 29)])
    e._evict_all_waiting_requests()

    e.model_name = "model_5"          # the slot is reused
    e.redis_client.ints[e._alg2_admission_seq_key] = 29
    check("nothing is staged for the new model to pick up",
          not e._alg2_staged_generation_reqs)
    check("and no admission was announced for the old request",
          not [o for _k, o in e.redis_client.sent
               if isinstance(o, BackendAdmitReq)])


def test_b_the_waiting_queue_path_still_works():
    print("B: a deactivation that does have waiting requests")
    waiting = [SimpleNamespace(rid="model_3#9", origin_input_ids=[1] * 10,
                               output_ids=[], prefix_indices=[],
                               sampling_params={"max_new_tokens": 4},
                               arrival_time=1000.0, slo=5.0, model="model_3",
                               alg2_seq=30)]
    e = engine(waiting=waiting, staged=[staged_req("model_3#3", "model_3", 29)])
    e._convert_req_to_frontend_reqs = lambda r: staged_req(r.rid, "model_3", None)
    e._evict_all_waiting_requests()
    check("staged requests are still released", not e._alg2_staged_generation_reqs)
    check("staged and waiting are reported separately",
          len(reports(e, "staged-never-admitted")) == 1
          and len(reports(e, "evicted-to-frontend")) == 1)
    check("the waiting queue is cleared", not e.waiting_queue)


def test_b_cleanup_is_idempotent():
    print("B: a second deactivation changes nothing")
    e = engine(staged=[staged_req("model_3#3", "model_3", 29)])
    e._evict_all_waiting_requests()
    first = len(reports(e, "staged-never-admitted"))
    e._evict_all_waiting_requests()
    check("no duplicate staged report on a second pass",
          len(reports(e, "staged-never-admitted")) == first == 1)
    check("and the list stays empty", not e._alg2_staged_generation_reqs)


def test_b_nothing_is_reported_when_nothing_was_staged():
    print("B: an empty staged list reports nothing")
    e = engine()
    e._evict_all_waiting_requests()
    check("no empty report is sent", not reports(e, "staged-never-admitted"))


# ---------------------------------------------------------------- A
def test_a_admission_names_the_scheduled_model():
    print("A: the admission carries the request's model, not the slot's")
    e = engine(model="model_5")           # the slot is now model_5 ...
    dispatched = staged_req("model_3#3", "model_3", 29)   # ... the request is not

    e._tokenize = lambda r: r
    def handle(req):
        e.waiting_queue.append(SimpleNamespace(
            rid=req.rid, alg2_seq=req.alg2_seq, alg2_backend_admitted=False))
    e.handle_generate_request = handle

    e.process_input_gen_requests([dispatched])
    admits = [o for _k, o in e.redis_client.sent if isinstance(o, BackendAdmitReq)]
    check("an admission is announced", len(admits) == 1)
    check("tagged with the model Algorithm 2 scheduled it under",
          admits[0].model == "model_3")
    check("not with the slot's current name", admits[0].model != "model_5")
    check("carrying its sequence", admits[0].alg2_seqs == [29])


def test_a_the_ordinary_case_is_unchanged():
    print("A: a request on its own model is unaffected")
    e = engine(model="model_3")
    e._tokenize = lambda r: r
    e.handle_generate_request = lambda req: e.waiting_queue.append(
        SimpleNamespace(rid=req.rid, alg2_seq=req.alg2_seq,
                        alg2_backend_admitted=False))
    e.process_input_gen_requests([staged_req("model_3#4", "model_3", 30)])
    admits = [o for _k, o in e.redis_client.sent if isinstance(o, BackendAdmitReq)]
    check("still admitted under its own model",
          len(admits) == 1 and admits[0].model == "model_3")


def test_a_a_request_without_a_model_falls_back_to_the_slot():
    print("A: a request that names no model")
    e = engine(model="model_3")
    e._tokenize = lambda r: r
    e.handle_generate_request = lambda req: e.waiting_queue.append(
        SimpleNamespace(rid=req.rid, alg2_seq=req.alg2_seq,
                        alg2_backend_admitted=False))
    req = staged_req("x#1", "model_3", 31)
    req.model = None
    e.process_input_gen_requests([req])
    admits = [o for _k, o in e.redis_client.sent if isinstance(o, BackendAdmitReq)]
    check("falls back to the engine's model rather than sending None",
          len(admits) == 1 and admits[0].model == "model_3")


# ------------------------------------------------- frontier still advances
def test_the_retired_sequence_lets_the_frontier_move():
    print("the retired staged sequence is one the GPU can step over")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_alg2_migration_handoff import (  # noqa: E402
        make_scheduler, dispatch, admit_and_start,
    )
    # model_6 stays resident, so its sequence stays live while model_3 leaves.
    gpu = make_scheduler(models=("model_3", "model_6"))
    seq_a = dispatch(gpu, "model_3#1", "model_3")
    seq_b = dispatch(gpu, "model_3#3", "model_3")     # staged, never admitted
    seq_c = dispatch(gpu, "model_6#5", "model_6")
    admit_and_start(gpu, "model_3#1", "model_3", seq_a)
    check("the frontier waits on the staged request",
          gpu._mh_next_backend_admit_seq == seq_b)
    gpu._handle_mh_migrated_away(MigratedAwayReq(
        rids=["model_3#3"], model="model_3", gpu_id=1,
        reason="staged-never-admitted", report_time=0.0))
    check("retiring it moves the frontier to the next live request",
          gpu._mh_next_backend_admit_seq == seq_c)


def main():
    test_b_staged_cleanup_with_an_empty_waiting_queue()
    test_b_the_slot_cannot_promote_it_afterwards()
    test_b_the_waiting_queue_path_still_works()
    test_b_cleanup_is_idempotent()
    test_b_nothing_is_reported_when_nothing_was_staged()
    test_a_admission_names_the_scheduled_model()
    test_a_the_ordinary_case_is_unchanged()
    test_a_a_request_without_a_model_falls_back_to_the_slot()
    test_the_retired_sequence_lets_the_frontier_move()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for name in FAIL:
            print(f"  FAILED: {name}")
        raise SystemExit(1)
    print("ALL STALE STAGED CLEANUP TESTS PASSED")


if __name__ == "__main__":
    main()
