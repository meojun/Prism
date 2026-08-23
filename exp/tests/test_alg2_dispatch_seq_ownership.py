#!/usr/bin/env python3
"""Dispatch-side ownership of an Algorithm 2 sequence.

The invariant this file checks:

    A dispatched Algorithm 2 sequence is owned by the GPU scheduler until the
    backend acknowledges admission. After that acknowledgement, ownership
    passes to the engine-side lifecycle accounting.

The failure that motivated it: on GPU 0 of `cal-0p00035-s0` attempt 2, seqs
1159/1160 were dispatched for model_4#140/#141, model_4 left GPU 0 before its
engine ever fetched them, and the engine's migrated-away report could not name
requests it had never held. Nothing retired the sequences, the contiguous
frontier stopped on 1159 -- correctly refusing to skip a possibly-live
sequence -- and GPU 0 admitted nothing for the remaining 26 minutes, including
two live model_6 requests on a still-resident model. The same class had also
failed attempt 1 (seqs 30/31, model_6#23/#24).

So the scheduler retires what it still owns, and only that. Anything already
acknowledged stays with the engine-side paths that already answer for it.
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
    BackendAdmitReq, BatchRunReq, MigratedAwayReq, PrefillCompleteReq,
)

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


class Redis:
    def __init__(self):
        self.ints = {}
        self.sent = []
        self.queues = {}

    def recv_pyobj_non_block(self, key, count=1):
        q = self.queues.setdefault(key, [])
        out, self.queues[key] = q[:count], q[count:]
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


def make_scheduler(gpu_id=0, models=("model_4", "model_6", "model_2")):
    gpu = GPUScheduler.__new__(GPUScheduler)
    gpu.gpu_id = gpu_id
    gpu._mh_runtime_gate = True
    gpu._mh_gate_lock = Lock()
    gpu._mh_outstanding_prefills = {}
    gpu._mh_retired_admit_seqs = set()
    gpu._mh_retired_start_seqs = set()
    gpu._mh_scheduler_retired_rids = {}
    gpu._mh_dispatch_seq = 0
    gpu._mh_next_backend_admit_seq = 1
    gpu._mh_next_prefill_start_seq = 1
    gpu._mh_admission_seq_key = f"alg2-next:{gpu_id}"
    gpu._shutdown_event = SimpleNamespace(set=lambda: None, is_set=lambda: False)
    gpu.redis_client = Redis()
    gpu.redis_client.set_int(gpu._mh_admission_seq_key, 1)
    gpu.server_args = SimpleNamespace(
        backend_generate_request_key_prefix="backend")
    gpu.queue = RequestQueue({m: 32768 for m in models})
    return gpu


def dispatch(gpu, rid, model):
    """Exactly what the dispatch loop records, including the tombstone clear."""
    gpu.queue.remove_requests_by_rid([rid])
    with gpu._mh_gate_lock:
        gpu._mh_scheduler_retired_rids.pop(rid, None)
        gpu._mh_dispatch_seq += 1
        gpu._mh_outstanding_prefills[rid] = {
            "seq": gpu._mh_dispatch_seq, "rid": rid, "model": model,
            "dispatch_time": 0.0, "backend_admit_time": None,
            "start_time": None, "predicted_exec_s": 0.01,
            "backend_admitted": False, "started": False,
        }
    return gpu._mh_dispatch_seq


def admit(gpu, rid, model, seq):
    gpu._handle_mh_backend_admit(BackendAdmitReq(
        rids=[rid], model=model, alg2_seqs=[seq], admit_time=0.0,
        gpu_id=gpu.gpu_id))


def start(gpu, rid, model, seq):
    gpu._handle_mh_prefill_start(BatchRunReq(
        rids=[rid], model=model, run_time=0.0, gpu_id=gpu.gpu_id,
        alg2_seqs=[seq]))
    gpu.redis_client.compare_and_advance_int(gpu._mh_admission_seq_key, seq)


def depart(gpu, model, reported=(), reason="kv-stash"):
    gpu._handle_mh_migrated_away(MigratedAwayReq(
        rids=list(reported), model=model, gpu_id=gpu.gpu_id, reason=reason,
        report_time=0.0))


# --------------------------------------------------------------------------
# 1. never fetched -- the exact production failure
# --------------------------------------------------------------------------

def test_never_fetched():
    print("never fetched: dispatched, model leaves before the engine fetches")
    gpu = make_scheduler()
    seq = dispatch(gpu, "model_4#140", "model_4")
    check("the scheduler owns it: dispatched, not acknowledged",
          gpu._mh_outstanding_prefills["model_4#140"]["backend_admitted"]
          is False)

    # The engine never held it, so it names nobody.
    depart(gpu, "model_4", reported=())

    check("the scheduler retired its own unacknowledged sequence",
          "model_4#140" not in gpu._mh_outstanding_prefills)
    check("the admit frontier advanced past it",
          gpu._mh_next_backend_admit_seq == seq + 1)
    check("the prefill-start frontier advanced past it",
          gpu._mh_next_prefill_start_seq == seq + 1)
    check("the shared admission token followed",
          gpu.redis_client.get_int(gpu._mh_admission_seq_key) == seq + 1)
    check("a tombstone records the retirement",
          gpu._mh_scheduler_retired_rids["model_4#140"]["seq"] == seq)


def test_never_fetched_then_target_and_frontier():
    """The production sequence end to end, across both GPUs."""
    print("never fetched: source frontier recovers, target schedules normally")
    src, tgt = make_scheduler(0), make_scheduler(1)
    seq_src = dispatch(src, "model_4#140", "model_4")
    dispatch(src, "model_6#1629", "model_6")      # live, behind it

    depart(src, "model_4", reported=())

    # The target admits the same request under its own local sequence.
    seq_tgt = dispatch(tgt, "model_4#140", "model_4")
    admit(tgt, "model_4#140", "model_4", seq_tgt)
    start(tgt, "model_4#140", "model_4", seq_tgt)
    tgt._handle_mh_prefill_complete(PrefillCompleteReq(
        rids=["model_4#140"], model="model_4", complete_time=0.0, gpu_id=1))

    check("the target used its own sequence space, not the source's",
          seq_tgt == 1 and seq_src == 1 and tgt is not src)
    check("the target served it to completion",
          "model_4#140" not in tgt._mh_outstanding_prefills)

    # And the live model_6 request behind the leak can now be admitted.
    admit(src, "model_6#1629", "model_6", 2)
    check("the source admitted the live request that was stuck behind it",
          src._mh_outstanding_prefills["model_6#1629"]["backend_admitted"])
    check("the source frontier is exactly one past it",
          src._mh_next_backend_admit_seq == 3)


# --------------------------------------------------------------------------
# 2. already staged -- ownership has passed to the engine
# --------------------------------------------------------------------------

def test_already_staged_is_not_retired_by_the_scheduler():
    print("already staged: acknowledged work stays with the engine")
    gpu = make_scheduler()
    seq = dispatch(gpu, "model_4#200", "model_4")
    admit(gpu, "model_4#200", "model_4", seq)     # ownership transfers

    depart(gpu, "model_4", reported=())           # engine has not reported yet

    check("the scheduler did not retire an acknowledged request",
          "model_4#200" in gpu._mh_outstanding_prefills)
    check("no tombstone was written for it",
          "model_4#200" not in gpu._mh_scheduler_retired_rids)
    check("the start frontier did not move",
          gpu._mh_next_prefill_start_seq == seq)

    # The engine's own cleanup retires it, exactly as before this change.
    depart(gpu, "model_4", reported=("model_4#200",))
    check("the engine-side report retires it",
          "model_4#200" not in gpu._mh_outstanding_prefills)
    check("and only then does the start frontier advance",
          gpu._mh_next_prefill_start_seq == seq + 1)


def test_no_double_retire_between_the_two_paths():
    print("already staged: the two paths never retire the same sequence twice")
    gpu = make_scheduler()
    seq = dispatch(gpu, "model_4#201", "model_4")

    depart(gpu, "model_4", reported=())               # scheduler retires
    front = (gpu._mh_next_backend_admit_seq, gpu._mh_next_prefill_start_seq)
    depart(gpu, "model_4", reported=("model_4#201",))  # engine reports it too
    check("the engine's later report of the same request is a no-op",
          (gpu._mh_next_backend_admit_seq,
           gpu._mh_next_prefill_start_seq) == front)
    check("the sequence was retired exactly once",
          seq not in gpu._mh_retired_admit_seqs
          and seq not in gpu._mh_retired_start_seqs)


def test_repeated_departures_are_idempotent():
    print("already staged: repeated departure reports change nothing")
    gpu = make_scheduler()
    dispatch(gpu, "model_4#202", "model_4")
    depart(gpu, "model_4", reported=())
    front = (gpu._mh_next_backend_admit_seq, gpu._mh_next_prefill_start_seq)
    for reason in ("kv-stash", "evicted-to-frontend", "staged-never-admitted"):
        depart(gpu, "model_4", reported=(), reason=reason)
    check("three more departures move nothing",
          (gpu._mh_next_backend_admit_seq,
           gpu._mh_next_prefill_start_seq) == front)
    check("no ledger entry reappeared", not gpu._mh_outstanding_prefills)


# --------------------------------------------------------------------------
# 3. the backend_admit race
# --------------------------------------------------------------------------

def test_admit_ack_arriving_after_retirement():
    print("race: the acknowledgement was already in flight when the model left")
    gpu = make_scheduler()
    seq = dispatch(gpu, "model_4#300", "model_4")
    dispatch(gpu, "model_6#900", "model_6")       # live, behind it

    depart(gpu, "model_4", reported=())
    front_after_retire = gpu._mh_next_backend_admit_seq

    crashed = False
    try:
        admit(gpu, "model_4#300", "model_4", seq)   # late ACK
    except RuntimeError:
        crashed = True
    check("a late acknowledgement is not an ordering violation", not crashed)
    check("and it does not advance the frontier a second time",
          gpu._mh_next_backend_admit_seq == front_after_retire)

    # The live request behind it is still admissible at its own turn.
    admit(gpu, "model_6#900", "model_6", 2)
    check("the live sequence behind it still admits normally",
          gpu._mh_next_backend_admit_seq == 3)


def test_admit_ack_before_departure_keeps_engine_ownership():
    print("race: the acknowledgement wins, so the engine keeps the request")
    gpu = make_scheduler()
    seq = dispatch(gpu, "model_4#301", "model_4")
    admit(gpu, "model_4#301", "model_4", seq)
    depart(gpu, "model_4", reported=())
    check("the acknowledged request is still on the engine's books",
          "model_4#301" in gpu._mh_outstanding_prefills)
    check("the sequence was counted once, by admission",
          gpu._mh_next_backend_admit_seq == seq + 1
          and seq not in gpu._mh_retired_admit_seqs)


def test_late_completion_after_retirement():
    print("race: a late completion for a retired sequence is a no-op")
    gpu = make_scheduler()
    seq = dispatch(gpu, "model_4#302", "model_4")
    depart(gpu, "model_4", reported=())
    admit(gpu, "model_4#302", "model_4", seq)
    crashed = False
    try:
        gpu._handle_mh_prefill_complete(PrefillCompleteReq(
            rids=["model_4#302"], model="model_4", complete_time=0.0,
            gpu_id=0))
    except (RuntimeError, KeyError):
        crashed = True
    check("a late completion does not fail the gate closed", not crashed)
    check("and leaves the frontier where retirement put it",
          gpu._mh_next_backend_admit_seq == seq + 1)


def test_returning_request_is_not_shadowed_by_its_tombstone():
    print("race: a request that comes back gets a live record, not a ghost")
    gpu = make_scheduler()
    dispatch(gpu, "model_4#303", "model_4")
    depart(gpu, "model_4", reported=())
    check("tombstoned after departure",
          "model_4#303" in gpu._mh_scheduler_retired_rids)

    seq2 = dispatch(gpu, "model_4#303", "model_4")   # migrated back later
    check("dispatching it again clears the tombstone",
          "model_4#303" not in gpu._mh_scheduler_retired_rids)
    admit(gpu, "model_4#303", "model_4", seq2)
    check("the new sequence is admitted through the ordinary gate",
          gpu._mh_outstanding_prefills["model_4#303"]["backend_admitted"])
    check("the frontier reflects both the retirement and the new admission",
          gpu._mh_next_backend_admit_seq == seq2 + 1)


# --------------------------------------------------------------------------
# 4. partial admission at departure -- the production shape
# --------------------------------------------------------------------------

def test_partial_admission_frontier_advance():
    """1158 admitted, 1159/1160 dispatched-only: only the latter two retire."""
    print("partial: only the unacknowledged sequences retire")
    gpu = make_scheduler()
    for n in range(1, 1158):                      # 1..1157 done and gone
        rid = f"filler#{n}"
        seq = dispatch(gpu, rid, "model_6")
        admit(gpu, rid, "model_6", seq)
        start(gpu, rid, "model_6", seq)
        gpu._handle_mh_prefill_complete(PrefillCompleteReq(
            rids=[rid], model="model_6", complete_time=0.0, gpu_id=0))

    seq_1158 = dispatch(gpu, "model_4#139", "model_4")
    admit(gpu, "model_4#139", "model_4", seq_1158)          # acknowledged
    seq_1159 = dispatch(gpu, "model_4#140", "model_4")      # dispatched only
    seq_1160 = dispatch(gpu, "model_4#141", "model_4")      # dispatched only
    seq_1161 = dispatch(gpu, "model_6#1629", "model_6")     # live, other model
    check("the sequence numbers match the production failure",
          (seq_1158, seq_1159, seq_1160, seq_1161)
          == (1158, 1159, 1160, 1161))

    depart(gpu, "model_4", reported=())

    check("the acknowledged 1158 was left to the engine",
          "model_4#139" in gpu._mh_outstanding_prefills)
    check("1159 was retired", "model_4#140" not in gpu._mh_outstanding_prefills)
    check("1160 was retired", "model_4#141" not in gpu._mh_outstanding_prefills)
    check("the live model_6 request was untouched",
          "model_6#1629" in gpu._mh_outstanding_prefills)
    # 1158 was acknowledged, so the admit frontier had already moved to 1159;
    # retiring 1159/1160 drains it straight through to the live 1161. This is
    # the whole point: in production it stopped dead at 1159 instead.
    check("the admit frontier drained past both retired sequences",
          gpu._mh_next_backend_admit_seq == 1161)
    check("and stopped exactly at the live sequence 1161",
          1161 not in gpu._mh_retired_admit_seqs
          and not gpu._mh_retired_admit_seqs)

    # The start frontier must NOT move: 1158 is admitted, live, and unstarted.
    check("the start frontier still waits on live 1158",
          gpu._mh_next_prefill_start_seq == 1158)
    check("1159 and 1160 wait behind it as retired, not skipped",
          gpu._mh_retired_start_seqs == {1159, 1160})

    # The engine finishes with 1158; the contiguous prefix then unblocks.
    depart(gpu, "model_4", reported=("model_4#139",))
    check("retiring 1158 drains the start prefix 1158-1160",
          gpu._mh_next_prefill_start_seq == 1161)
    check("and stops exactly at the live sequence 1161",
          not gpu._mh_retired_start_seqs)

    admit(gpu, "model_6#1629", "model_6", 1161)
    check("the live request behind the leak is admitted",
          gpu._mh_next_backend_admit_seq == 1162)


def test_a_live_sequence_is_never_retired():
    print("partial: another model's live sequence is never retired")
    gpu = make_scheduler()
    seq_a = dispatch(gpu, "model_6#1", "model_6")   # live, different model
    seq_b = dispatch(gpu, "model_4#1", "model_4")

    depart(gpu, "model_4", reported=())

    check("the other model's sequence is still live",
          "model_6#1" in gpu._mh_outstanding_prefills)
    check("the frontier did not step over it",
          gpu._mh_next_backend_admit_seq == seq_a)
    check("the departing model's sequence is queued as retired",
          gpu._mh_retired_admit_seqs == {seq_b})

    admit(gpu, "model_6#1", "model_6", seq_a)
    check("admitting the live one drains the retired one behind it",
          gpu._mh_next_backend_admit_seq == seq_b + 1)


def test_departure_of_a_model_with_nothing_outstanding():
    print("partial: a departure with nothing owned changes nothing")
    gpu = make_scheduler()
    seq = dispatch(gpu, "model_6#1", "model_6")
    depart(gpu, "model_4", reported=())
    check("no frontier moved", gpu._mh_next_backend_admit_seq == seq)
    check("no tombstone was written", not gpu._mh_scheduler_retired_rids)
    check("the unrelated request is untouched",
          "model_6#1" in gpu._mh_outstanding_prefills)


def main():
    for fn in (
        test_never_fetched,
        test_never_fetched_then_target_and_frontier,
        test_already_staged_is_not_retired_by_the_scheduler,
        test_no_double_retire_between_the_two_paths,
        test_repeated_departures_are_idempotent,
        test_admit_ack_arriving_after_retirement,
        test_admit_ack_before_departure_keeps_engine_ownership,
        test_late_completion_after_retirement,
        test_returning_request_is_not_shadowed_by_its_tombstone,
        test_partial_admission_frontier_advance,
        test_a_live_sequence_is_never_retired,
        test_departure_of_a_model_with_nothing_outstanding,
    ):
        fn()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for name in FAIL:
        print("  FAILED:", name)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
