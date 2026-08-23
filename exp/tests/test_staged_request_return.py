#!/usr/bin/env python3
"""A staged request must be given back before its sequence is retired.

`cal-0p07-s0` served 8,226 of 8,227 requests and stalled on the last one.
`model_2#58` was dispatched as sequence 1014 and fetched into model_2's staged
list; model_2 was then deactivated, the staged list was emptied and reported as
`staged-never-admitted`, and 1014 was retired with both frontiers drained over
it -- correctly. But `_alg2_report_migrated_away` only tells the GPU scheduler
to stop waiting for a sequence. It delivers the request to nobody. So the ledger
stayed perfectly consistent, every ordering and stale-sequence check passed, and
the request was lost.

The two sibling paths in the same function return the payload first and report
second. The invariant they share, and that the staged path now shares:

    Before the source gives up ownership, the request payload is returned to
    the frontend exactly once; only then is the source-local Algorithm 2
    sequence retired.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2] / "prism-research"
sys.path.insert(0, str(REPO / "python"))

from sglang.srt.managers.io_struct import (  # noqa: E402
    GenerateReqInput, MigratedAwayReq,
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

    def close(self):
        pass

    def frontend(self, model="model_2"):
        return [o for k, o in self.sent if k == f"frontend:{model}"]

    def reports(self):
        return [o for _k, o in self.sent if isinstance(o, MigratedAwayReq)]


def req(rid, prompt=64, arrival=1234.5, slo=7.5, model="model_2"):
    r = GenerateReqInput(rid=rid, model=model, prompt_len=prompt,
                         arrival_time=arrival, slo=slo, output_len=8,
                         input_ids=[3] * prompt)
    r.origin_input_ids = [3] * prompt
    r.sampling_params = SimpleNamespace(
        max_new_tokens=8, min_new_tokens=0, stop_strs=None, stop_token_ids=None,
        temperature=0.0, top_p=1.0, top_k=-1, min_p=0.0, frequency_penalty=0.0,
        presence_penalty=0.0, repetition_penalty=1.0, ignore_eos=False,
        skip_special_tokens=True, spaces_between_special_tokens=True,
        regex=None, n=1, json_schema=None, no_stop_trim=False)
    r.prefix_indices, r.extend_input_len = [], 0
    r.return_logprob, r.logprob_start_len, r.top_logprobs_num = False, 0, 0
    r.stream, r.lora_path = False, None
    r.alg2_seq, r.alg2_backend_admitted = None, False
    return r


def engine(model="model_2", gpu_id=0, staged=(), waiting=(), backend=()):
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
    e.token_to_kv_pool = SimpleNamespace(free=lambda s: None)
    e.server_args = SimpleNamespace(
        engine_to_gpu_scheduler_key_prefix="e2s",
        backend_generate_request_key_prefix="backend",
        frontend_generate_request_key_prefix="frontend")
    if backend:
        e.redis_client.queues[f"backend:{model}"] = list(backend)
    return e


def order_of(e):
    """Positions of frontend deliveries and of migrated-away reports."""
    fe, rep = [], []
    for i, (k, o) in enumerate(e.redis_client.sent):
        if k == f"frontend:{e.model_name}":
            fe.append(i)
        elif isinstance(o, MigratedAwayReq):
            rep.append(i)
    return fe, rep


# ------------------------------------------------------------------ one staged
def test_one_staged_request_returned_exactly_once():
    print("one staged request goes back to the frontend exactly once")
    e = engine(staged=[req("model_2#58")])
    e._evict_all_waiting_requests()
    fe = e.redis_client.frontend()
    check("delivered once", [o.rid for o in fe] == ["model_2#58"])
    check("the staged list is empty", e._alg2_staged_generation_reqs == [])
    reports = [r for r in e.redis_client.reports()
               if r.reason == "staged-never-admitted"]
    check("still reported as staged-never-admitted, so 1014 is retired",
          len(reports) == 1 and reports[0].rids == ["model_2#58"])


def test_returned_before_the_sequence_is_retired():
    print("the payload is returned before the sequence is retired")
    e = engine(staged=[req("model_2#58")])
    e._evict_all_waiting_requests()
    fe, rep = order_of(e)
    check("frontend delivery precedes the retire report", fe[0] < rep[0])


def test_request_fields_survive_the_return():
    print("arrival time, SLO, model and prompt survive the handover")
    e = engine(staged=[req("model_2#58", prompt=97, arrival=1234.5, slo=7.5)])
    e._evict_all_waiting_requests()
    o = e.redis_client.frontend()[0]
    check("arrival_time preserved", o.arrival_time == 1234.5)
    check("slo preserved -- the deadline is unchanged", o.slo == 7.5)
    check("model preserved", o.model == "model_2")
    check("prompt preserved", o.prompt_len == 97
          and list(o.input_ids) == [3] * 97)
    check("it is not flagged as a resumed placeholder",
          not getattr(o, "alg2_resumed", False))


# --------------------------------------------------------------- many staged
def test_many_staged_requests_each_returned_once():
    print("several staged requests are each returned exactly once")
    rids = [f"model_2#{n}" for n in (58, 59, 60, 61)]
    e = engine(staged=[req(r) for r in rids])
    e._evict_all_waiting_requests()
    got = [o.rid for o in e.redis_client.frontend()]
    check("all four delivered", sorted(got) == sorted(rids))
    check("none delivered twice", len(got) == len(set(got)) == 4)
    reports = [r for r in e.redis_client.reports()
               if r.reason == "staged-never-admitted"]
    check("all four retired in one report",
          len(reports) == 1 and sorted(reports[0].rids) == sorted(rids))


# ------------------------------------------------- alongside the other paths
def test_no_rid_is_returned_twice_across_the_three_paths():
    print("staged, waiting and backend together: each rid returned once")
    staged = [req("model_2#58")]
    waiting = [req("model_2#70")]
    backend = [req("model_2#80")]
    e = engine(staged=staged, waiting=waiting, backend=backend)
    e._evict_all_waiting_requests()
    got = [o.rid for o in e.redis_client.frontend()]
    check("all three returned",
          sorted(got) == ["model_2#58", "model_2#70", "model_2#80"])
    check("no rid returned twice", len(got) == len(set(got)))
    reasons = sorted(r.reason for r in e.redis_client.reports())
    check("each path reports under its own reason",
          reasons == ["backend-queue-drain", "evicted-to-frontend",
                      "staged-never-admitted"])


def test_a_rid_in_two_holdings_is_not_returned_twice():
    """The same request cannot be in two holdings at once, and if a report
    names it twice the frontend must still see it once per holding it was
    actually in -- never a phantom extra copy."""
    print("a rid present only in the staged list is not also evicted")
    e = engine(staged=[req("model_2#58")], waiting=[])
    e._evict_all_waiting_requests()
    got = [o.rid for o in e.redis_client.frontend()]
    check("exactly one delivery", got.count("model_2#58") == 1)
    evicted = [r for r in e.redis_client.reports()
               if r.reason == "evicted-to-frontend"]
    check("the waiting-queue path did not also claim it",
          all("model_2#58" not in r.rids for r in evicted))


# ---------------------------------------------------------------- regressions
def test_waiting_queue_path_unchanged():
    print("regression: the waiting-queue path still returns and reports")
    e = engine(waiting=[req("model_2#70"), req("model_2#71")])
    e._evict_all_waiting_requests()
    got = [o.rid for o in e.redis_client.frontend()]
    check("both returned once", sorted(got) == ["model_2#70", "model_2#71"])
    check("the waiting queue is cleared", not e.waiting_queue)
    check("reported as evicted-to-frontend",
          any(r.reason == "evicted-to-frontend"
              and sorted(r.rids) == ["model_2#70", "model_2#71"]
              for r in e.redis_client.reports()))


def test_backend_drain_path_unchanged():
    print("regression: the backend drain still returns and reports")
    e = engine(backend=[req("model_2#80")])
    e._evict_all_waiting_requests()
    check("returned once",
          [o.rid for o in e.redis_client.frontend()] == ["model_2#80"])
    check("reported as backend-queue-drain",
          any(r.reason == "backend-queue-drain" and r.rids == ["model_2#80"]
              for r in e.redis_client.reports()))


def test_empty_deactivation_is_a_no_op():
    print("regression: a deactivation holding nothing returns nothing")
    e = engine()
    e._evict_all_waiting_requests()
    check("no frontend traffic", e.redis_client.frontend() == [])
    check("no reports", e.redis_client.reports() == [])


def test_staged_release_still_precedes_the_early_return():
    """The B fix exists because an empty waiting queue used to skip the staged
    release entirely, leaving a request in a slot that was then reassigned."""
    print("regression: staged release happens even with an empty waiting queue")
    e = engine(staged=[req("model_2#58")], waiting=[])
    e._evict_all_waiting_requests()
    check("released despite the empty waiting queue",
          [o.rid for o in e.redis_client.frontend()] == ["model_2#58"])
    check("and its sequence retired",
          any(r.reason == "staged-never-admitted"
              for r in e.redis_client.reports()))


# ------------------------------------------------------- the failing scenario
def test_model_2_58_can_complete_after_re_entry():
    print("the cal-0p07-s0 scenario: model_2#58 can be served after re-entry")
    src = engine(staged=[req("model_2#58")], gpu_id=0)
    src._evict_all_waiting_requests()
    returned = src.redis_client.frontend()
    check("the source returned it", len(returned) == 1)

    # Whichever GPU picks it up dispatches it afresh, with a new sequence.
    from sglang.multi_model.scheduling.gpu.request_queue import RequestQueue
    q = RequestQueue({"model_2": 32768})
    q.add_requests(list(returned))
    admitted = q.admission_control(
        available_resources=1 << 40, model_backend_queue_lens={"model_2": 0},
        model_states={"model_2": "activated"},
        allow_sending_when_activating=True)
    got = admitted if isinstance(admitted, list) else \
        [r for v in admitted.values() for r in v]
    check("the target admits it", [r.rid for r in got] == ["model_2#58"])
    check("it carries no stale source sequence",
          getattr(got[0], "alg2_seq", None) is None)
    check("its deadline is the one it always had",
          got[0].arrival_time == 1234.5 and got[0].slo == 7.5)


def main():
    for fn in (
        test_one_staged_request_returned_exactly_once,
        test_returned_before_the_sequence_is_retired,
        test_request_fields_survive_the_return,
        test_many_staged_requests_each_returned_once,
        test_no_rid_is_returned_twice_across_the_three_paths,
        test_a_rid_in_two_holdings_is_not_returned_twice,
        test_waiting_queue_path_unchanged,
        test_backend_drain_path_unchanged,
        test_empty_deactivation_is_a_no_op,
        test_staged_release_still_precedes_the_early_return,
        test_model_2_58_can_complete_after_re_entry,
    ):
        fn()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for n in FAIL:
        print("  FAILED:", n)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
