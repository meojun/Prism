#!/usr/bin/env python3
"""Deterministic unit tests for the corrected decode-token-rate estimator.

Estimator behaviour only -- no GPU, no scheduler, no Algorithm 1 objective.
Time is injected explicitly so every assertion is exact.
"""
import sys, types, unittest
sys.path.insert(0, "/workspace/prism-exp/prism-research/python")
from sglang.multi_model.scheduling.model_queue_tracker import ModelQueueTracker
from sglang.multi_model.scheduling.policy.kvpr_global import KVPRGlobalPolicy

W = 30.0


def tracker(events=()):
    t = ModelQueueTracker("m")
    for ts, n in events:
        t.record_decode_tokens(ts, n)
    return t


class TestA_SteadyProduction(unittest.TestCase):
    def test_constant_rate(self):
        """100 tok/s reported every second for 30 s -> exactly 100 tok/s."""
        t = tracker((float(i), 100) for i in range(1, 31))
        self.assertAlmostEqual(t.decode_token_rate(30.0, W), 100.0)

    def test_denominator_is_the_window_not_event_span(self):
        """Half a window of production reads as half the rate -- no inflation."""
        t = tracker((float(i), 100) for i in range(1, 16))
        self.assertAlmostEqual(t.decode_token_rate(30.0, W), 50.0)


class TestB_IdleExpiration(unittest.TestCase):
    def test_decays_then_reaches_zero(self):
        t = tracker((float(i), 100) for i in range(1, 31))
        self.assertAlmostEqual(t.decode_token_rate(30.0, W), 100.0)
        # at t=45 the window is [15,45]: reports 15..30 survive, 1..14 aged out
        self.assertAlmostEqual(t.decode_token_rate(45.0, W), 1600 / W)
        # the last report (t=30) sits exactly on the boundary at now=60 and is
        # still counted; one epsilon later the window is empty
        self.assertAlmostEqual(t.decode_token_rate(60.0, W), 100 / W)
        self.assertAlmostEqual(t.decode_token_rate(60.0 + 1e-6, W), 0.0)
        self.assertAlmostEqual(t.decode_token_rate(600.0, W), 0.0)   # stays zero

    def test_no_stale_value_persists(self):
        """The old failure mode: a cached scalar that never ages."""
        t = tracker([(1.0, 5000)])
        self.assertGreater(t.decode_token_rate(2.0, W), 0.0)
        self.assertEqual(t.decode_token_rate(1.0 + W + 1e-6, W), 0.0)


class TestC_MigrationPause(unittest.TestCase):
    def test_pause_and_resume_is_pure_window_arithmetic(self):
        """Produce 10 s, pause 5 s (no reports), resume 10 s.

        The rate must equal (tokens still inside the window)/window at every
        instant -- no artificial zero during the pause, no spike on resume.
        """
        ev = [(float(i), 100) for i in range(1, 11)]
        ev += [(float(i), 100) for i in range(16, 26)]
        # only what has actually been reported by ``now`` is in the tracker
        at = lambda now: tracker(e for e in ev if e[0] <= now)
        # during the pause: pre-pause tokens still in window, none expired yet
        self.assertAlmostEqual(at(13.0).decode_token_rate(13.0, W), 1000 / W)
        # after resume: both bursts inside the window, nothing lost or doubled
        self.assertAlmostEqual(at(25.0).decode_token_rate(25.0, W), 2000 / W)
        # the first post-resume value is a smooth continuation, not a jump
        self.assertAlmostEqual(at(16.0).decode_token_rate(16.0, W),
                               at(15.0).decode_token_rate(15.0, W) + 100 / W)


class TestD_DeactivateReactivate(unittest.TestCase):
    def test_rate_independent_of_reporting_gap(self):
        """Timer bookkeeping cannot move the rate; only token counts can.

        Same tokens at the same times, delivered either as one report per
        second or as a single batched report -- the window rate is identical.
        Under the old achieved-throughput scalar these differed by the length
        of the inactive gap.
        """
        fine = tracker((float(i), 100) for i in range(1, 11))
        # identical token total, but the reporting cadence is irregular
        coarse = tracker([(10.0, 1000)])
        self.assertAlmostEqual(fine.decode_token_rate(10.0, W),
                               coarse.decode_token_rate(10.0, W))

    def test_zero_reports_change_nothing(self):
        """Reports carrying no tokens (deactivated engine) are inert."""
        a = tracker([(1.0, 300)])
        b = tracker([(1.0, 300)] + [(float(i), 0) for i in range(2, 20)])
        self.assertAlmostEqual(a.decode_token_rate(20.0, W),
                               b.decode_token_rate(20.0, W))


class TestE_MixedInputDecode(unittest.TestCase):
    def test_token_rate_is_exact_sum_of_components(self):
        info = {"m": {"cell_size": 131072.0, "model_size": 15.0}}
        pol = KVPRGlobalPolicy(num_gpus=2, gpu_mem=79.25, model_weights_info=info,
                               workers_per_gpu=1, rate_window=W,
                               tpot_slo_s={"m": 0.05})
        now = 1000.0
        q = tracker([(now - 5.0, 600)])                      # 600 tok in window
        q.received_reqs = {                                   # 400 input tok in window
            "r1": types.SimpleNamespace(arrival_time=now - 2.0, prompt_len=400,
                                        is_warmup=False),
            "r2": types.SimpleNamespace(arrival_time=now - 100.0, prompt_len=9999,
                                        is_warmup=False),     # outside the window
            "r3": types.SimpleNamespace(arrival_time=now - 1.0, prompt_len=7777,
                                        is_warmup=True),      # warmup, excluded
        }
        import sglang.multi_model.scheduling.policy.kvpr_global as kg
        real, kg.time.time = kg.time.time, lambda: now
        try:
            out = pol._weighted_token_rates({"m": q}, ["m"])["m"]
        finally:
            kg.time.time = real
        self.assertAlmostEqual(out["input_token_rate"], 400 / W)
        self.assertAlmostEqual(out["decode_token_rate"], 600 / W)
        self.assertAlmostEqual(out["token_rate"], 1000 / W)
        self.assertAlmostEqual(out["weighted_token_rate"],
                               (1000 / W) * 131072.0 / 0.05)


class TestF_WindowBoundary(unittest.TestCase):
    def test_expiry_is_exact_and_half_open(self):
        t = tracker([(100.0, 60)])
        self.assertAlmostEqual(t.decode_token_rate(100.0 + W - 1e-9, W), 60 / W)
        self.assertAlmostEqual(t.decode_token_rate(100.0 + W, W), 60 / W)
        self.assertAlmostEqual(t.decode_token_rate(100.0 + W + 1e-6, W), 0.0)

    def test_events_expire_in_order(self):
        t = tracker([(100.0, 10), (110.0, 20), (120.0, 30)])
        self.assertAlmostEqual(t.decode_token_rate(125.0, W), 60 / W)
        self.assertAlmostEqual(t.decode_token_rate(131.0, W), 50 / W)
        self.assertAlmostEqual(t.decode_token_rate(141.0, W), 30 / W)
        self.assertEqual(len(t.decode_token_events), 1)   # pruned, not scanned


if __name__ == "__main__":
    unittest.main(verbosity=2)
