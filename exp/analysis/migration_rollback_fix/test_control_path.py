#!/usr/bin/env python3
"""Targeted tests for the migration control-path lifecycle guards.

No GPU, no server. The class is constructed without __init__ and given only the
attributes the code under test touches, so these exercise the real methods.
"""
import asyncio, os, sys, tempfile, types, unittest
sys.path.insert(0, "/workspace/prism-exp/prism-research/python")
import sglang.multi_model.request_handler_worker_pool as M

RH = getattr(M, "RequestHandlerWorkerPool", None)


class FakeSocket:
    def __init__(self):
        self.sent = []

    def send_pyobj(self, obj):
        self.sent.append(obj)


def handler(ipc_map, sockets):
    h = object.__new__(RH)
    h.gpu_scheduler_ipc_name = ipc_map
    h.send_to_gpu_scheduler_dict = sockets
    h.rid_to_state = {}
    return h


class DeactivateReqInput:
    """Stands in for the real request object; only these fields are read."""

    def __init__(self, gpu_id, rid="r1", model_name="model_4"):
        self.gpu_id = gpu_id
        self.rid = rid
        self.model_name = model_name


def req(gpu_id, rid="r1"):
    return DeactivateReqInput(gpu_id, rid)


class TestLiveness(unittest.TestCase):
    def test_alive_when_endpoint_exists(self):
        with tempfile.NamedTemporaryFile() as f:
            h = handler({0: f.name}, {0: FakeSocket()})
            self.assertTrue(h._gpu_scheduler_alive(0))

    def test_not_alive_when_endpoint_removed(self):
        f = tempfile.NamedTemporaryFile(delete=False)
        name = f.name; f.close(); os.unlink(name)
        h = handler({0: name}, {0: FakeSocket()})
        self.assertFalse(h._gpu_scheduler_alive(0))

    def test_fails_open_for_unknown_gpu(self):
        """Never refuse a request we cannot be certain about."""
        h = handler({}, {0: FakeSocket()})
        self.assertTrue(h._gpu_scheduler_alive(7))
        h2 = object.__new__(RH)          # registry absent entirely
        self.assertTrue(h2._gpu_scheduler_alive(0))


class TestBoundedControlPath(unittest.IsolatedAsyncioTestCase):
    async def test_refuses_to_send_when_target_is_gone(self):
        """The exact failure mode: deactivate to a torn-down scheduler."""
        f = tempfile.NamedTemporaryFile(delete=False)
        name = f.name; f.close(); os.unlink(name)
        sock = FakeSocket()
        h = handler({0: name}, {0: sock})
        ok, out = await h._send_req_and_wait_for_response(req(0))
        self.assertFalse(ok)
        self.assertIsNone(out)
        self.assertEqual(sock.sent, [], "must not send into a dead endpoint")
        self.assertEqual(h.rid_to_state, {}, "must not leak a waiter")

    async def test_times_out_instead_of_blocking_forever(self):
        with tempfile.NamedTemporaryFile() as f:
            sock = FakeSocket()
            h = handler({0: f.name}, {0: sock})
            M.CONTROL_REQUEST_TIMEOUT_S = 0.2      # no reply will ever arrive
            t0 = asyncio.get_running_loop().time()
            ok, out = await h._send_req_and_wait_for_response(req(0))
            elapsed = asyncio.get_running_loop().time() - t0
            self.assertFalse(ok)
            self.assertIsNone(out)
            self.assertLess(elapsed, 5.0, "must not block on an unanswered request")
            self.assertGreaterEqual(elapsed, 0.2)
            self.assertEqual(len(sock.sent), 1, "the request was genuinely sent")
            self.assertEqual(h.rid_to_state, {}, "waiter cleaned up after timeout")

    async def test_normal_response_still_succeeds(self):
        """Regression: the guards must not break the healthy path."""
        with tempfile.NamedTemporaryFile() as f:
            sock = FakeSocket()
            h = handler({0: f.name}, {0: sock})
            M.CONTROL_REQUEST_TIMEOUT_S = 5.0

            async def answer():
                await asyncio.sleep(0.05)
                st = h.rid_to_state["r1"]
                st.out_list.append({"ok": True})
                st.finished = True
                st.event.set()

            asyncio.create_task(answer())
            ok, out = await h._send_req_and_wait_for_response(req(0))
            self.assertTrue(ok)
            self.assertEqual(out, {"ok": True})
            self.assertEqual(len(sock.sent), 1)
            self.assertEqual(h.rid_to_state, {})


if __name__ == "__main__":
    if RH is None:
        print("FATAL: handler class not found", file=sys.stderr); sys.exit(2)
    unittest.main(verbosity=2)
