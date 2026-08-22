#!/usr/bin/env python3
"""The KV ownership trace has to add up, and has to stay out of the way.

D3 run 7 ended with model_2's pool reporting 930,401 tokens held and no running
requests, and the logs could not say whose KV that was. The trace added for that
question decomposes what the pool holds:

    pool_used = live-request-owned + migration-owned + tree-evictable
                + unexplained

These tests pin the arithmetic, that each bucket counts what it should, and
that the whole thing is inert: off unless asked for, and never consulted by a
scheduling decision.
"""

import io
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2] / "prism-research"
sys.path.insert(0, str(REPO / "python"))

from sglang.srt.managers import scheduler as sched_mod  # noqa: E402
from sglang.srt.managers.scheduler import Scheduler  # noqa: E402

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


class Req:
    def __init__(self, rid, prompt=0, output=0, prefix=0, pool_idx=None, seq=None):
        self.rid = rid
        self.origin_input_ids = [1] * prompt
        self.output_ids = [2] * output
        self.prefix_indices = list(range(prefix))
        self.req_pool_idx = pool_idx
        self.alg2_seq = seq
        self.alg2_backend_admitted = seq is not None


def engine(available, total=1000, evictable=0, running=(), waiting=(),
           pending=(), captured=(), inflight=None, trace=True):
    e = Scheduler.__new__(Scheduler)
    e.tp_rank = 0
    e.model_name = "model_2"
    e.gpu_id = 1
    e.engine_id = "1_0"
    e.max_total_num_tokens = total
    e.token_to_kv_pool = SimpleNamespace(available_size=lambda: available)
    e.tree_cache = SimpleNamespace(evictable_size=lambda: evictable)
    e.running_batch = SimpleNamespace(reqs=list(running)) if running else None
    e.waiting_queue = list(waiting)
    e._alg2_pending_adoption = {r.rid: r for r in pending}
    e._v6_captured = list(captured)
    e.current_inflight_req = inflight
    e._kv_own_trace = trace
    return e


def capture_logs(fn):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = sched_mod.logger
    level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        fn()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)
    out = []
    for line in stream.getvalue().splitlines():
        for marker in ("[PAPER-KV-OWN] ", "[PAPER-KV-RID] "):
            if marker not in line:
                continue
            try:
                out.append(json.loads(line.split(marker, 1)[1]))
            except json.JSONDecodeError:
                pass          # a warning line, not a record
    return out


def test_tokens_held_per_request():
    print("what one request is counted as holding")
    e = engine(available=1000)
    check("a request in the pool holds its prompt and its output",
          e._kv_tokens_held(Req("a", prompt=100, output=40, pool_idx=3)) == 140)
    check("one not in the pool holds only its prefix",
          e._kv_tokens_held(Req("b", prompt=100, output=40, prefix=7)) == 7)
    check("a retracted request holds nothing",
          e._kv_tokens_held(Req("c", prompt=100, output=40)) == 0)
    check("a resumed request holds its migrated KV",
          e._kv_tokens_held(Req("d", prompt=6, output=3, prefix=8)) == 8)


def test_a_fully_explained_pool_has_no_orphan():
    print("a pool everyone can account for")
    running = [Req("r1", prompt=100, output=20, pool_idx=1),
               Req("r2", prompt=50, output=10, pool_idx=2)]        # 120 + 60
    pending = [Req("p1", prompt=6, output=3, prefix=8)]            # 8
    captured = [SimpleNamespace(num_tokens=12)]                    # 12
    used = 120 + 60 + 8 + 12
    e = engine(available=1000 - used, running=running, pending=pending,
               captured=captured)
    rows = capture_logs(lambda: e._kv_ownership_snapshot("unit"))
    row = rows[0]
    check("pool_used is total minus available", row["pool_used"] == used)
    check("running is counted", row["owned_running"] == 180)
    check("pending adoption is counted", row["owned_pending_adoption"] == 8)
    check("captured migration KV is counted", row["migration_owned"] == 12)
    check("and nothing is left unexplained", row["unexplained"] == 0)


def test_an_orphan_is_reported_as_one():
    print("a pool holding KV nobody owns")
    running = [Req("r1", prompt=100, output=20, pool_idx=1)]       # 120
    e = engine(available=1000 - 500, running=running)              # 500 used
    row = capture_logs(lambda: e._kv_ownership_snapshot("unit"))[0]
    check("the orphan is the difference", row["unexplained"] == 380)
    check("and the live side is reported separately",
          row["live_owned"] == 120 and row["migration_owned"] == 0)


def test_the_run7_shape():
    """No running requests, a full pool: the shape that has to be legible."""
    print("the D3 run 7 shape")
    e = engine(available=0, total=930401)
    row = capture_logs(lambda: e._kv_ownership_snapshot("unit"))[0]
    check("a full pool with nobody running is entirely unexplained",
          row["unexplained"] == 930401 and row["live_owned"] == 0)
    check("and the request counts say so",
          row["n_running"] == 0 and row["n_waiting"] == 0)


def test_evictable_kv_is_not_an_orphan():
    print("KV the tree cache is holding")
    e = engine(available=800, total=1000, evictable=200)
    row = capture_logs(lambda: e._kv_ownership_snapshot("unit"))[0]
    check("evictable KV explains its own share", row["unexplained"] == 0)
    check("and is reported in its own bucket", row["tree_evictable"] == 200)


def test_rid_events_carry_the_transition():
    print("per-request lifecycle lines")
    e = engine(available=1000)
    req = Req("model_2#1217", prompt=434, output=174, prefix=0, seq=5472)
    rows = capture_logs(
        lambda: e._kv_rid_event("decode_retraction", req, {"extra": 1}))
    row = rows[0]
    check("the transition is named", row["transition"] == "decode_retraction")
    check("the request is identified", row["rid"] == "model_2#1217")
    check("its sequence travels with it", row["alg2_seq"] == 5472)
    check("and the caller's detail is kept", row["extra"] == 1)


def test_the_trace_is_off_by_default_and_inert():
    print("inertness")
    e = engine(available=500, trace=False)
    check("nothing is written when the trace is off",
          capture_logs(lambda: e._kv_ownership_snapshot("unit")) == [])
    check("and no rid line either",
          capture_logs(lambda: e._kv_rid_event("x", Req("a"))) == [])

    broken = engine(available=500)
    broken.token_to_kv_pool = SimpleNamespace(
        available_size=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    capture_logs(lambda: broken._kv_ownership_snapshot("unit"))
    check("a snapshot that fails does not propagate", True)

    source = (REPO / "python/sglang/srt/managers/scheduler.py").read_text()
    body = source.split("def _kv_ownership_snapshot", 1)[1].split("\n    def ", 1)[0]
    check("the snapshot only reads and logs",
          "return" in body and "self.waiting_queue =" not in body
          and "self.running_batch =" not in body)


def main():
    test_tokens_held_per_request()
    test_a_fully_explained_pool_has_no_orphan()
    test_an_orphan_is_reported_as_one()
    test_the_run7_shape()
    test_evictable_kv_is_not_an_orphan()
    test_rid_events_carry_the_transition()
    test_the_trace_is_off_by_default_and_inert()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for name in FAIL:
            print(f"  FAILED: {name}")
        raise SystemExit(1)
    print("ALL KV OWNERSHIP TRACE TESTS PASSED")


if __name__ == "__main__":
    main()
