#!/usr/bin/env python3
"""Stage 0 -- window semantics tests (30 s vs 60 s). No GPU, exact arithmetic."""
import sys, types, unittest
sys.path.insert(0, "/workspace/prism-exp/prism-research/python")
from sglang.multi_model.scheduling.model_queue_tracker import ModelQueueTracker
from sglang.multi_model.scheduling.policy.kvpr_global import KVPRGlobalPolicy
import sglang.multi_model.scheduling.policy.kvpr_global as kg

INFO = {"m": {"cell_size": 131072.0, "model_size": 15.0},
        "n": {"cell_size": 57344.0, "model_size": 14.0}}


def tracker(events):
    t = ModelQueueTracker("m")
    for ts, n in events:
        t.record_decode_tokens(ts, n)
    return t


def policy(window):
    return KVPRGlobalPolicy(num_gpus=2, gpu_mem=79.25, model_weights_info=INFO,
                            workers_per_gpu=1, rate_window=window,
                            tpot_slo_s={"m": 0.05, "n": 0.05})


def rates(pol, q, now):
    real, kg.time.time = kg.time.time, lambda: now
    try:
        return pol._weighted_token_rates({"m": q}, ["m"])["m"]
    finally:
        kg.time.time = real


class TestExpiryBoundaries(unittest.TestCase):
    def test_30s_excludes_older_than_30s(self):
        # a fresh tracker per query: pruning is destructive, so a single tracker
        # may only ever be queried with non-decreasing `now` (see
        # TestMonotonicTimeAssumption below)
        ev = [(100.0, 300), (140.0, 600)]
        self.assertAlmostEqual(tracker(ev[:1]).decode_token_rate(130.0, 30.0), 300 / 30)
        self.assertAlmostEqual(tracker(ev).decode_token_rate(170.0, 30.0), 600 / 30)

    def test_60s_excludes_older_than_60s(self):
        ev = [(100.0, 300), (140.0, 600)]
        # at t=155 (cutoff 95) a 60 s window holds BOTH reports; a 30 s window
        # (cutoff 125) holds only the newer one
        self.assertAlmostEqual(tracker(ev).decode_token_rate(155.0, 60.0), 900 / 60)
        self.assertAlmostEqual(tracker(ev).decode_token_rate(155.0, 30.0), 600 / 30)
        # at t=170 the 60 s cutoff is 110, which now also excludes the t=100 report
        self.assertAlmostEqual(tracker(ev).decode_token_rate(170.0, 60.0), 600 / 60)
        self.assertAlmostEqual(tracker(ev).decode_token_rate(200.0, 60.0), 600 / 60)
        self.assertAlmostEqual(tracker(ev).decode_token_rate(200.1, 60.0), 0.0)
        self.assertAlmostEqual(tracker(ev).decode_token_rate(240.1, 60.0), 0.0)

    def test_longer_window_never_drops_an_event_the_short_one_keeps(self):
        ev = [(float(i), 10) for i in range(1, 121)]
        for now in (60.0, 90.0, 120.0):
            seen = [e for e in ev if e[0] <= now]      # only what has been reported
            a = sum(n for ts, n in seen if ts >= now - 30.0)
            b = sum(n for ts, n in seen if ts >= now - 60.0)
            self.assertGreaterEqual(b, a)
            self.assertAlmostEqual(tracker(seen).decode_token_rate(now, 30.0), a / 30.0)
            self.assertAlmostEqual(tracker(seen).decode_token_rate(now, 60.0), b / 60.0)


class TestBothComponentsShareTheWindow(unittest.TestCase):
    """Requirement 8.3 -- one configured window drives input AND decode."""

    def _q(self, now):
        q = tracker([(now - 45.0, 900), (now - 10.0, 300)])
        q.received_reqs = {
            "old": types.SimpleNamespace(arrival_time=now - 45.0, prompt_len=450,
                                         is_warmup=False),
            "new": types.SimpleNamespace(arrival_time=now - 10.0, prompt_len=150,
                                         is_warmup=False)}
        return q

    def test_30s_sees_only_the_recent_event_on_both_sides(self):
        now = 1000.0
        out = rates(policy(30.0), self._q(now), now)
        self.assertAlmostEqual(out["input_token_rate"], 150 / 30)
        self.assertAlmostEqual(out["decode_token_rate"], 300 / 30)
        self.assertAlmostEqual(out["token_rate"], 450 / 30)

    def test_60s_sees_both_events_on_both_sides(self):
        now = 1000.0
        out = rates(policy(60.0), self._q(now), now)
        self.assertAlmostEqual(out["input_token_rate"], 600 / 60)
        self.assertAlmostEqual(out["decode_token_rate"], 1200 / 60)
        self.assertAlmostEqual(out["token_rate"], 1800 / 60)

    def test_neither_component_uses_a_different_window_than_the_other(self):
        """The 45 s-old event must be in or out of BOTH terms, never one."""
        now = 1000.0
        for w in (30.0, 60.0):
            out = rates(policy(w), self._q(now), now)
            inp_has_old = out["input_token_rate"] * w > 150 + 1e-9
            dec_has_old = out["decode_token_rate"] * w > 300 + 1e-9
            self.assertEqual(inp_has_old, dec_has_old, f"window {w}: components disagree")


class TestWindowChangesNothingElse(unittest.TestCase):
    """Requirement 8.4 -- the knob must not move tau, cooldown, or the formula."""

    def test_tau_cooldown_slo_unchanged(self):
        a, b = policy(30.0), policy(60.0)
        self.assertEqual(a.tau, b.tau)
        self.assertEqual(a.migration_cooldown, b.migration_cooldown)
        self.assertEqual(a.tpot_slo_s, b.tpot_slo_s)
        self.assertEqual(a.gpu_mem, b.gpu_mem)

    def test_weighted_formula_identical_given_identical_rates(self):
        """Same token_rate in -> same weighted_token_rate out, whatever the window."""
        now = 1000.0
        # 600 decode tokens over 30 s == 1200 over 60 s: both give 20 tok/s
        q30 = tracker([(now - 5.0, 600)])
        q60 = tracker([(now - 5.0, 1200)])
        o30 = rates(policy(30.0), q30, now)
        o60 = rates(policy(60.0), q60, now)
        self.assertAlmostEqual(o30["token_rate"], o60["token_rate"])
        self.assertAlmostEqual(o30["weighted_token_rate"], o60["weighted_token_rate"])
        self.assertAlmostEqual(o30["token_size"], o60["token_size"])
        self.assertAlmostEqual(o30["tpot_slo_s"], o60["tpot_slo_s"])

    def test_shared_kv_and_kvpr_untouched_by_window(self):
        mapping = {0: ["m"], 1: ["n"]}
        self.assertAlmostEqual(policy(30.0)._shared_kv(mapping, 0),
                               policy(60.0)._shared_kv(mapping, 0))


class TestHandCalculatedSynthetic(unittest.TestCase):
    """Requirement 8.5 -- match values computed by hand."""

    def test_known_numbers(self):
        now = 500.0
        q = tracker([(now - 50.0, 2000), (now - 20.0, 1000)])  # chronological
        q.received_reqs = {"r": types.SimpleNamespace(arrival_time=now - 20.0,
                                                      prompt_len=500, is_warmup=False)}
        # 30 s: decode 1000/30=33.333, input 500/30=16.667, total 50.0
        o = rates(policy(30.0), q, now)
        self.assertAlmostEqual(o["decode_token_rate"], 33.3333333, places=5)
        q = tracker([(now - 50.0, 2000), (now - 20.0, 1000)])   # fresh: 30 s pruned it
        q.received_reqs = {"r": types.SimpleNamespace(arrival_time=now - 20.0,
                                                      prompt_len=500, is_warmup=False)}
        self.assertAlmostEqual(o["input_token_rate"], 16.6666667, places=5)
        self.assertAlmostEqual(o["token_rate"], 50.0, places=5)
        # 60 s: decode 3000/60=50.0, input 500/60=8.3333, total 58.3333
        o = rates(policy(60.0), q, now)
        self.assertAlmostEqual(o["decode_token_rate"], 50.0, places=5)
        self.assertAlmostEqual(o["input_token_rate"], 8.3333333, places=5)
        self.assertAlmostEqual(o["token_rate"], 58.3333333, places=5)
        # weighted = token_rate * cell_size / tpot_slo
        self.assertAlmostEqual(o["weighted_token_rate"],
                               o["token_rate"] * 131072.0 / 0.05, places=6)
        self.assertAlmostEqual(o["weighted_token_rate"] / 1e6, 152.91733, places=4)


class TestMonotonicTimeAssumption(unittest.TestCase):
    """Documented property, surfaced while writing these tests.

    Pruning is destructive: expired events are popped, not filtered. The
    estimator is therefore only correct when queried with non-decreasing time
    and fed chronologically ordered events. Both hold in the runtime -- the
    controller evaluates once per 5 s cycle on wall clock, and the engine
    reports in order -- but the assumption is recorded here rather than left
    implicit.
    """

    def test_pruning_is_destructive(self):
        t = tracker([(100.0, 300), (140.0, 600)])
        self.assertAlmostEqual(t.decode_token_rate(170.0, 30.0), 600 / 30)
        # querying backwards afterwards cannot resurrect the pruned event
        self.assertAlmostEqual(t.decode_token_rate(130.0, 30.0), 600 / 30)

    def test_forward_queries_are_consistent(self):
        ev = [(float(i), 10) for i in range(1, 61)]
        t = tracker(ev)
        prev = None
        for now in (60.0, 70.0, 80.0, 95.0, 130.0):
            v = t.decode_token_rate(now, 30.0)
            if prev is not None:
                self.assertLessEqual(v, prev + 1e-9)   # monotonically decaying
            prev = v
        self.assertAlmostEqual(t.decode_token_rate(200.0, 30.0), 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
