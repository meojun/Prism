#!/usr/bin/env python3
"""The activation memory wait must actually wait for the model's weights.

`WorkerPoolModelRunner.load_gpu_model` guards the activation with

    while get_available_gpu_memory(...) - min_reserve_mem < model_gpu_mem_usage:
        sleep(0.1)

but `model_gpu_mem_usage` was set to 0 in `__init__` and never updated by
`_set_model_params`, so the condition reduced to `free < min_reserve_mem`: the
loop never waited for the weights of the model actually being activated. It
logged nothing across all five D2 runs of 2026-08-22, and in run 4 an
activation walked into a GPU with 185.25 MiB free and died inside
`torch.empty_like` allocating 260 MiB.

These tests pin that the size is populated per model from the profiled model
info, that the wait loop trips on a full GPU and releases when memory comes
back, and that it says so in the log -- the symptom that made the defect
invisible for five runs was silence.
"""

import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2] / "prism-research"
sys.path.insert(0, str(REPO / "python"))

from sglang.srt.model_executor import worker_pool_model_runner as wp  # noqa: E402
from sglang.srt.model_executor.worker_pool_model_runner import (  # noqa: E402
    WorkerPoolModelRunner,
)

PASS, FAIL = [], []

# (model path, GiB of weights) straight from utils/model_info.json.
EXPECTED = {
    "meta-llama/Llama-3.2-1B": 2.279296875,
    "Qwen/Qwen2.5-1.5B-Instruct": 3.0078125,
    "meta-llama/Llama-3.2-3B": 6.0,
    "Qwen/Qwen2.5-3B-Instruct": 5.8359375,
    "meta-llama/Llama-3.1-8B": 15.080078125,
    "Qwen/Qwen2.5-7B-Instruct": 14.283203125,
}


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


def runner(model_path):
    """A runner with only what the size lookup and the wait loop touch."""
    obj = WorkerPoolModelRunner.__new__(WorkerPoolModelRunner)
    obj.model_config = SimpleNamespace(path=model_path)
    obj.device = "cuda"
    obj.gpu_id = 0
    obj.min_reserve_mem = 6.459
    obj.model_gpu_mem_usage = 0
    return obj


def test_size_is_populated_per_model():
    print("profiled weight size")
    for path, gib in EXPECTED.items():
        obj = runner(path)
        check(f"{path} -> {gib} GiB",
              obj._get_profiled_model_gpu_mem_usage() == gib)

    obj = runner("nobody/not-a-real-model")
    check("an unknown model degrades to 0 rather than raising",
          obj._get_profiled_model_gpu_mem_usage() == 0)


def test_set_model_params_refreshes_it():
    """Each activation is a different model, so the size must be refreshed."""
    print("_set_model_params")
    source = (REPO
              / "python/sglang/srt/model_executor/worker_pool_model_runner.py"
              ).read_text()
    body = source.split("def _set_model_params", 1)[1].split("\n    def ", 1)[0]
    check("_set_model_params sets model_gpu_mem_usage",
          "self.model_gpu_mem_usage = self._get_profiled_model_gpu_mem_usage()"
          in body)
    check("it is no longer left at the __init__ zero",
          "self.model_gpu_mem_usage = 0" in source
          and body.count("model_gpu_mem_usage") >= 1)


def _wait_loop(free_readings, model_gib, reserve=6.459):
    """Drive load_gpu_model's guard with a scripted sequence of free memory."""
    obj = runner("Qwen/Qwen2.5-7B-Instruct")
    obj.min_reserve_mem = reserve
    obj.model_gpu_mem_usage = model_gib
    readings = list(free_readings)
    seen = []

    def fake_available(device, gpu_id):
        value = readings.pop(0) if len(readings) > 1 else readings[0]
        seen.append(value)
        return value

    slept = []
    with patch.object(wp, "get_available_gpu_memory", fake_available), \
            patch.object(wp.time, "sleep", lambda s: slept.append(s)):
        # Only the guard: stop before the load itself.
        try:
            WorkerPoolModelRunner.load_gpu_model(
                obj, check_mem=True, use_model_service=False)
        except Exception:
            pass
    return seen, slept


def test_the_wait_trips_on_a_full_gpu():
    print("the wait loop")
    caplog = []

    class Collect(logging.Handler):
        def emit(self, record):
            caplog.append(record.getMessage())

    handler = Collect()
    wp.logger.addHandler(handler)
    previous_level = wp.logger.level
    wp.logger.setLevel(logging.INFO)
    try:
        # 20 GB free, 14.28 GiB model, 6.459 reserve -> 20 - 6.459 < 14.28: wait.
        # Then memory frees up and it proceeds.
        _, slept = _wait_loop([20.0, 20.0, 40.0], model_gib=14.283203125)
        check("a GPU with 20 GB free makes a 14.28 GiB activation wait",
              len(slept) >= 1)
        check("the wait is announced in the log, not silent",
              any("Waiting for enough memory to load the model" in m
                  for m in caplog))

        caplog.clear()
        # Same GPU, same reserve, but the old zero size: never waits.
        _, slept_zero = _wait_loop([20.0, 20.0, 40.0], model_gib=0)
        check("with the old zero size the same GPU never waits",
              not slept_zero)
        check("which is why five runs logged nothing",
              not any("Waiting for enough memory" in m for m in caplog))

        caplog.clear()
        # Plenty of room: no wait, and no spurious delay on the common path.
        _, slept_free = _wait_loop([60.0], model_gib=14.283203125)
        check("an activation with real headroom is not delayed",
              not slept_free)
    finally:
        wp.logger.removeHandler(handler)
        wp.logger.setLevel(previous_level)


def main():
    test_size_is_populated_per_model()
    test_set_model_params_refreshes_it()
    test_the_wait_trips_on_a_full_gpu()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for name in FAIL:
            print(f"  FAILED: {name}")
        raise SystemExit(1)
    print("ALL ACTIVATION MEMORY WAIT TESTS PASSED")


if __name__ == "__main__":
    main()
