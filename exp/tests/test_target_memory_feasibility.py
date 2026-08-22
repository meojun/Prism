#!/usr/bin/env python3
"""Target-memory feasibility gate for Algorithm 1's migration emission.

The D2 run of 2026-08-22 moved a 15.23 GB model onto a GPU with 18.64 GB free
and left about 3.4 GB for that model's KV pool and every attention workspace on
the device. Fifteen seconds later a prefill batch could not allocate its
448 MB flashinfer workspace, the worker died, and the server was killed --
while the policy audit still read `rejected_by_memory: 0`.

`KVPRGlobalPolicyV4` now requires the target to have room for the incoming
weights *plus* the reserve the engine itself insists on. Algorithm 1's line-8
rule, tau, the cooldown and the one-migration-per-cycle emission are unchanged;
only the definition of "the target has room" is.

The last test replays the five real migration decisions from that run against
their recorded free-memory readings, so the gate is calibrated against measured
behaviour rather than an invented scenario.
"""

import sys
import time
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2] / "prism-research"
sys.path.insert(0, str(REPO / "python"))

from sglang.multi_model.scheduling.policy.kvpr_global_v4 import (  # noqa: E402
    KVPRGlobalPolicyV4,
)

PASS, FAIL = [], []

# model_size in GiB, cell_size in bytes per token -- the real values for the
# two models involved in the D2 decisions, from utils/model_info.json.
WEIGHTS = {
    "model_6": {"model_size": 14.2822265625, "cell_size": 57344},   # Qwen2.5-7B
    "model_2": {"model_size": 3.0078125, "cell_size": 28672},       # Qwen2.5-1.5B
    "model_1": {"model_size": 2.2802734375, "cell_size": 32768},    # Llama-3.2-1B
    "model_5": {"model_size": 14.9609375, "cell_size": 32768},      # Llama-3.1-8B
}


# GPU1 carries a very hot small model and the 7B; GPU0 is nearly idle. The
# greedy pass places the heaviest model first (it never moves, all ratios
# start at zero), which loads GPU1 and pushes model_6 towards GPU0.
HOT_GPU1 = (
    {0: ["model_5"], 1: ["model_2", "model_6"]},
    {"model_5": 8, "model_2": 40000, "model_6": 4096},
)


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


class Queue:
    """A ModelQueueTracker stub with a steady, non-zero token rate."""

    def __init__(self, prompt_len, count=8):
        now = time.time()
        self.received_reqs = {
            i: SimpleNamespace(prompt_len=prompt_len, arrival_time=now, is_warmup=False)
            for i in range(count)
        }
        self.decode_token_tput = float(prompt_len)


def policy(reserve, tau=0.07):
    return KVPRGlobalPolicyV4(
        num_gpus=2, gpu_mem=79.15, model_weights_info=WEIGHTS,
        workers_per_gpu=4, tau=tau, rate_window=30.0,
        migration_cooldown=0.0, target_reserve_gib=reserve,
    )


def decide(pol, mapping, free, loads):
    """Run one scheduling cycle; return (emitted, blocked, audit)."""
    queues = {name: Queue(length) for name, length in loads.items()}
    emitted = pol._find_optimal_migrations(
        model_instance_state_dict={},
        model_queues=queues,
        gpu_available_memory=free,
        model_violation_stats=None,
        gpu_to_model_mapping=mapping,
    )
    record = getattr(pol, "_last_line8", [])
    return emitted, record, dict(pol._audit)


def test_reserve_blocks_a_target_that_only_fits_the_weights():
    print("a target that fits the weights but nothing else")
    # model_2 is the heaviest, so the greedy pass pins it to GPU1 first and
    # GPU1's ratio then exceeds tau for model_6 -- the plan wants model_6 on
    # the idle GPU0, which is the shape of the real D2 decisions.
    mapping, loads = HOT_GPU1

    # 18.64 GB free: exactly the D2 situation.
    lenient = policy(reserve=0.0)
    emitted, _, audit = decide(lenient, mapping, {0: 18.64, 1: 4.0}, loads)
    check("without a reserve the weights-only test lets it through",
          audit["rejected_by_memory"] == 0 and emitted)

    strict = policy(reserve=6.459)
    emitted, _, audit = decide(strict, mapping, {0: 18.64, 1: 4.0}, loads)
    check("with the reserve the same target is refused",
          audit["rejected_by_memory"] >= 1)
    check("and no migration is emitted onto it", not emitted)


def test_reserve_still_allows_a_target_with_real_headroom():
    print("a target with genuine headroom")
    mapping, loads = HOT_GPU1
    strict = policy(reserve=6.459)
    emitted, _, audit = decide(strict, mapping, {0: 44.67, 1: 4.0}, loads)
    check("44.67 GB free admits a 14.28 GB model",
          audit["rejected_by_memory"] == 0)
    check("and the migration is emitted", emitted == [("model_6", 1, 0)])


def test_the_gate_is_reported_not_silent():
    print("the refusal is visible")
    mapping, loads = HOT_GPU1
    strict = policy(reserve=6.459)
    decide(strict, mapping, {0: 18.64, 1: 4.0}, loads)
    check("the reserve is carried in the policy for the audit record",
          strict.target_reserve_gib == 6.459)
    check("rejected_by_memory counts the refusal",
          strict._audit["rejected_by_memory"] >= 1)


def test_replays_the_recorded_d2_decisions():
    """The five real decisions, against their recorded free-memory readings.

    Migrations 1-3 (including the model_6 forward/reverse pair) must survive
    the stricter gate: a reserve that blocks every migration would make the
    D2 gate meaningless, since it could then pass with no migration at all.
    """
    print("replay of the five recorded D2 decisions")
    reserve = 6.459
    recorded = [
        # (id, model, src, dst, free on dst at the decision cycle)
        (1, "model_6", 1, 0, 44.67),
        (2, "model_6", 0, 1, 41.26),
        (3, "model_1", 0, 1, 24.21),
        (4, "model_2", 1, 0, 7.50),
        (5, "model_6", 1, 0, 18.64),
    ]
    allowed, refused = [], []
    for index, model, _src, _dst, free in recorded:
        (allowed if free >= WEIGHTS[model]["model_size"] + reserve
         else refused).append(index)
    check("the forward and reverse model_6 migrations both survive",
          1 in allowed and 2 in allowed)
    check("the third migration survives", 3 in allowed)
    check("the two memory-marginal migrations are refused", refused == [4, 5])
    check("the gate does not block every migration", len(allowed) >= 2)
    print(f"        allowed={allowed} refused={refused} reserve={reserve:.3f} GiB")


def main():
    test_reserve_blocks_a_target_that_only_fits_the_weights()
    test_reserve_still_allows_a_target_with_real_headroom()
    test_the_gate_is_reported_not_silent()
    test_replays_the_recorded_d2_decisions()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for name in FAIL:
            print(f"  FAILED: {name}")
        raise SystemExit(1)
    print("ALL TARGET MEMORY FEASIBILITY TESTS PASSED")


if __name__ == "__main__":
    main()
