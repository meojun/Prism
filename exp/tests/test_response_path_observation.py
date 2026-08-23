#!/usr/bin/env python3
"""Observation at the six response-path boundaries.

1,204 requests in cal-0p07-s0 received no response while every engine reported
itself alive and empty, the Algorithm 2 ledger was clean, only ~340 requests had
ever gone back to the frontend, and the client logged zero descriptor failures.
The requests were finished as far as the server was concerned and absent as far
as the client was concerned, and no record spans the two.

So one marker, `[PAPER-RESP-OBS]`, at each boundary a response crosses:

  1 engine_output      the engine produced a final output for the request
  2 stream_output_send handed to the detokenizer
  3 detokenizer_send   detokenized and passed to the request handler
  4 handler_recv       received by the handler and queued for the generator
  5 http_final_yield   final chunk about to reach the client
  6 stream_abandoned   the stream was given up instead

A rid present at boundary N and absent at N+1 names the boundary that lost it.
These observers must not change behaviour -- most of this file checks that.
"""

import io
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2] / "prism-research"
sys.path.insert(0, str(REPO / "python"))

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


def capture(fn):
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    root = logging.getLogger()
    old = root.level
    root.setLevel(logging.INFO)
    root.addHandler(h)
    try:
        fn()
    finally:
        root.removeHandler(h)
        root.setLevel(old)
    return [json.loads(l.split("[PAPER-RESP-OBS] ", 1)[1])
            for l in buf.getvalue().splitlines() if "[PAPER-RESP-OBS] " in l]


# ------------------------------------------------------------ engine side
from sglang.srt.managers.scheduler import Scheduler  # noqa: E402


def eng():
    e = Scheduler.__new__(Scheduler)
    e.model_name, e.gpu_id = "model_3", 0
    return e


def test_engine_observer_records_the_required_fields():
    print("engine: every field the diagnosis needs")
    e = eng()
    recs = capture(lambda: e._resp_obs("engine_output", "model_3#526",
                                       final=True, n=42))
    check("one record", len(recs) == 1)
    r = recs[0]
    for f in ("boundary", "time", "rid", "model", "final", "n", "exc"):
        check(f"records {f}", f in r)
    check("values carried",
          r["rid"] == "model_3#526" and r["final"] is True and r["n"] == 42)
    check("model defaults to the engine's", r["model"] == "model_3")
    check("gpu recorded", r["gpu_id"] == 0)


def test_engine_observer_never_raises():
    print("engine: cannot raise, even on a bare object")
    bare = Scheduler.__new__(Scheduler)
    raised = []
    try:
        bare._resp_obs("engine_output", "x")
    except Exception as exc:
        raised.append(repr(exc))
    check("no exception on a bare Scheduler", not raised)

    class Unserialisable:
        def __repr__(self):
            raise RuntimeError("boom")
    e = eng()
    raised2 = []
    try:
        e._resp_obs("engine_output", Unserialisable())
    except Exception as exc:
        raised2.append(repr(exc))
    check("no exception on an unserialisable rid", not raised2)


def test_engine_observer_changes_no_state():
    print("engine: reads only")
    e = eng()
    before = dict(e.__dict__)
    capture(lambda: e._resp_obs("engine_output", "r", final=True, n=1))
    check("no attribute added or changed", e.__dict__ == before)


# ----------------------------------------------------------- handler side
from sglang.multi_model.request_handler_worker_pool import _resp_obs  # noqa: E402


def test_handler_observer_records_and_extends():
    print("handler: fields plus per-boundary extras")
    recs = capture(lambda: _resp_obs("handler_recv", "model_3#526",
                                     model="model_3", final=True, n=7,
                                     queued=True))
    check("one record", len(recs) == 1)
    r = recs[0]
    check("boundary named", r["boundary"] == "handler_recv")
    check("rid and model", r["rid"] == "model_3#526" and r["model"] == "model_3")
    check("final and length", r["final"] is True and r["n"] == 7)
    check("extras are carried through", r.get("queued") is True)


def test_abandonment_is_distinguishable_from_delivery():
    print("handler: an abandoned stream is not a delivered one")
    recs = capture(lambda: (
        _resp_obs("http_final_yield", "a", model="m", final=True),
        _resp_obs("stream_abandoned", "b", model="m", final=False,
                  exc="client_disconnected")))
    by = {r["rid"]: r for r in recs}
    check("delivery is final with no exception",
          by["a"]["boundary"] == "http_final_yield"
          and by["a"]["final"] is True and by["a"]["exc"] is None)
    check("abandonment is not final and names the cause",
          by["b"]["boundary"] == "stream_abandoned"
          and by["b"]["final"] is False
          and by["b"]["exc"] == "client_disconnected")


def test_handler_observer_never_raises():
    print("handler: cannot raise")
    class Bad:
        def __repr__(self):
            raise RuntimeError("no")
    raised = []
    for args in (("b", Bad()), ("b", "r")):
        try:
            _resp_obs(*args, model=Bad())
        except Exception as exc:
            raised.append(repr(exc))
    check("no exception from unserialisable arguments", not raised)


# ------------------------------------------------------- the whole crossing
def test_a_rid_can_be_followed_across_all_six():
    print("a rid is followable across every boundary")
    e = eng()
    recs = capture(lambda: (
        e._resp_obs("engine_output", "model_3#526", final=True, n=12),
        e._resp_obs("stream_output_send", "model_3#526", final=True),
        _resp_obs("detokenizer_send", "model_3#526", final=True, n=30),
        _resp_obs("handler_recv", "model_3#526", model="model_3", final=True),
        _resp_obs("http_final_yield", "model_3#526", model="model_3",
                  final=True)))
    seen = [r["boundary"] for r in recs if r["rid"] == "model_3#526"]
    check("all five delivery boundaries in order",
          seen == ["engine_output", "stream_output_send", "detokenizer_send",
                   "handler_recv", "http_final_yield"])
    check("timestamps are non-decreasing",
          all(a["time"] <= b["time"] for a, b in zip(recs, recs[1:])))


def test_a_gap_names_the_losing_boundary():
    """The diagnostic property: the last boundary a rid reaches is the answer."""
    print("a rid that stops midway names where it stopped")
    e = eng()
    recs = capture(lambda: (
        e._resp_obs("engine_output", "lost", final=True, n=5),
        e._resp_obs("stream_output_send", "lost", final=True),
        _resp_obs("detokenizer_send", "lost", final=True)))
        # nothing at handler_recv or http_final_yield
    seen = [r["boundary"] for r in recs if r["rid"] == "lost"]
    check("it reached the detokenizer", "detokenizer_send" in seen)
    check("and no further", "handler_recv" not in seen
          and "http_final_yield" not in seen)
    check("so the loss is between detokenizer and handler",
          seen[-1] == "detokenizer_send")


def test_marker_is_greppable_and_parses():
    print("the marker is one line of JSON per event")
    e = eng()
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    root = logging.getLogger(); root.setLevel(logging.INFO); root.addHandler(h)
    try:
        e._resp_obs("engine_output", "r", final=True, n=1)
    finally:
        root.removeHandler(h)
    lines = [l for l in buf.getvalue().splitlines() if l.strip()]
    check("one line", len(lines) == 1)
    check("marker present", "[PAPER-RESP-OBS] " in lines[0])
    check("the remainder is valid JSON",
          isinstance(json.loads(lines[0].split("[PAPER-RESP-OBS] ", 1)[1]), dict))


def main():
    for fn in (
        test_engine_observer_records_the_required_fields,
        test_engine_observer_never_raises,
        test_engine_observer_changes_no_state,
        test_handler_observer_records_and_extends,
        test_abandonment_is_distinguishable_from_delivery,
        test_handler_observer_never_raises,
        test_a_rid_can_be_followed_across_all_six,
        test_a_gap_names_the_losing_boundary,
        test_marker_is_greppable_and_parses,
    ):
        fn()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for n in FAIL:
        print("  FAILED:", n)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
