# D2 migration-only correctness gate

## Verdict

```text
D2 CORRECTNESS GATE : PASS   (run5, and again on run6 after the accounting fix)
```

Five runs were needed before the first pass. Runs 1-4 are kept in full:
each failed for a different reason, and the sequence is what identified the
causes. No run was rerun after a SUCCESS.

## Setting

Identical across all five runs; only the output directory changed.

```text
Algorithm 2 = OFF (PRISM_DIAG_ALG2=0)
migration = ON, overlap migration = ON, KV migration = ON
tau = 0.07 (diagnostic, not a final threshold)
bursty rate 20, seed 1, 6 models, A100 80GB x2, 420 s
```

The workload was reused, never regenerated; all three input hashes match the
preregistered values in `../baseline-readiness/META.txt`.

## What each run established

| run | outcome | what it showed |
|---|---|---|
| 1 | server killed at 3,070 responses | residency fix verified: 5/5 migrations read the resident GPU over P2P, including the reverse migration that used to cold-load 6.79 GB. Died of a flashinfer workspace allocation. |
| 2 | server killed at 4,203 responses | with the weights+reserve feasibility gate active (`rejected_by_memory: 20`), the same allocation failed **on a GPU that was not migrating anything** -- ruling migration out as the cause. |
| 3 | server killed at 2,706 responses | same failure with the kvcached admission bound added. Still not it. |
| 4 | server killed at 2,909 responses | with `FLASHINFER_WORKSPACE_SIZE` restored the workspace failure vanished (0 occurrences against 1 fatal occurrence in each earlier run), and the run died instead of a genuine CUDA OOM during an activation. |
| 5 | **completed, rc 0, watchdog COMPLETE** | gate PASS. |

## The two causes, and how they were told apart

**The workspace.** Runs 1-3 all died of

```text
RuntimeError: Failed to allocate memory for batch_prefill_tmp_v
              with size 443-456 MB in AlignedAllocator
```

That is flashinfer's *fixed* prefill scratch buffer, not GPU memory: upstream
defaults to 384 MiB and model_6 (Qwen2.5-7B, GQA 28 query heads to 4 KV heads)
asks for 420-455 MiB at these rates. A worker treats the raise as fatal and
calls `kill_parent_process()`, taking the server with it. This was already
diagnosed in the v4 milestone -- `paper-faithful-v4/provenance/ENVIRONMENT.md`
and `HANDOVER.md` 4.4 record `FLASHINFER_WORKSPACE_SIZE=1073741824` "for all
runs and all arms", and the `int()` cast that makes the variable readable at all
is in this tree. The value lived only in `/workspace/.env`, which this
instance's rebuild recreated with `HF_TOKEN` alone. It now lives in
`exp/scripts/env.sh`, inside the repository.

**The elastic pools.** Run 4 then produced a real CUDA OOM, and it discriminates
between the two memory changes that had been made speculatively:

```text
GPU=1 Worker 3, activate -> create_empty_gpu_model_from_cpu_model
torch.OutOfMemoryError: Tried to allocate 260.00 MiB.
GPU 1 ... 185.25 MiB is free
```

At 09:44:57 the controller read 18.15 GB free on GPU1 and cleared the
activation. Three seconds later the KV pools -- `token usage 1.00` and `0.96`,
439 running requests -- had taken all of it. A weights-plus-reserve test at
decision time would have passed too (18.15 >= 3.01 + 6.46), so that change was
not the fix and stays reverted; Algorithm 1's emission behaviour is untouched.
What does fix it is refusing to let the pools grow into the reserve at all:
`MHATokenToKVPool.available_size()` returned the kvcached allocator's *virtual*
availability unchecked on the `use_kvcached_v0` path, while the non-v0 elastic
path already bounded admission by physical free memory. Both paths now take
that bound.

## Correctness checks (run5)

`exp/scripts/check_migration_correctness.py`, full output in
`d2_run5_correctness.json`. The same script still fails runs 1-4 and the
original diagnostic run.

| check | result |
|---|---|
| pipeline rc zero | PASS |
| watchdog COMPLETE | PASS |
| no CUDA OOM | PASS |
| no server crash | PASS |
| migration source is the resident GPU | PASS (13/13) |
| every decision produced a transfer | PASS |
| at least one GPU-to-GPU migration | PASS |
| reverse migration completed | PASS |
| KV transfer healthy | PASS (369 requests, 0 skipped over cap) |
| migrations not all blocked by memory | PASS (13 emitted) |
| migration weights move over P2P | PASS (13/13) |
| release is owner-aware | PASS |
| no residency metadata loss | PASS |
| benchmark result written | PASS |
| no migration-induced abort | PASS |
| shutdown state clean | PASS |

`Decode out of memory happened. #retracted_reqs: N` is recorded but does not
gate: it is upstream SGLang's designed decode-retraction backpressure -- the
scheduler sends a `BatchRetractDecodeReq` and lowers `new_token_ratio`, nothing
fails to allocate and no request is lost. Run 5 logged 32 such events. It is
reported because it measures how hard the pools were squeezed; see the cost
section below.

## Migration timing (13 decisions, 11 complete timelines)

Seconds, from `migration_timeline_run5.csv` / `migration_summary_run5.json`.

| phase | n | mean | P50 | P95 | max |
|---|---:|---:|---:|---:|---:|
| target prepare | 13 | 2.4612 | 1.9169 | 4.0490 | 5.2493 |
| target ready to quiesce | 13 | 0.0402 | 0.0181 | 0.1321 | 0.2455 |
| quiesce control | 13 | 0.0349 | 0.0142 | 0.1274 | 0.2410 |
| request drain | 13 | 0.5524 | 0.1437 | 1.3923 | 1.4528 |
| KV stash | 13 | 0.8681 | 1.0815 | 1.7454 | 1.8204 |
| weight transfer | 13 | 0.5635 | 0.3213 | 1.2119 | 1.2710 |
| KV transfer | 10 | 3.7866 | 4.5266 | 6.8152 | 7.4130 |
| target inject total | 13 | 5.2934 | 4.5189 | 12.9682 | 14.4284 |
| KV inject exclusive | 13 | 2.3806 | 1.7075 | 6.6952 | 7.0154 |
| routing switch | 13 | 0.0000 | 0.0000 | 0.0000 | 0.0001 |
| routing to first request | 11 | 3.1114 | 0.0517 | 15.6208 | 29.3671 |
| first request to first decode | 11 | 2.7791 | 2.7317 | 7.7661 | 10.4000 |
| exposed service downtime | 11 | 13.2310 | 12.4706 | 28.1373 | 36.4090 |
| total migration wall | 11 | 18.9385 | 18.4004 | 34.6807 | 42.6673 |

Bytes and effective bandwidth:

```text
weights : 97,720,946,688 B, 13/13 gpu-to-gpu-p2p, mean 13.05 GB/s
KV      :  4,522,926,080 B, 10/10 gpu-to-gpu-p2p, mean 0.31 GB/s (P50 0.13)
          369 requests, 130,681 tokens moved, 0 skipped over cap
```

## Reading of the timing

Routing is ~12 microseconds. Weight transfer is not the cost either: 97.7 GB at
13.05 GB/s mean is NVLink doing its job, and every one of the 13 transfers took
the P2P path.

The cost is concentrated in the KV path. KV transfer plus the exclusive
target-side inject average 6.17 s of the 13.23 s exposed downtime -- 47% -- and
the KV transfer moves its bytes at **0.31 GB/s mean (0.13 GB/s median) over the
same P2P link on which the weights move at 13.05 GB/s**, a factor of 40 to 100.
A 4.5 GB payload should not take 38 s of link time on hardware that moved
97.7 GB in 7.3 s. Under the handoff's section 12 criteria that is an
implementation-stall signature: the bytes and the measured link bandwidth do not
explain the duration.

`routing to first request` also deserves a look -- mean 3.11 s against a P50 of
0.05 s, with a 29.4 s maximum -- but it is a tail, not a systematic cost.

Neither is acted on here. The next step is to instrument the KV path and find
the concrete cause (P2P fast path not taken, host staging, serialization,
per-block synchronization, target rebuild) before changing anything.

## Cost of the admission bound, reported honestly

Run 5 completed 7,047 of 8,423 requests with 1,376 aborted (the benchmark aborts
a request when it exceeds its SLO), mean TTFT 33.5 s and P99 TTFT 170.5 s, and
32 decode-retraction events. The Released Prototype's own clean runs at this
rate recorded **zero** retractions.

Part of that is this arm (migration on, tau 0.07 diagnostic) and part of it is
the admission bound, which keeps `min_reserve_mem` = 6.459 GB per GPU out of the
KV pools -- about 13 GB of the 134 GB pool budget across two GPUs. The non-v0
elastic path keeps back 0.5 GB for the same purpose.

A related defect was found while reading this path and is fixed in run 6 below:
`WorkerPoolModelRunner.model_gpu_mem_usage` is set to `0` in `__init__` and
never updated by `_set_model_params`, so the activation wait loop in
`load_gpu_model` -- whose whole purpose is to wait until
`free - min_reserve_mem >= model_gpu_mem_usage` -- only ever waits for the
reserve and never for the model's own weights. It logged nothing in any of the
five runs. That is why run 4's activation walked into a GPU with 185 MiB free
instead of waiting.

Whether to fix it, and whether the pool reserve can then drop to the 0.5 GB the
sibling path uses, is a separate change that must be measured on its own.

## Run 6: the accounting fix, measured

`WorkerPoolModelRunner._set_model_params` now populates `model_gpu_mem_usage`,
so `load_gpu_model`'s wait loop finally waits for the weights of the model being
activated instead of collapsing to `free < min_reserve_mem`. With the reserve
enforced there, the kvcached-v0 admission bound went back to the 0.5 GB the
non-v0 elastic path has always kept back, returning roughly 13 GB of KV pool
across the two GPUs.

Run 6 is the same D2 -- same workload, seed, SLOs, GPUs, tau -- run once.

| | run 5 | run 6 |
|---|---:|---:|
| completed | 7,047 | **7,921** |
| aborted (SLO) | 1,376 | **502** |
| request throughput (req/s) | 13.63 | **16.91** |
| mean TTFT | 33,501 ms | **17,306 ms** |
| P99 TTFT | 170,486 ms | **110,332 ms** |
| mean TPOT | 219.5 ms | 226.3 ms |
| P99 TPOT | 1,607 ms | 1,769 ms |
| decode-retraction events | 32 (1,999 requests) | **0** |
| CUDA OOM | 0 | 0 |
| server crash | 0 | 0 |

Aborts fell by 64%, throughput rose 24%, mean TTFT halved, and retraction
disappeared entirely. TPOT is marginally worse. The wait loop is no longer
silent: 809 lines of `Waiting for enough memory to load the model...` with real
numbers (`available 19.18 GB, min reserve 6.46 GB, model memory usage 14.28`),
against zero lines in every earlier run.

The correctness gate passes again on run 6: 4 P2P migrations, all reading the
resident GPU, forward and reverse both present, residency clean, KV transfers
healthy, no OOM, no crash, clean shutdown.

### A finding in the controller, reported not fixed

Run 6 recorded five MIGRATE decisions but four weight transfers. The fifth is
not a failed migration:

```text
cycle 20  ACTION: deactivate model_4:1 on GPU 1. Reason: idle instance eviction
cycle 20  PLANNING: migrate model model_4 from GPU 1 to GPU 0
cycle 20  Executed 2 actions ... [DeactivateAction(model_1), DeactivateAction(model_4)]
```

Idle-instance eviction runs before Algorithm 1 in the same cycle, and the action
list built afterwards carried only the deactivation. The migration was
superseded, and model_4 was re-activated on GPU1 later.

The consequence worth carrying forward is an evidence one: the policy's
`migrations_emitted` audit counts *decisions*, not executed migrations, so
migration counts in any report must come from the weight-transfer records rather
than from that counter. The correctness script now separates the two --
`decisions_superseded_by_idle_eviction` is reported, while a decision that
disappears with no stated reason still fails the gate. No controller behaviour
was changed.

## Runs 7-9: the KV path, and what the accounting fix exposed

### The KV bottleneck, measured before anything was changed

`exp/scripts/microbench_kv_migration.py` imports the shipped `kv_migration_v6`
and `parallel_loading_v4` and only times them, on the same GPU pair, with the
real per-layer shapes. 161 requests, 5.96 GB, 9,016 copies of 646 KiB, NVLink
counters confirming P2P (tx 5,964,120,064 B, rx 0), zero host-staged copies:

| | target idle | target busy |
|---|---:|---:|
| KV wall | 2.33 s | 92.1 s |
| KV bandwidth | 2.56 GB/s | 0.065 GB/s |
| time inside 161 `synchronize` calls | 0.002 s | **59.0 s** |
| weight path, comparable bytes | 0.033 s | 1.08 s (one `synchronize`) |

Same bytes, same copies, same path. The entire 40x difference is time spent in
`torch.cuda.synchronize(target_gpu)` -- a device-wide barrier taken once per
request, which waits for every other model's prefill and decode queued on that
GPU: 366 ms each. It also explains why field wall times never tracked bytes
(run 5 moved 166 MB in 4.3 s and 5,967 MB in 5.6 s).

The batch now runs on one stream and is waited on once, which is what the weight
path already does and what `transfer_capsule`'s unused `stream` parameter was
for. Source tensors are held until that wait completes. Re-measured under load:
model_3 92.1 -> 10.9 s, model_4 69.7 -> 8.4 s, model_6 27.3 -> 3.0 s -- 7.3x to
9.3x. Idle is unchanged, as it should be. The remaining gap to the weight path
is the second cause, 9,016 copies of 646 KiB against large chunked ones, and is
left alone.

### What runs 7 and 8 exposed

With the activation guard live, an activation whose target lacks
weights-plus-reserve does not fail -- it blocks in `load_gpu_model`'s wait loop
*inside the scheduler's event loop*, so that model serves nothing while it
waits. Run 7: two migrations decided at 12:24:26 and 12:25:13 never ran, their
activations waiting from 12:25:15 to 12:32:51. Run 8: one worker waited 2,661
iterations (~266 s) for a 14.28 GiB model on a GPU whose KV pools were full; the
server stopped serving and 4,187 of 8,423 requests aborted.

That is the case the weights-plus-reserve feasibility gate refuses up front, so
it was reinstated on the policy's existing `rejected_by_memory` branch, with the
reserve being the engine's own `min_reserve_mem`. No timeout and no fallback
policy were introduced; the activation guard stays as a safety check.

### Run 9

| | run 5 | run 6 | run 7 | run 8 | run 9 |
|---|---:|---:|---:|---:|---:|
| completed | 7,047 | 7,921 | 7,442 | 4,236 | **8,423** |
| aborted (SLO) | 1,376 | 502 | 981 | 4,187 | **0** |
| throughput (req/s) | 13.63 | 16.91 | 11.71 | 7.56 | **18.44** |
| mean TTFT (ms) | 33,501 | 17,306 | 2,497 | 3,001 | 5,535 |
| P99 TTFT (ms) | 170,486 | 110,332 | 28,978 | 30,548 | 30,805 |
| mean TPOT (ms) | 219.5 | 226.3 | 227.8 | 235.4 | **219.2** |
| activation wait lines | 0 | 809 | 3,309 | 2,885 | **0** |
| decode retractions | 32 | 0 | 0 | 1 | **0** |
| exposed downtime (s) | 13.23 | 20.94 | 10.12 | -- | **9.42** |

Every request in the workload completed and none was aborted. `rejected_by_memory`
reached 25, so the gate is refusing targets rather than sitting idle, and 7 of 7
migrations moved their weights over P2P from the GPU that held them.

Mean TTFT is higher than run 7's, which is the expected shape: run 7 dropped 981
requests, and dropped requests do not contribute a TTFT. Run 9 served all 8,423
at the same P99.

### A gate refinement, with the evidence for it

Run 9 host-loaded three activations that followed a MIGRATE decision. Each was
preceded by an owner release with `dropped=True` -- the model had been fully
deactivated before the load, so no GPU copy remained and a host load is the only
thing the loader can do:

```text
load 13:01:56 Qwen2.5-7B    -> gpu1 | released gpu0 dropped=True 24.9s earlier
load 13:02:53 Llama-3.2-1B  -> gpu0 | released gpu1 dropped=True  5.5s earlier
load 13:04:26 Qwen2.5-3B    -> gpu1 | released gpu0 dropped=True 98.0s earlier
```

That is not the original defect. The original defect was a host load *while the
model was still GPU-resident*, because a completed migration had deleted its own
residency record. The check now separates the two using the release log, and
still fails the original diagnostic run -- which shows
`host_loads_after_deactivation: 0` alongside its wrong-source migration.

## Status

```text
D2 correctness: PASS (run5, run6, run9)
Memory accounting: fixed; infeasible placements refused at the decision
KV transfer: per-request device barrier removed, 7.3-9.3x under load
Remaining, reported not fixed: KV moves 9,016 copies of 646 KiB where the
  weight path moves large chunks; migrations_emitted counts decisions, not
  executed migrations
Next: D3 interaction gate.
```
