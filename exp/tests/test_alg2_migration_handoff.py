#!/usr/bin/env python3
"""The Algorithm 2 x migration handoff, against its stated invariant.

D3 died because a request rebuilt on the target from migrated KV re-entered
prefill there with no entry in that GPU's Algorithm 2 ledger: the completion
gate found no record and failed closed. The invariant is written up in
`exp/results/final-baseline-ready/ALG2_MIGRATION_HANDOFF_INVARIANT.md`; these
tests check the three obligations it places on the implementation.

  H1  the source retires the request -- and repairs the sequence frontiers it
      was blocking -- before letting go, idempotently, and never stepping over
      a live sequence
  H2  the target adopts it, and its sequence comes from that GPU's ordinary
      Algorithm 2 scheduling with the original arrival time and SLO intact --
      not from a special issuance path
  H3  it then passes the same admission and ordering gates as everything else
"""

import sys
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2] / "prism-research"
sys.path.insert(0, str(REPO / "python"))

from sglang.multi_model.scheduling.gpu.gpu_scheduler import GPUScheduler  # noqa: E402
from sglang.multi_model.scheduling.gpu.request_queue import RequestQueue  # noqa: E402
from sglang.srt.managers.io_struct import (  # noqa: E402
    AdoptResumedReq, BackendAdmitReq, BatchRunReq, GenerateReqInput,
    MigratedAwayReq, PrefillCompleteReq,
)

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


class Redis:
    def __init__(self):
        self.sent = []
        self.ints = {}
        self.queues = {}

    def recv_pyobj_non_block(self, key, count=1):
        queue = self.queues.setdefault(key, [])
        out, self.queues[key] = queue[:count], queue[count:]
        return out

    def send_pyobj(self, key, obj):
        self.sent.append((key, obj))

    def get_int(self, key):
        return self.ints.get(key)

    def set_int(self, key, value):
        self.ints[key] = value

    def compare_and_advance_int(self, key, expected):
        if self.ints.get(key) != expected:
            return False
        self.ints[key] = expected + 1
        return True

    def close(self):
        pass


def make_scheduler(models=("model1", "model2")):
    gpu = GPUScheduler.__new__(GPUScheduler)
    gpu.gpu_id = 1
    gpu._mh_runtime_gate = True
    gpu._mh_gate_lock = Lock()
    gpu._mh_outstanding_prefills = {}
    gpu._mh_retired_admit_seqs = set()
    gpu._mh_retired_start_seqs = set()
    gpu._mh_dispatch_seq = 0
    gpu._mh_next_backend_admit_seq = 1
    gpu._mh_next_prefill_start_seq = 1
    gpu._mh_admission_seq_key = "alg2-next:1"
    gpu._shutdown_event = SimpleNamespace(set=lambda: None, is_set=lambda: False)
    gpu.redis_client = Redis()
    gpu.redis_client.set_int(gpu._mh_admission_seq_key, 1)
    gpu.server_args = SimpleNamespace(
        backend_generate_request_key_prefix="backend")
    gpu.queue = RequestQueue({m: 32768 for m in models})
    return gpu


def dispatch(gpu, rid, model, seq=None, exec_s=0.01):
    """What the scheduler's dispatch loop records for any request."""
    with gpu._mh_gate_lock:
        gpu._mh_dispatch_seq += 1
        gpu._mh_outstanding_prefills[rid] = {
            "seq": seq or gpu._mh_dispatch_seq, "rid": rid, "model": model,
            "dispatch_time": 0.0, "backend_admit_time": None,
            "start_time": None, "predicted_exec_s": exec_s,
            "backend_admitted": False, "started": False,
        }
    return gpu._mh_outstanding_prefills[rid]["seq"]


def admit_and_start(gpu, rid, model, seq):
    gpu._handle_mh_backend_admit(BackendAdmitReq(
        rids=[rid], model=model, alg2_seqs=[seq], admit_time=0.0, gpu_id=1))
    gpu._handle_mh_prefill_start(BatchRunReq(
        rids=[rid], model=model, run_time=0.0, gpu_id=1, alg2_seqs=[seq]))
    # The engine advances the shared token at prefill start; mirror it so the
    # token in these tests means what it means at runtime.
    gpu.redis_client.compare_and_advance_int(gpu._mh_admission_seq_key, seq)


def test_h1_source_retires_and_repairs():
    print("H1: the source retires before it lets go")
    gpu = make_scheduler()
    seq_r = dispatch(gpu, "R", "model1")

    gpu._handle_mh_migrated_away(MigratedAwayReq(
        rids=["R"], model="model1", gpu_id=1, reason="kv-stash",
        report_time=0.0))
    check("the ledger entry is gone", "R" not in gpu._mh_outstanding_prefills)
    check("the admit frontier steps over the retired sequence",
          gpu._mh_next_backend_admit_seq == seq_r + 1)
    check("so does the prefill-start frontier",
          gpu._mh_next_prefill_start_seq == seq_r + 1)
    check("and the shared admission token follows",
          gpu.redis_client.get_int(gpu._mh_admission_seq_key) == seq_r + 1)

    before = (gpu._mh_next_backend_admit_seq, gpu._mh_next_prefill_start_seq)
    gpu._handle_mh_migrated_away(MigratedAwayReq(
        rids=["R"], model="model1", gpu_id=1, reason="evicted-to-frontend",
        report_time=0.0))
    check("a second report of the same request changes nothing",
          (gpu._mh_next_backend_admit_seq,
           gpu._mh_next_prefill_start_seq) == before)


def test_h1_never_steps_over_a_live_sequence():
    """The frontier may only cross a *contiguous* run of retired sequences."""
    print("H1: a retired sequence behind a live one waits its turn")
    gpu = make_scheduler()
    seq_a = dispatch(gpu, "A", "model1")   # 1, live
    seq_r = dispatch(gpu, "R", "model1")   # 2, about to migrate away
    seq_b = dispatch(gpu, "B", "model1")   # 3, live

    gpu._handle_mh_migrated_away(MigratedAwayReq(
        rids=["R"], model="model1", gpu_id=1, reason="kv-stash",
        report_time=0.0))
    check("the frontier does not jump past the live sequence in front of it",
          gpu._mh_next_backend_admit_seq == seq_a)
    check("B's sequence is not consumed either",
          gpu._mh_next_backend_admit_seq < seq_b)

    # A now runs; the retired sequence behind it must be crossed automatically,
    # or B would wait for an admission that is never coming.
    admit_and_start(gpu, "A", "model1", seq_a)
    check("once A is admitted the retired sequence is crossed",
          gpu._mh_next_backend_admit_seq == seq_b)
    check("and the start frontier reaches B as well",
          gpu._mh_next_prefill_start_seq == seq_b)

    admit_and_start(gpu, "B", "model1", seq_b)
    gpu._handle_mh_prefill_complete(PrefillCompleteReq(
        rids=["A"], model="model1", complete_time=0.0, gpu_id=1))
    gpu._handle_mh_prefill_complete(PrefillCompleteReq(
        rids=["B"], model="model1", complete_time=0.0, gpu_id=1))
    check("the source ledger ends empty", not gpu._mh_outstanding_prefills)


def test_h2_adoption_goes_through_ordinary_scheduling():
    print("H2: adoption queues the request, it does not hand out a sequence")
    gpu = make_scheduler()
    gpu._handle_mh_adopt_resumed(AdoptResumedReq(
        rids=["R"], model="model1", gpu_id=1, prefill_tokens=[1],
        arrival_times=[1000.0], slos=[5.0], request_time=0.0))

    check("no sequence was issued on receipt", gpu._mh_dispatch_seq == 0)
    check("and no ledger entry was created",
          "R" not in gpu._mh_outstanding_prefills)

    queued = [w.req for w in gpu.queue._queue]
    check("the request is in the GPU's ordinary queue",
          [r.rid for r in queued] == ["R"])
    resumed = queued[0]
    check("marked as resumed so the engine promotes the request it holds",
          resumed.alg2_resumed is True)
    check("its original arrival time is preserved",
          resumed.arrival_time == 1000.0)
    check("its original SLO is preserved -- the deadline is unchanged",
          resumed.slo == 5.0)
    check("its prefill cost is the resume extend length, not the whole prompt",
          resumed.prompt_len == 1)


def test_h2_the_source_sequence_is_not_reused():
    print("H2: a target sequence is a target sequence")
    gpu = make_scheduler()
    gpu._mh_dispatch_seq = 40           # this GPU has issued 40 already
    gpu._handle_mh_adopt_resumed(AdoptResumedReq(
        rids=["R"], model="model1", gpu_id=1, prefill_tokens=[1],
        arrival_times=[1000.0], slos=[5.0], request_time=0.0))
    resumed = [w.req for w in gpu.queue._queue][0]
    check("the placeholder carries no sequence of its own",
          resumed.alg2_seq is None)
    seq = dispatch(gpu, "R", "model1")
    check("it takes the next sequence of THIS GPU when dispatched", seq == 41)


def test_h3_mixed_order_is_the_targets_order():
    """A (target-local), R (resumed), B (target-local) in one schedule."""
    print("H3: one gate for target-local and resumed alike")
    gpu = make_scheduler()
    seq_a = dispatch(gpu, "A", "model1")
    seq_r = dispatch(gpu, "R", "model1")     # adopted, dispatched in its turn
    seq_b = dispatch(gpu, "B", "model2")
    check("sequences are consecutive and target-local",
          [seq_a, seq_r, seq_b] == [1, 2, 3])

    for rid, model, seq in (("A", "model1", seq_a), ("R", "model1", seq_r),
                            ("B", "model2", seq_b)):
        admit_and_start(gpu, rid, model, seq)
    check("admission followed the target's order",
          gpu._mh_next_backend_admit_seq == 4)
    check("prefill start followed the target's order",
          gpu._mh_next_prefill_start_seq == 4)

    for rid, model in (("R", "model1"), ("A", "model1"), ("B", "model2")):
        gpu._handle_mh_prefill_complete(PrefillCompleteReq(
            rids=[rid], model=model, complete_time=0.0, gpu_id=1))
    check("the ledger is empty at the end", not gpu._mh_outstanding_prefills)


def test_h3_a_resumed_request_out_of_order_still_fails_closed():
    """No exemption: if the gate would reject it, it must still reject it."""
    print("H3: no bypass for resumed requests")
    gpu = make_scheduler()
    seq_a = dispatch(gpu, "A", "model1")
    seq_r = dispatch(gpu, "R", "model1")
    try:
        gpu._handle_mh_backend_admit(BackendAdmitReq(
            rids=["R"], model="model1", alg2_seqs=[seq_r], admit_time=0.0,
            gpu_id=1))
    except RuntimeError:
        check("admitting the resumed request ahead of A is rejected", True)
    else:
        check("admitting the resumed request ahead of A is rejected", False)


def test_the_full_handoff():
    """GPU0 holds R, R migrates, GPU1 runs it, both ledgers end empty."""
    print("the whole handoff, end to end")
    source = make_scheduler()
    source.gpu_id = 0
    target = make_scheduler()

    seq_source = dispatch(source, "R", "model1")
    check("R is outstanding on the source, and only there",
          "R" in source._mh_outstanding_prefills
          and "R" not in target._mh_outstanding_prefills)

    source._handle_mh_migrated_away(MigratedAwayReq(
        rids=["R"], model="model1", gpu_id=0, reason="kv-stash",
        report_time=0.0))
    check("in transit it is outstanding nowhere",
          "R" not in source._mh_outstanding_prefills
          and "R" not in target._mh_outstanding_prefills)

    target._handle_mh_adopt_resumed(AdoptResumedReq(
        rids=["R"], model="model1", gpu_id=1, prefill_tokens=[1],
        arrival_times=[1000.0], slos=[5.0], request_time=0.0))
    seq_target = dispatch(target, "R", "model1")
    check("it is outstanding on the target, and only there",
          "R" in target._mh_outstanding_prefills
          and "R" not in source._mh_outstanding_prefills)
    check("with a target-local sequence, not the source's",
          seq_target == 1 and seq_source == 1
          and target._mh_outstanding_prefills["R"]["seq"] == seq_target)

    admit_and_start(target, "R", "model1", seq_target)
    target._handle_mh_prefill_complete(PrefillCompleteReq(
        rids=["R"], model="model1", complete_time=0.0, gpu_id=1))
    check("both ledgers end empty",
          not source._mh_outstanding_prefills
          and not target._mh_outstanding_prefills)


def test_mixed_schedule_through_the_real_selector():
    """A (target-local), R (resumed), B (target-local), ordered by Algorithm 2.

    This does not stage the order by hand: A, R and B go into the target's real
    queue, Moore--Hodgson picks the order from their deadlines and costs, and
    the runtime gates then have to see exactly that order. R is in the running
    on its original deadline, not on a fresh one.
    """
    print("a mixed schedule, ordered by the real selector")
    gpu = make_scheduler(models=("model1", "model2"))
    gpu.queue.configure_moore_hodgson(
        True, {"model1": 20000.0, "model2": 20000.0})

    now = 1000.0
    # A is due last, R next, B first: the order must come out B, R, A.
    gpu.queue.add_requests([
        GenerateReqInput(rid="A", model="model1", prompt_len=100,
                         arrival_time=now, slo=9.0, input_ids=[1] * 100,
                         sampling_params={"max_new_tokens": 4}, output_len=4),
        GenerateReqInput(rid="B", model="model2", prompt_len=100,
                         arrival_time=now, slo=3.0, input_ids=[1] * 100,
                         sampling_params={"max_new_tokens": 4}, output_len=4),
    ])
    gpu._handle_mh_adopt_resumed(AdoptResumedReq(
        rids=["R"], model="model1", gpu_id=1, prefill_tokens=[1],
        arrival_times=[now], slos=[6.0], request_time=now))
    check("all three are queued together",
          {w.req.rid for w in gpu.queue._queue} == {"A", "R", "B"})

    ordered = gpu.queue.admission_control(
        available_resources=10 ** 9,
        model_backend_queue_lens={"model1": 0, "model2": 0},
        model_states={"model1": "activated", "model2": "activated"},
        allow_sending_when_activating=True,
        mh_outstanding_work_s=0.0,
        mh_dispatch_budget=3,
    )
    chosen = [req.rid for req in ordered]
    check(f"Algorithm 2 ordered them by deadline: {chosen}",
          chosen == ["B", "R", "A"])
    check("the resumed request was ranked on its own deadline, in the middle",
          chosen.index("R") == 1)

    # Dispatch in exactly that order, the way the scheduler loop does.
    seqs = {}
    for req in ordered:
        seqs[req.rid] = dispatch(gpu, req.rid, req.model,
                                 exec_s=gpu.queue.mh_exec_time(req))
    check("sequences follow the selector's order",
          [seqs[rid] for rid in chosen] == [1, 2, 3])

    # And the gates accept exactly that order and nothing else.
    model_of = {req.rid: req.model for req in ordered}
    for rid in chosen:
        admit_and_start(gpu, rid, model_of[rid], seqs[rid])
    check("admission and prefill start followed it to the end",
          gpu._mh_next_backend_admit_seq == 4
          and gpu._mh_next_prefill_start_seq == 4)

    for rid in chosen:
        gpu._handle_mh_prefill_complete(PrefillCompleteReq(
            rids=[rid], model=model_of[rid], complete_time=0.0, gpu_id=1))
    check("nothing is left outstanding", not gpu._mh_outstanding_prefills)


def test_resumed_request_keeps_its_place_when_it_is_late():
    """Its deadline is the original one, so an old request sorts early."""
    print("the resumed request's deadline is the one it always had")
    gpu = make_scheduler()
    gpu.queue.configure_moore_hodgson(True, {"model1": 20000.0})
    now = 1000.0
    gpu.queue.add_requests([
        GenerateReqInput(rid="NEW", model="model1", prompt_len=100,
                         arrival_time=now, slo=5.0, input_ids=[1] * 100,
                         sampling_params={"max_new_tokens": 4}, output_len=4),
    ])
    # R arrived 4 s ago with the same SLO: its deadline is 4 s earlier.
    gpu._handle_mh_adopt_resumed(AdoptResumedReq(
        rids=["R"], model="model1", gpu_id=1, prefill_tokens=[1],
        arrival_times=[now - 4.0], slos=[5.0], request_time=now))
    ordered = gpu.queue.admission_control(
        available_resources=10 ** 9,
        model_backend_queue_lens={"model1": 0},
        model_states={"model1": "activated"},
        allow_sending_when_activating=True,
        mh_outstanding_work_s=0.0, mh_dispatch_budget=2,
    )
    check("the older resumed request is scheduled before the new one",
          [req.rid for req in ordered][0] == "R")


def make_engine(model="model1", gpu_id=1):
    """A Scheduler with only what the handoff path touches."""
    from sglang.srt.managers.scheduler import Scheduler
    engine = Scheduler.__new__(Scheduler)
    engine.tp_rank = 0
    engine.tp_size = 1
    engine.gpu_id = gpu_id
    engine.model_name = model
    engine.waiting_queue = []
    engine._alg2_staged_generation_reqs = []
    engine._alg2_pending_adoption = {}
    engine._alg2_runtime_gate = True
    engine._alg2_admission_seq_key = f"alg2-next:{gpu_id}"
    engine.redis_client = Redis()
    engine.server_args = SimpleNamespace(
        engine_to_gpu_scheduler_key_prefix="e2s",
        backend_generate_request_key_prefix="backend")
    return engine


class HeldReq:
    """Stands in for the Req rebuilt from a migrated capsule."""

    def __init__(self, rid, arrival_time, slo, extend_input_len=1):
        self.rid = rid
        self.arrival_time = arrival_time
        self.slo = slo
        self.extend_input_len = extend_input_len
        self.prefix_indices = list(range(8))
        self.alg2_seq = None
        self.alg2_backend_admitted = False


def test_engine_holds_then_promotes_the_request_it_rebuilt():
    print("engine side: held out of the queue, then admitted normally")
    from sglang.srt.managers.io_struct import AdoptGrantReq
    engine = make_engine()
    held = HeldReq("R", arrival_time=1000.0, slo=5.0)
    engine._alg2_pending_adoption["R"] = held

    check("a rebuilt request is not in the waiting queue yet",
          not engine.waiting_queue)

    engine._alg2_request_adoption()
    sent = [obj for _key, obj in engine.redis_client.sent
            if isinstance(obj, AdoptResumedReq)]
    check("the engine asks the target's Algorithm 2 to schedule it",
          len(sent) == 1 and sent[0].rids == ["R"])
    check("it reports the resume extend length as the prefill cost",
          sent[0].prefill_tokens == [1])
    check("and carries the original arrival time and SLO",
          sent[0].arrival_times == [1000.0] and sent[0].slos == [5.0])

    # The placeholder comes back dispatched, with the sequence Algorithm 2 gave
    # it, through the same backend queue every request arrives on.
    engine.redis_client.sent.clear()
    engine.process_input_gen_requests([GenerateReqInput(
        rid="R", model="model1", alg2_seq=7, alg2_resumed=True,
        prompt_len=1, arrival_time=1000.0, slo=5.0)])

    check("the request that goes into the waiting queue is the rebuilt one",
          engine.waiting_queue == [held])
    check("its KV slots are still attached", held.prefix_indices == list(range(8)))
    check("it carries the target's sequence", held.alg2_seq == 7)
    check("it is marked backend-admitted like any other request",
          held.alg2_backend_admitted is True)
    admits = [obj for _key, obj in engine.redis_client.sent
              if isinstance(obj, BackendAdmitReq)]
    check("and it announces its admission through the same message",
          len(admits) == 1 and admits[0].rids == ["R"]
          and admits[0].alg2_seqs == [7])
    check("nothing is left pending", not engine._alg2_pending_adoption)


def test_engine_reports_both_ways_a_request_leaves():
    print("engine side: both exits are reported")
    engine = make_engine(gpu_id=0)
    engine._alg2_report_migrated_away(["R1", "R2"], "kv-stash")
    engine._alg2_report_migrated_away([], "evicted-to-frontend")
    reports = [obj for _key, obj in engine.redis_client.sent
               if isinstance(obj, MigratedAwayReq)]
    check("the stash exit is reported once",
          len(reports) == 1 and sorted(reports[0].rids) == ["R1", "R2"])
    check("it names the GPU letting go and why",
          reports[0].gpu_id == 0 and reports[0].reason == "kv-stash")
    check("an empty report is not sent at all", len(reports) == 1)


def test_a_dispatch_still_in_redis_is_drained_and_retired():
    """The third exit: dispatched, never fetched, model deactivates.

    Traced in D3 run 2: `model_4#187` took sequence 1256 at 14:02:37.593, the
    deactivate arrived 43 ms later with the engine holding only 1254 and 1255,
    and GPU1's admission frontier waited on 1256 for the rest of the run --
    zero admissions after that instant. The waiting queue and the staged list
    were both drained; the Redis queue was not.
    """
    print("a dispatch still sitting in Redis when the model deactivates")
    from sglang.srt.managers.io_struct import AdoptGrantReq
    engine = make_engine(model="model_4", gpu_id=1)
    backend_key = "backend:model_4"
    frontend_key = "frontend:model_4"
    engine.server_args = SimpleNamespace(
        engine_to_gpu_scheduler_key_prefix="e2s",
        backend_generate_request_key_prefix="backend",
        frontend_generate_request_key_prefix="frontend")

    still_queued = GenerateReqInput(
        rid="model_4#187", model="model_4", alg2_seq=1256, prompt_len=100,
        arrival_time=1000.0, slo=5.0, input_ids=[1] * 100,
        sampling_params={"max_new_tokens": 4}, output_len=4)
    placeholder = GenerateReqInput(
        rid="R", model="model_4", alg2_seq=1257, alg2_resumed=True,
        prompt_len=1, arrival_time=999.0, slo=5.0)
    engine.redis_client.queues = {backend_key: [still_queued, placeholder]}

    engine._drain_backend_queue()

    check("the Redis queue is emptied",
          not engine.redis_client.queues.get(backend_key))
    returned = [obj for key, obj in engine.redis_client.sent if key == frontend_key]
    check("the undelivered request goes back to the frontend",
          [r.rid for r in returned] == ["model_4#187"])
    check("and drops this GPU's sequence on the way out",
          returned[0].alg2_seq is None)
    check("an adoption placeholder is not sent to the frontend as a request",
          all(r.rid != "R" for r in returned))
    reports = [obj for _key, obj in engine.redis_client.sent
               if isinstance(obj, MigratedAwayReq)]
    check("both are reported so the GPU stops expecting their sequences",
          len(reports) == 1
          and sorted(reports[0].rids) == ["R", "model_4#187"]
          and reports[0].reason == "backend-queue-drain")


def test_the_drained_sequence_lets_the_frontier_move_on():
    """End to end for the stall: dispatch, leave it in Redis, deactivate, drain."""
    print("the frontier advances once the undelivered dispatch is retired")
    gpu = make_scheduler(models=("model_4",))
    seq_a = dispatch(gpu, "model_4#182", "model_4")   # 1: fetched and admitted
    seq_b = dispatch(gpu, "model_4#187", "model_4")   # 2: stays in Redis
    seq_c = dispatch(gpu, "model_4#190", "model_4")   # 3: behind it

    admit_and_start(gpu, "model_4#182", "model_4", seq_a)
    check("the frontier is waiting on the undelivered dispatch",
          gpu._mh_next_backend_admit_seq == seq_b)

    gpu._handle_mh_migrated_away(MigratedAwayReq(
        rids=["model_4#187"], model="model_4", gpu_id=1,
        reason="backend-queue-drain", report_time=0.0))
    check("retiring it moves the frontier to the next live request",
          gpu._mh_next_backend_admit_seq == seq_c
          and gpu._mh_next_prefill_start_seq == seq_c)
    check("and the shared admission token follows",
          gpu.redis_client.get_int(gpu._mh_admission_seq_key) == seq_c)

    admit_and_start(gpu, "model_4#190", "model_4", seq_c)
    gpu._handle_mh_prefill_complete(PrefillCompleteReq(
        rids=["model_4#182"], model="model_4", complete_time=0.0, gpu_id=1))
    gpu._handle_mh_prefill_complete(PrefillCompleteReq(
        rids=["model_4#190"], model="model_4", complete_time=0.0, gpu_id=1))
    check("the GPU is serving again and its ledger is empty",
          not gpu._mh_outstanding_prefills)


def main():
    test_h1_source_retires_and_repairs()
    test_h1_never_steps_over_a_live_sequence()
    test_h2_adoption_goes_through_ordinary_scheduling()
    test_h2_the_source_sequence_is_not_reused()
    test_h3_mixed_order_is_the_targets_order()
    test_h3_a_resumed_request_out_of_order_still_fails_closed()
    test_the_full_handoff()
    test_mixed_schedule_through_the_real_selector()
    test_resumed_request_keeps_its_place_when_it_is_late()
    test_engine_holds_then_promotes_the_request_it_rebuilt()
    test_engine_reports_both_ways_a_request_leaves()
    test_a_dispatch_still_in_redis_is_drained_and_retired()
    test_the_drained_sequence_lets_the_frontier_move_on()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for name in FAIL:
            print(f"  FAILED: {name}")
        raise SystemExit(1)
    print("ALL ALG2 MIGRATION HANDOFF TESTS PASSED")


if __name__ == "__main__":
    main()
