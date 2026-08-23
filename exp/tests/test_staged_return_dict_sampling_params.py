#!/usr/bin/env python3
"""A staged payload goes back in the form it arrived in.

The tau=0.00035 seed-0 calibration run lost `model_3#192`. It had been
dispatched as sequence 854 and fetched into model_3's staged list on GPU 0;
one second later model_3 migrated away, and the staged-return path tried to
hand the payload back through `_convert_req_to_frontend_reqs`:

    [PAPER-ALG2-HANDOFF] could not return staged model_3#192 to the frontend:
        'dict' object has no attribute 'max_new_tokens'

The send never happened. The sequence was retired anyway -- correctly, since
withholding it would stall this GPU's frontier on a request that had left it --
so every ordering and stale-sequence check passed while the request itself was
gone. Both GPUs went idle waiting for it and the run ended at 8,225 of 8,227.

The cause is a type mismatch, not a policy question. A staged request is the
payload as it came off the backend queue: it was never admitted, so it was
never built into a `Req`, and its `sampling_params` is still the dict it was
serialized as. `_convert_req_to_frontend_reqs` is written for an admitted `Req`
and reads attributes off that field.

The existing staged-return suite missed this because its fixture builds
`sampling_params` as an object. This one builds it as a dict, which is what the
engine actually holds, and asserts the invariant the run needed:

    A staged-but-never-admitted request is returned to the frontend exactly
    once, in its original backend form, with this GPU's sequence cleared, and
    its issuing sequence retired exactly once.
"""

import logging
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


# The dict a backend payload actually carries: what redis round-trips, not what
# the scheduler builds after admission.
BACKEND_SAMPLING_PARAMS = {
    "max_new_tokens": 384, "min_new_tokens": 0, "stop": None,
    "stop_token_ids": None, "temperature": 0.0, "top_p": 1.0, "top_k": -1,
    "min_p": 0.0, "frequency_penalty": 0.0, "presence_penalty": 0.0,
    "repetition_penalty": 1.0, "ignore_eos": False, "skip_special_tokens": True,
    "spaces_between_special_tokens": True, "regex": None, "n": 1,
    "json_schema": None, "no_stop_trim": False,
}


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

    def get_int(self, key):
        return None

    def close(self):
        pass

    def frontend(self, model):
        return [o for k, o in self.sent if k == f"frontend:{model}"]

    def reports(self):
        return [o for _k, o in self.sent if isinstance(o, MigratedAwayReq)]


def backend_payload(rid, model="model_3", prompt=64, arrival=1234.5, slo=7.5,
                    alg2_seq=854):
    """A request exactly as the engine fetched it off backend:<gpu>:<model>."""
    r = GenerateReqInput(rid=rid, model=model, prompt_len=prompt,
                         arrival_time=arrival, slo=slo, output_len=384,
                         input_ids=[3] * prompt)
    r.origin_input_ids = [3] * prompt
    r.sampling_params = dict(BACKEND_SAMPLING_PARAMS)     # <- the dict form
    r.prefix_indices, r.extend_input_len = [], 0
    r.return_logprob, r.logprob_start_len, r.top_logprobs_num = False, 0, 0
    r.stream, r.lora_path = False, None
    r.alg2_seq, r.alg2_backend_admitted = alg2_seq, False
    return r


def engine(model="model_3", gpu_id=0, staged=(), waiting=(), backend=()):
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
        e.redis_client.queues[f"backend:{gpu_id}:{model}"] = list(backend)
    return e


class CaptureErrors(logging.Handler):
    """Every ERROR the scheduler emits during the eviction."""

    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.records = []

    def emit(self, record):
        self.records.append(record.getMessage())


def evict_capturing(e):
    log = logging.getLogger("sglang.srt.managers.scheduler")
    cap = CaptureErrors()
    log.addHandler(cap)
    try:
        e._evict_all_waiting_requests()
    finally:
        log.removeHandler(cap)
    return cap.records


# ------------------------------------------------------- the exact failure
def test_the_run8225_failure_does_not_recur():
    print("model_3#192: a staged payload with dict sampling_params comes back")
    e = engine(staged=[backend_payload("model_3#192")])
    errors = evict_capturing(e)
    check("no exception reported returning the staged request",
          not any("could not return staged" in m for m in errors))
    check("no staged_return_failed event",
          not any("staged_return_failed" in m for m in errors))
    fe = e.redis_client.frontend("model_3")
    check("returned to the frontend exactly once",
          [o.rid for o in fe] == ["model_3#192"])
    reports = [r for r in e.redis_client.reports()
               if r.reason == "staged-never-admitted"]
    check("its sequence is retired exactly once",
          len(reports) == 1 and reports[0].rids == ["model_3#192"])
    check("the staged list is empty", e._alg2_staged_generation_reqs == [])


def test_the_payload_goes_back_in_its_original_form():
    print("the object returned is the payload that was fetched")
    staged = backend_payload("model_3#192")
    e = engine(staged=[staged])
    evict_capturing(e)
    o = e.redis_client.frontend("model_3")[0]
    check("the same object, not a converted copy", o is staged)
    check("sampling_params is still the backend dict",
          isinstance(o.sampling_params, dict)
          and o.sampling_params["max_new_tokens"] == 384)


def test_this_gpus_sequence_is_cleared():
    print("the returned payload does not carry this GPU's sequence onward")
    e = engine(staged=[backend_payload("model_3#192", alg2_seq=854)])
    evict_capturing(e)
    o = e.redis_client.frontend("model_3")[0]
    check("alg2_seq cleared, so the next GPU issues a fresh one",
          o.alg2_seq is None)
    check("not marked backend-admitted", not o.alg2_backend_admitted)
    check("not a resumed placeholder", not getattr(o, "alg2_resumed", False))


def test_scheduling_fields_survive():
    print("arrival time, SLO, model and prompt survive the handover")
    e = engine(staged=[backend_payload("model_3#192", prompt=97,
                                       arrival=1234.5, slo=7.5)])
    evict_capturing(e)
    o = e.redis_client.frontend("model_3")[0]
    check("arrival_time preserved", o.arrival_time == 1234.5)
    check("slo preserved -- the deadline is unchanged", o.slo == 7.5)
    check("model preserved", o.model == "model_3")
    check("prompt preserved",
          o.prompt_len == 97 and list(o.input_ids) == [3] * 97)


def test_many_staged_payloads_each_returned_once():
    print("several dict-form staged payloads are each returned once")
    rids = [f"model_3#{n}" for n in (192, 193, 194)]
    e = engine(staged=[backend_payload(r, alg2_seq=854 + i)
                       for i, r in enumerate(rids)])
    errors = evict_capturing(e)
    got = [o.rid for o in e.redis_client.frontend("model_3")]
    check("all returned", sorted(got) == sorted(rids))
    check("none returned twice", len(got) == len(set(got)) == 3)
    check("no failures", not any("staged_return_failed" in m for m in errors))
    reports = [r for r in e.redis_client.reports()
               if r.reason == "staged-never-admitted"]
    check("all retired in one report",
          len(reports) == 1 and sorted(reports[0].rids) == sorted(rids))


def test_alongside_the_other_two_paths():
    print("staged, waiting and backend together: each rid returned once")
    staged = backend_payload("model_3#192")
    waiting = backend_payload("model_3#200")
    # The waiting queue holds admitted requests, whose sampling_params is an
    # object; that path still uses the Req converter and must keep working.
    waiting.sampling_params = SimpleNamespace(**{
        **BACKEND_SAMPLING_PARAMS, "stop_strs": None})
    backend = backend_payload("model_3#210")
    e = engine(staged=[staged], waiting=[waiting], backend=[backend])
    errors = evict_capturing(e)
    got = [o.rid for o in e.redis_client.frontend("model_3")]
    check("all three returned",
          sorted(got) == ["model_3#192", "model_3#200", "model_3#210"])
    check("no rid returned twice", len(got) == len(set(got)))
    check("no path failed", not any("could not return" in m for m in errors))
    reasons = sorted(r.reason for r in e.redis_client.reports())
    check("each path reports under its own reason",
          reasons == ["backend-queue-drain", "evicted-to-frontend",
                      "staged-never-admitted"])


# --------------------------------------------- the Req contract is unchanged
def test_the_req_converter_was_not_widened():
    """Option B -- teaching the converter to accept dicts -- was deliberately
    not taken. The converter serves admitted Reqs and its contract stays as
    narrow as it was; the staged path simply stopped calling it."""
    print("the Req -> frontend converter still expects an admitted Req")
    e = engine()
    raised = False
    try:
        e._convert_req_to_frontend_reqs(backend_payload("model_3#192"))
    except AttributeError:
        raised = True
    check("a dict sampling_params is still rejected by the converter", raised)


def main():
    print(__doc__.strip().splitlines()[0])
    print()
    for fn in (test_the_run8225_failure_does_not_recur,
               test_the_payload_goes_back_in_its_original_form,
               test_this_gpus_sequence_is_cleared,
               test_scheduling_fields_survive,
               test_many_staged_payloads_each_returned_once,
               test_alongside_the_other_two_paths,
               test_the_req_converter_was_not_widened):
        fn()
        print()
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
