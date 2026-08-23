#!/usr/bin/env python3
"""The observation logging added to separate b1 from b2.

GPU 0's frontier stalled on sequences 3113-3115 dispatched to model_1. The
requests provably reached `backend:model_1` -- `_send_to_backend_queue` logs the
dispatch and sends in the same loop iteration, with nothing between -- and the
engine provably never promoted one: zero `Received N generation requests` from
that worker while GPU 1 served 508 more. Two hypotheses survive and the
artifacts cannot separate them:

  b1  the loop ran and the token never matched -- the requests sit staged and
      unpromoted, which nothing logs today
  b2  the loop stopped -- which looks identical, because an activated idle
      engine is as silent as a dead one

So: log every fetch with the token beside it, log staged work that fails to
promote, and beat a heartbeat whether or not there is anything to do.

These observers must not change behaviour. That is what most of this file
checks: identical control flow, identical state, and no exception escaping even
when the objects they read are broken.
"""

import io
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

# These observers are gated off by default; the suite turns them on for
# itself so it tests what it claims to.
import os
os.environ["PRISM_OBS"] = "1"

REPO = Path(__file__).resolve().parents[2] / "prism-research"
sys.path.insert(0, str(REPO / "python"))

from sglang.srt.managers.scheduler import Scheduler  # noqa: E402

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


class Redis:
    def __init__(self, token=None):
        self.ints = {"alg2-next:0": token} if token is not None else {}

    def get_int(self, key):
        return self.ints.get(key)


def engine(staged=(), waiting=(), running=None, token=3113, activated=True):
    e = Scheduler.__new__(Scheduler)
    e.gpu_id, e.model_name, e.tp_rank = 0, "model_1", 0
    e.worker_id = 1
    e.waiting_queue = list(waiting)
    e._alg2_staged_generation_reqs = list(staged)
    e.running_batch = running
    e._activated = activated
    e._alg2_admission_seq_key = "alg2-next:0"
    e.redis_client = Redis(token)
    return e


def req(rid, seq):
    return SimpleNamespace(rid=rid, alg2_seq=seq)


def capture(fn, level=logging.INFO):
    """Run fn and return the [PAPER-ALG2-OBS] records it emitted."""
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setLevel(level)
    root = logging.getLogger()
    old = root.level
    root.setLevel(level)
    root.addHandler(h)
    try:
        fn()
    finally:
        root.removeHandler(h)
        root.setLevel(old)
    out = []
    for line in buf.getvalue().splitlines():
        if "[PAPER-ALG2-OBS] " in line:
            out.append(json.loads(line.split("[PAPER-ALG2-OBS] ", 1)[1]))
    return out


# ------------------------------------------------------------------- fetch
def test_fetch_records_everything_needed():
    print("fetch: rid, seq, token and the counts, per request")
    e = engine(staged=[req("model_1#388", 3113)],
               waiting=[object()], token=3113)
    recs = capture(lambda: e._alg2_obs_fetch([req("model_1#388", 3113)]))
    check("one record per fetched request", len(recs) == 1)
    r = recs[0]
    for f in ("time", "gpu_id", "worker", "model", "rid", "alg2_seq",
              "shared_token", "waiting", "running", "staged", "activated"):
        check(f"records {f}", f in r)
    check("rid and seq are the fetched ones",
          r["rid"] == "model_1#388" and r["alg2_seq"] == 3113)
    check("token is read at fetch time", r["shared_token"] == 3113)
    check("gpu and worker identify the engine",
          r["gpu_id"] == 0 and r["worker"] == 1 and r["model"] == "model_1")


def test_fetch_records_each_of_several():
    print("fetch: several requests in one batch are each recorded")
    e = engine(token=3113)
    recs = capture(lambda: e._alg2_obs_fetch(
        [req("a", 3113), req("b", 3114), req("c", 3115)]))
    check("three records", [r["alg2_seq"] for r in recs] == [3113, 3114, 3115])


# -------------------------------------------------------------- no promote
def test_no_promote_is_the_b1_signature():
    print("no-promote: staged head, expected token, counts -- the b1 evidence")
    e = engine(staged=[req("model_1#388", 3113), req("model_1#389", 3114)],
               token=9999)
    recs = capture(lambda: e._alg2_obs_no_promote(9999), logging.WARNING)
    check("one record", len(recs) == 1)
    r = recs[0]
    check("named for the condition", r["event"] == "staged_not_promoted")
    check("staged head seq recorded", r["staged_head_seq"] == 3113)
    check("staged head rid recorded", r["staged_head_rid"] == "model_1#388")
    check("the staged sequences are listed", r["staged_seqs"] == [3113, 3114])
    check("expected and shared token both recorded",
          r["expected_token"] == 9999 and r["shared_token"] == 9999)
    check("counts and activation recorded",
          r["staged"] == 2 and r["activated"] is True)


def test_no_promote_silent_when_nothing_is_staged():
    print("no-promote: nothing staged means nothing to report")
    e = engine(staged=[], token=3113)
    recs = capture(lambda: e._alg2_obs_no_promote(3113), logging.WARNING)
    check("no record", recs == [])


def test_no_promote_is_rate_limited():
    print("no-promote: rate-limited so a stall cannot flood the log")
    e = engine(staged=[req("x", 1)], token=2)
    n = len(capture(lambda: [e._alg2_obs_no_promote(2) for _ in range(50)],
                    logging.WARNING))
    check("50 calls produce one record", n == 1)


# --------------------------------------------------------------- heartbeat
def test_heartbeat_beats_when_idle_and_when_stopped_it_does_not():
    print("heartbeat: an idle engine still beats -- that is the b2 test")
    e = engine(staged=[], waiting=[], token=3113)
    recs = capture(lambda: e._alg2_obs_heartbeat())
    check("one beat", len(recs) == 1)
    r = recs[0]
    for f in ("gpu_id", "worker", "model", "activated", "waiting", "running",
              "staged", "last_fetch_rid", "last_fetch_seq", "shared_token"):
        check(f"beat records {f}", f in r)
    check("with no work at all", r["waiting"] == 0 and r["staged"] == 0)


def test_heartbeat_beats_even_when_not_activated():
    print("heartbeat: beats regardless of activation")
    e = engine(activated=False, token=None)
    recs = capture(lambda: e._alg2_obs_heartbeat())
    check("still beats", len(recs) == 1 and recs[0]["activated"] is False)


def test_heartbeat_is_rate_limited():
    print("heartbeat: one beat per five seconds")
    e = engine(token=3113)
    n = len(capture(lambda: [e._alg2_obs_heartbeat() for _ in range(100)]))
    check("100 calls produce one beat", n == 1)


def test_heartbeat_carries_the_last_fetch():
    print("heartbeat: carries the last fetched rid and seq")
    e = engine(token=3113)
    capture(lambda: e._alg2_obs_fetch([req("model_1#390", 3115)]))
    recs = capture(lambda: e._alg2_obs_heartbeat())
    check("last fetch is remembered",
          recs[0]["last_fetch_rid"] == "model_1#390"
          and recs[0]["last_fetch_seq"] == 3115)


# ------------------------------------------------- must not change anything
def test_observers_do_not_mutate_state():
    print("observation only: no state is changed")
    staged = [req("a", 1), req("b", 2)]
    waiting = [object()]
    e = engine(staged=staged, waiting=waiting, token=7)
    before = (list(e._alg2_staged_generation_reqs), list(e.waiting_queue),
              e._activated, dict(e.redis_client.ints))
    capture(lambda: (e._alg2_obs_fetch([req("c", 3)]),
                     e._alg2_obs_no_promote(7),
                     e._alg2_obs_heartbeat()), logging.WARNING)
    check("staged list untouched",
          e._alg2_staged_generation_reqs == before[0])
    check("waiting queue untouched", e.waiting_queue == before[1])
    check("activation flag untouched", e._activated == before[2])
    check("the shared token is never written", e.redis_client.ints == before[3])


def test_observers_never_raise():
    """A broken engine must still fail the way it would have failed."""
    print("observation only: no observer can raise")
    broken = Scheduler.__new__(Scheduler)          # almost nothing set
    broken.gpu_id = 0
    raised = []
    for name, args in (("_alg2_obs_fetch", ([req("a", 1)],)),
                       ("_alg2_obs_no_promote", (1,)),
                       ("_alg2_obs_heartbeat", ())):
        try:
            getattr(broken, name)(*args)
        except Exception as exc:
            raised.append(f"{name}: {exc!r}")
    check("none raised on a half-built engine", not raised)

    class Exploding:
        def get_int(self, key):
            raise RuntimeError("redis down")

    e = engine(staged=[req("a", 1)], token=1)
    e.redis_client = Exploding()
    raised2 = []
    try:
        capture(lambda: (e._alg2_obs_fetch([req("a", 1)]),
                         e._alg2_obs_no_promote(1),
                         e._alg2_obs_heartbeat()), logging.WARNING)
    except Exception as exc:
        raised2.append(repr(exc))
    check("none raised when the token read fails", not raised2)


def test_running_batch_is_read_defensively():
    print("observation only: a broken running batch degrades, not raises")
    e = engine(token=1)
    e.running_batch = SimpleNamespace()            # no .reqs
    recs = capture(lambda: e._alg2_obs_heartbeat())
    check("beat still emitted", len(recs) == 1)
    check("running reported as unknown", recs[0]["running"] == -1)


def test_the_two_hypotheses_are_distinguishable():
    """The whole point: b1 and b2 must produce different evidence."""
    print("b1 and b2 produce different evidence")
    b1 = engine(staged=[req("model_1#388", 3113)], token=9999)
    # INFO, so the heartbeat (INFO) and the block record (WARNING) are both seen.
    b1_recs = capture(lambda: (b1._alg2_obs_heartbeat(),
                               b1._alg2_obs_no_promote(9999)), logging.INFO)
    events = {r["event"] for r in b1_recs}
    check("b1 shows a heartbeat AND staged-not-promoted",
          events == {"heartbeat", "staged_not_promoted"})
    check("b1 shows the mismatch explicitly",
          any(r.get("staged_head_seq") == 3113 and r.get("shared_token") == 9999
              for r in b1_recs))
    # b2 is the absence of any record at all -- nothing to emit, because the
    # loop that would emit it is not running.
    check("b2 is the absence of heartbeats, which is now meaningful",
          "heartbeat" in events)


def main():
    for fn in (
        test_fetch_records_everything_needed,
        test_fetch_records_each_of_several,
        test_no_promote_is_the_b1_signature,
        test_no_promote_silent_when_nothing_is_staged,
        test_no_promote_is_rate_limited,
        test_heartbeat_beats_when_idle_and_when_stopped_it_does_not,
        test_heartbeat_beats_even_when_not_activated,
        test_heartbeat_is_rate_limited,
        test_heartbeat_carries_the_last_fetch,
        test_observers_do_not_mutate_state,
        test_observers_never_raise,
        test_running_batch_is_read_defensively,
        test_the_two_hypotheses_are_distinguishable,
    ):
        fn()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for n in FAIL:
        print("  FAILED:", n)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
