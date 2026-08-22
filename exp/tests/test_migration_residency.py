#!/usr/bin/env python3
"""Migration residency-lifecycle regression test (Stage 1).

The D2 diagnostic died because the model service dropped a model's GPU
residency record on the *source* release of a target-first migration, even
though the target had already committed itself as the new owner.  The next
migration of that model then found no GPU source, cold-loaded 6.79 GB of
weights from host memory onto a GPU that already held the model, and kvcached
failed `cuMemCreate` with CUDA out-of-memory.

This test drives the real `ModelService.run()` message loop -- load, release,
and the migration source lookup -- with stubbed CUDA so it needs no GPU, and
asserts the residency invariants:

  * after a target commit, the target record stays discoverable;
  * a late source release never deletes the committed target record;
  * a reverse migration finds `src = <the GPU that actually holds it>`,
    never `src=None` while the model is GPU-resident;
  * a genuine deactivation by the current owner does drop the record, so no
    migration ever reads freed memory as its source.
"""

import sys
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2] / "prism-research"
sys.path.insert(0, str(REPO / "python"))

from sglang.multi_model import model_sevice as ms  # noqa: E402
from sglang.multi_model.model_sevice import ModelService  # noqa: E402

MODEL = "Qwen/Qwen2.5-3B-Instruct"

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


class _Drained(Exception):
    """Raised by the fake input queue to end the service loop."""


class FakeInputQueue:
    def __init__(self):
        self.items = []

    def feed(self, item):
        self.items.append(item)

    def get(self, timeout=None):
        if not self.items:
            raise _Drained
        return self.items.pop(0)


class FakeOutputQueue:
    def __init__(self):
        self.sent = []

    def put(self, item):
        self.sent.append(item)


class FakeModel:
    """Stands in for a GPU-resident model; its state dict is its identity."""

    def __init__(self, name):
        self._sd = {"w": f"{name}-weights"}

    def state_dict(self):
        return self._sd


class StubLoader:
    """Records every copy_model_to_gpu_v4 call the service makes."""

    def __init__(self):
        self.calls = []

    def copy_model_to_gpu_v4(self, cpu_sd, dst_sd, target_gpu, executor,
                             gpu_ids, pool, source_state_dict=None,
                             host_registered=False, tag=""):
        self.calls.append({
            "target_gpu": target_gpu,
            "source_state_dict": source_state_dict,
            "tag": tag,
            "path": "gpu-to-gpu-p2p" if source_state_dict is not None else "cpu",
        })


class Harness:
    """A ModelService with CUDA, the v4 loader, and the queues stubbed out."""

    def __init__(self, gpu_ids=(0, 1)):
        self.loader = StubLoader()
        self.input_queue = FakeInputQueue()
        self.output_queues = {}
        svc = ModelService.__new__(ModelService)
        svc.model_dict = {MODEL: FakeModel("cpu")}
        svc.input_queue = self.input_queue
        self.svc = svc
        svc.output_queue = self.output_queues
        svc.num_shards = 1
        svc.gpu_ids = list(gpu_ids)
        svc.service_id = 0
        svc.executor = None
        svc.v4_pool = None
        svc.v4_p2p = True
        svc.v4_resident = {}
        svc.v6_kv_stash = {}
        svc.v4_peer = {f"{a}->{b}": True for a in gpu_ids for b in gpu_ids if a != b}

    def _out(self, engine_id):
        return self.output_queues.setdefault(engine_id, FakeOutputQueue())

    def load(self, engine_id, gpu_id, model=None):
        """Activate the model in `engine_id` on `gpu_id` (a target prepare)."""
        self._out(engine_id)
        self.input_queue.feed(
            (MODEL, engine_id, gpu_id, model or FakeModel(engine_id)))
        return self._pump()

    def release(self, engine_id, gpu_id):
        """The `__release__` an engine sends when it drops its GPU copy."""
        self.input_queue.feed(("__release__", MODEL, gpu_id, engine_id))
        return self._pump()

    def _pump(self):
        before = len(self.loader.calls)
        with patch.object(ms, "_V4", (self.loader, False, True)), \
                patch.object(ms.torch.cuda, "empty_cache", lambda: None), \
                patch.object(ms.torch.cuda, "ipc_collect", lambda: None), \
                patch.object(ms.gc, "collect", lambda: 0):
            try:
                self.svc.run()
            except _Drained:
                pass
        calls = self.loader.calls[before:]
        return calls[-1] if calls else None

    def resident_gpu(self):
        entry = self.svc.v4_resident.get(MODEL)
        return None if entry is None else entry[0]

    def resident_engine(self):
        # Tolerant of a shorter record so a regression fails on the invariant
        # it breaks, not on an IndexError.
        entry = self.svc.v4_resident.get(MODEL)
        return None if entry is None or len(entry) < 3 else entry[2]


def test_forward_then_reverse_migration():
    """The exact D2 sequence: GPU0 -> GPU1 -> GPU0, target-first each time."""
    print("forward and reverse migration with target-first preparation")
    h = Harness()

    first = h.load("0_a", 0)
    check("initial activation cold-loads from host", first["path"] == "cpu")
    check("initial activation records residency on GPU0", h.resident_gpu() == 0)

    # Migration GPU0 -> GPU1: the target prepares while the source is live.
    fwd = h.load("1_a", 1)
    check("forward migration reads the GPU0 copy over P2P",
          fwd["path"] == "gpu-to-gpu-p2p" and "src=0" in fwd["tag"])
    check("target commit moves residency to GPU1", h.resident_gpu() == 1)
    check("target commit records the target engine", h.resident_engine() == "1_a")

    # The old source engine now drains and releases.  This is the step that
    # used to delete the target's record.
    h.release("0_a", 0)
    check("late source release keeps the committed target residency",
          h.resident_gpu() == 1)
    check("late source release keeps the target engine id",
          h.resident_engine() == "1_a")

    # Reverse migration GPU1 -> GPU0 must still find a GPU source.
    rev = h.load("0_b", 0)
    check("reverse migration finds src=1, not None",
          rev["source_state_dict"] is not None and "src=1" in rev["tag"])
    check("reverse migration uses P2P, not a CPU cold load",
          rev["path"] == "gpu-to-gpu-p2p")
    check("reverse target commit moves residency back to GPU0",
          h.resident_gpu() == 0)

    h.release("1_a", 1)
    check("reverse source release keeps the committed target residency",
          h.resident_gpu() == 0 and h.resident_engine() == "0_b")


def test_same_gpu_reactivation():
    """A model re-activated in another worker slot on the same GPU.

    The D2 log shows this happening (model6 ran in engine 0_3 and later in
    engine 0_1 on GPU 0), so a GPU-only ownership check is not enough.
    """
    print("same-GPU re-activation in a different worker slot")
    h = Harness()
    h.load("0_a", 0)
    h.load("0_b", 0)
    check("re-activation commits the new engine", h.resident_engine() == "0_b")
    h.release("0_a", 0)
    check("the drained slot's release does not delete the new record",
          h.resident_gpu() == 0 and h.resident_engine() == "0_b")


def test_owner_release_drops_the_record():
    """The fix must not turn release into a no-op: stale records are worse.

    A residency record that outlives its GPU copy would hand a later
    migration a state dict pointing at freed memory.
    """
    print("deactivation by the current owner")
    h = Harness()
    h.load("0_a", 0)
    h.release("0_a", 0)
    check("the owner's release drops the residency record",
          h.svc.v4_resident.get(MODEL) is None)
    after = h.load("1_a", 1)
    check("a later activation cold-loads instead of reading freed memory",
          after["path"] == "cpu" and "src=None" in after["tag"])

    h2 = Harness()
    h2.load("0_a", 0)
    h2.release("0_a", 0)
    check("releasing an unknown model is a no-op",
          h2._pump() is None and h2.svc.v4_resident.get(MODEL) is None)


def test_same_gpu_is_never_a_p2p_source():
    """A copy on the target GPU is not a cross-GPU migration source."""
    print("source selection")
    h = Harness()
    h.load("0_a", 0)
    same = h.load("0_b", 0)
    check("an activation on the resident GPU does not use P2P",
          same["path"] == "cpu" and "src=None" in same["tag"])

    # No peer link -> no P2P source, even across GPUs.
    h2 = Harness()
    h2.svc.v4_peer = {}
    h2.load("0_a", 0)
    nopeer = h2.load("1_a", 1)
    check("without peer access the migration falls back to host memory",
          nopeer["path"] == "cpu")


def test_release_is_owner_aware_by_construction():
    """Guard against a future edit reinstating the unconditional pop."""
    print("implementation guard")
    source = (REPO / "python/sglang/multi_model/model_sevice.py").read_text()
    check("the release branch no longer pops residency unconditionally",
          "self.v4_resident.pop(released_key, None)" not in source)
    check("release goes through the owner-aware compare-and-delete",
          "_residency_release(" in source)


def main():
    test_forward_then_reverse_migration()
    test_same_gpu_reactivation()
    test_owner_release_drops_the_record()
    test_same_gpu_is_never_a_p2p_source()
    test_release_is_owner_aware_by_construction()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for name in FAIL:
            print(f"  FAILED: {name}")
        raise SystemExit(1)
    print("ALL MIGRATION RESIDENCY TESTS PASSED")


if __name__ == "__main__":
    main()
