#!/usr/bin/env python3
"""Targeted tests for migration-lifecycle failure containment.

Deterministic, no GPU. Each test forces one failure path and asserts the
containment property, not merely that nothing crashed.
"""
import sys, types, unittest
sys.path.insert(0, "/workspace/prism-exp/prism-research/python")
import sglang.multi_model.scheduling.gpu.gpu_scheduler as G

GS = G.GPUScheduler


def scheduler(gpu_id=0):
    """A GPUScheduler with only the attributes the code under test touches."""
    s = object.__new__(GS)
    s.gpu_id = gpu_id
    s._shutdown_reason = None
    from threading import Event
    s._shutdown_event = Event()
    # shutdown() also joins the receiver thread and closes redis; neither is
    # under test here, so give it the shapes it dereferences.
    s._receiver_thread = None
    s.redis_client = None
    s.worker_pool = None
    return s


class TestShutdownReasonRecorded(unittest.TestCase):
    def test_normal_shutdown_is_labelled_normal(self):
        s = scheduler()
        s.shutdown()
        self.assertEqual(s._shutdown_reason, GS.NORMAL_SHUTDOWN)
        self.assertTrue(s._shutdown_event.is_set())

    def test_each_alg2_tripwire_reason_is_distinct_and_recorded(self):
        for reason in ("alg2_backend_admit_order_violation",
                       "alg2_prefill_order_violation",
                       "alg2_prefill_completion_violation"):
            s = scheduler()
            s._request_shutdown(reason)
            self.assertEqual(s._shutdown_reason, reason)
            self.assertTrue(s._shutdown_event.is_set())

    def test_first_reason_wins(self):
        """A tripwire followed by the normal teardown stays reported as the tripwire."""
        s = scheduler()
        s._request_shutdown("alg2_prefill_order_violation")
        s.shutdown()
        self.assertEqual(s._shutdown_reason, "alg2_prefill_order_violation")

    def test_receiver_error_is_an_unexpected_reason(self):
        s = scheduler()
        s._request_shutdown("receiver_thread_error", error="boom")
        self.assertNotEqual(s._shutdown_reason, GS.NORMAL_SHUTDOWN)


class TestFailClosedPropagation(unittest.TestCase):
    """The scheduling loop stopping must take the whole run down, not one GPU."""

    def _run(self, reason, raise_exc=None):
        killed = []
        real_gs, real_kill, real_cfg = G.GPUScheduler, G.kill_parent_process, G.configure_logger

        class FakeGS:
            NORMAL_SHUTDOWN = real_gs.NORMAL_SHUTDOWN

            def __init__(self, *a, **k):
                self._shutdown_reason = None

            def run_scheduling_loop(self):
                if raise_exc:
                    raise raise_exc
                self._shutdown_reason = reason

            def shutdown(self, reason=None):
                pass

            def _request_shutdown(self, reason, **d):
                if self._shutdown_reason is None:
                    self._shutdown_reason = reason

        G.GPUScheduler = FakeGS
        G.kill_parent_process = lambda: killed.append(True)
        G.configure_logger = lambda *a, **k: None
        try:
            pipe = types.SimpleNamespace(send=lambda x: None)
            G.run_gpu_scheduler_process(
                types.SimpleNamespace(), {}, {}, 0, [], pipe)
        finally:
            G.GPUScheduler, G.kill_parent_process, G.configure_logger = (
                real_gs, real_kill, real_cfg)
        return killed

    def test_alg2_tripwire_takes_the_run_down(self):
        """tripwire -> run-fatal, NOT log-and-continue on the other GPU."""
        self.assertEqual(
            self._run("alg2_backend_admit_order_violation"), [True],
            "an Alg2 order violation must kill the run, not just this scheduler")

    def test_receiver_error_takes_the_run_down(self):
        self.assertEqual(self._run("receiver_thread_error"), [True])

    def test_loop_returning_with_no_reason_takes_the_run_down(self):
        """Fail closed on the unexplained case too -- this is the observed incident."""
        self.assertEqual(self._run(None), [True])

    def test_process_exception_takes_the_run_down(self):
        self.assertEqual(self._run(None, raise_exc=RuntimeError("x")), [True])

    def test_normal_shutdown_does_not_kill_the_run(self):
        """Regression: an ordinary end-of-run teardown must stay clean."""
        self.assertEqual(self._run(GS.NORMAL_SHUTDOWN), [],
                         "normal shutdown must not be escalated")


class TestStashFallbackKeepsSourceServing(unittest.TestCase):
    """The rollback path itself -- confirming it was never the bug."""

    def test_source_stays_activated_and_runner_is_not_released(self):
        import sglang.srt.managers.scheduler as SCH
        src = open(SCH.__file__).read()
        i = src.index("stash_ok = self._v6_stash_captured()")
        j = src.index("self.tp_worker.deactivate_model_runner()", i)
        branch = src[i:j]
        # the failure branch must re-activate, report failure, and return
        self.assertIn("self._activated = True", branch)
        self.assertIn("success=False", branch)
        self.assertIn("return", branch)
        # and it must return BEFORE the irreversible release
        ret = branch.index("            return")
        self.assertLess(ret, len(branch),
                        "the failure branch must return before releasing the runner")


if __name__ == "__main__":
    unittest.main(verbosity=2)
