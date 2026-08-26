# Source manifest

Exactly which code reproduces the final baseline.

## Repository

| | |
|---|---|
| remote | `https://github.com/meojun/Prism.git` |
| safe research branch | `research/prism-final-baseline` |
| clean base commit | `3ef6adb1c7c138f6537d882c73d48a56df026b27` (tag `final-baseline-handoff`) |
| **RUN_CODE_COMMIT** | `d122e01a911d58ba82e2aa83e12487b3fed251c9` |

`RUN_CODE_COMMIT` is the commit that identifies the **serving runtime** used for
every authoritative result. Later documentation and report commits move `HEAD`;
they do not change the runtime. Every run records both.

**Never push `exp/4het-paired-evaluation`.** Its history reaches local commit
`51c5405`, which contains the ~0.6 GB ShareGPT dump.

## Serving runtime

The runtime lives in `prism-research/`, a **separate checkout that this
repository gitignores**. Its identity is a base commit plus the complete
working-tree delta:

| | |
|---|---|
| base commit | `595ec1f170e75a43897a7a2ad58ac5a9820aa2e8` |
| worktree patch sha256 | `49f47ebd7c75aecdac2d67efea1b3d121b9e599693df514cfd71bfe10724881d` |
| **RUNTIME_SOURCE_TREE_HASH** | `7fbd431c6a636df0c72bb6a40324f851d002d204` |

`RUNTIME_SOURCE_TREE_HASH` is a real `git write-tree` hash of the live runtime,
computed through a throwaway index so the checkout's own index is untouched.

### Rebuild the exact runtime

```bash
cd /workspace/prism-exp
git -C prism-research reset --hard 595ec1f170e75a43897a7a2ad58ac5a9820aa2e8
git -C prism-research clean -fdq
git -C prism-research apply patches/lifecycle_containment/prism_research_worktree.patch
# prove it
PRISM_REPO=$PWD/prism-research bash exp/scripts/snapshot_source_patch.sh /tmp/verify verify
sha256sum /tmp/verify/prism_research_worktree.patch    # must equal the value above
```

## Patch layers

Applied in `patches/`, oldest first: `baseline_readiness`, `final_baseline_ready`, `lifecycle_containment`, `paper_faithful`, `paper_faithful_tp`, `paper_faithful_v3`, `paper_faithful_v4`, `paper_faithful_v5_2`, `paper_faithful_v6`.

The final layer, `patches/lifecycle_containment/`, is the frozen runtime used for
every authoritative result. It contains, relative to the previous freeze:

| change | file | purpose |
|---|---|---|
| token-rate estimator correction | `multi_model/scheduling/model_queue_tracker.py`, `controller_global.py`, `policy/kvpr_global.py`, `srt/managers/scheduler.py` | paper §4 + A.4: input and decode tokens over **one** sliding window |
| structured shutdown reasons | `scheduling/gpu/gpu_scheduler.py` | name every `_shutdown_event` setter |
| fail-closed propagation | `scheduling/gpu/gpu_scheduler.py` | an unexpected scheduler exit ends the run, not one GPU |
| bounded control path | `multi_model/request_handler_worker_pool.py` | `asyncio.wait_for`, default 120 s |
| scheduler liveness precondition | `multi_model/request_handler_worker_pool.py` | refuse control requests to a departed endpoint |

**Paper mechanisms vs implementation-added infrastructure** — the estimator
correction restores paper-specified semantics; the other four are
implementation-added correctness infrastructure with no counterpart in the paper.
Algorithm 1, Algorithm 2, τ, window, cooldown, placement and migration semantics
are unchanged by this layer.

## Orchestration and gates

| script | role |
|---|---|
| `exp/scripts/prism_final_orchestrator.sh` | chains τ → pilot → many-model → final 4-HET |
| `exp/scripts/prism_final_stage_runner.sh` | one arm × one condition list, idempotent, gates each run |
| `exp/scripts/run_v4_case.sh` | launches one server + benchmark |
| `exp/scripts/final_stage.sh` | per-run supervision, verification, Algorithm-2 gate |
| `exp/scripts/lifecycle_validity_gate.py` | offline lifecycle integrity gate |
| `exp/scripts/check_alg2_interaction.py` | Algorithm-2 interaction gate (arm-aware) |
| `exp/scripts/final_run_verify.py` | run verification and numbers extraction |
| `exp/scripts/build_paired_workload.py` | canonical trace generator |
| `exp/scripts/find_free_port.py` | per-run port block selection |

### Known runtime defect, found and deliberately not fixed

`PortArgs` in `srt/server_args.py` performs a check-then-use on `nccl_port`. All
engines scan upward from the same `start_port + 1`, so two can bind the same
port and one dies with `EADDRINUSE` before the server starts. Observed twice in
~110 runs. Fixing it would have changed runtime semantics after the baseline
freeze, which is a hard stop condition, so it is documented instead:
`exp/results/many-model-final/KNOWN_RUNTIME_RACE_nccl_port.md`.
