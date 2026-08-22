# D2 migration-only gate, run 1 (after the residency fix)

## Verdict

```text
RESIDENCY DEFECT      : FIXED AND VERIFIED
D2 CORRECTNESS GATE   : FAIL -- new, different blocker
```

The defect this rerun existed to test is gone. The run then died of a second,
unrelated memory blocker, so the gate as a whole does not pass and no wider
experiment may follow (`STOP B`).

## Setting

Identical to the diagnostic D2, only the output directory differs.

```text
Algorithm 2 = OFF (PRISM_DIAG_ALG2=0)
migration = ON, overlap migration = ON, KV migration = ON
tau = 0.07 (diagnostic, not a final threshold)
bursty rate 20, seed 1, 6 models, A100 80GB x2
```

The workload was reused, not regenerated. All three input hashes match the
preregistered values in `../baseline-readiness/META.txt`:

```text
bursty_r20_s1.pkl         666c453234519310d868fde79e02616522aa26077a2f48889790bc317174f27a
paired_requests_r20_s1    31d80a6f1157d3a1441fb2c1ab62c38e56672ccec1a5de22737f0126e562ad96
phases_r20_s1.json        a40d42de086acf7698b171a9c607353667b3fdf677efe15beef9fe62cdc02640
```

## What the fix changed

Five migration decisions, five weight transfers, all reading the GPU that
actually held the model:

| # | model | from | to | weight src | path | GB/s |
|---:|---|---:|---:|---:|---|---:|
| 1 | model_6 | 1 | 0 | 1 | gpu-to-gpu-p2p | 12.70 |
| 2 | model_6 | 0 | 1 | 0 | gpu-to-gpu-p2p | 17.40 |
| 3 | model_1 | 0 | 1 | 0 | gpu-to-gpu-p2p | 14.99 |
| 4 | model_2 | 1 | 0 | 1 | gpu-to-gpu-p2p | 14.00 |
| 5 | model_6 | 1 | 0 | 1 | gpu-to-gpu-p2p | 9.78 |

Migration 2 is the reverse migration that failed before: it now reads GPU0 over
NVLink instead of cold-loading 6.79 GB from host memory. Zero migrations used
the host path, against one of four in the diagnostic run.

The release log now carries ownership, and shows the invariant holding:

```text
release model_6 gpu 1 engine=1_2 held=0 held_engine=0_3 dropped=False
release model_6 gpu 0 engine=0_3 held=1 held_engine=1_3 dropped=False
release model_6 gpu 1 engine=1_3 held=1 held_engine=1_3 dropped=True
release model_1 gpu 0 engine=0_0 held=1 held_engine=1_2 dropped=False
release model_2 gpu 1 engine=1_0 held=0 held_engine=0_3 dropped=False
release model_2 gpu 0 engine=0_3 held=0 held_engine=0_3 dropped=True
release model_6 gpu 1 engine=1_3 held=0 held_engine=0_0 dropped=False
release model_4 gpu 0 engine=0_1 held=0 held_engine=0_1 dropped=True
```

Five late source releases preserved a committed target record; three genuine
deactivations by the owning engine dropped it. No release deleted a record it
did not own.

`kvcached cuMemCreate` did not fail, and peak GPU0 memory was 77,106 MiB
against 81,152 MiB in the diagnostic run.

## Correctness checks

`exp/scripts/check_migration_correctness.py`, full output in
`d2_correctness.json`.

| check | result |
|---|---|
| migration source is the resident GPU | PASS (5/5) |
| migration weights move over P2P | PASS (5/5) |
| release is owner-aware | PASS (8 releases, all with ownership evidence) |
| no residency metadata loss | PASS |
| no CUDA OOM | PASS |
| no migration-induced abort | PASS |
| benchmark result written | PASS |
| shutdown state reported clean | PASS |
| no server crash | **FAIL** |
| watchdog COMPLETE | **FAIL** |

## A harness flaw found while reading this run

`pipeline.rc` for this stage reads `0` even though the stage failed. Two
fail-open paths in `exp/scripts/run_baseline_stage.sh` produced that: `tee`
masked the stage command's exit status, and the EXIT trap recorded `$?` of an
unrelated last command when the watchdog killed the session. Both are now
closed -- a killed stage records 143 -- and the script self-checks. For this
run the watchdog `monitor/FAIL` marker is the authoritative record:
`benchmark: inner server session exited without result`.

## The new blocker

At 08:47:24, `GPU=0 Worker 0 (model_6)` took a prefill batch at `token usage:
0.95` and flashinfer could not allocate its workspace:

```text
RuntimeError: Failed to allocate memory for batch_prefill_tmp_v
with size 448659456 and alignment 16 in AlignedAllocator
```

The worker died, and the launcher was killed 22 seconds later. The benchmark
stopped at 3,070 completed responses.

The cause is the definition of feasibility in the placement policy, not
residency. `kvpr_global_v4._find_optimal_migrations` refuses a target only when

```python
need = self.model_weights_info[name]["model_size"]
if gpu_available_memory.get(dst, 0.0) < need:   # weights only
```

`gpu_available_memory` is real free memory, refreshed every cycle, so the
reading was correct -- the *requirement* is not. At the migration-5 decision
(08:47:00) GPU0 had 18.64 GB free and model_6 needs 15.23 GB of weights, so the
test passed with about 3.4 GB left for that model's KV pool and every
attention workspace on the GPU. Fifteen seconds later GPU0 was down to 3.70 GB,
and the first 8,192-token prefill batch that needed a 448 MB scratch buffer
failed. The policy audit confirms it never saw a problem: `rejected_by_memory:
0`, `blocked: []`.

This is the second half of the fix that `MIGRATION_DIAGNOSIS.md` identified
("target preparation must also fail closed against actual target memory
headroom") and that Stage 1 deliberately left out. The D2 evidence now says it
is required.

## Migration timing (n=5 decisions; 4 complete timelines)

Seconds, from `migration_timeline.csv` / `migration_summary.json`.

| phase | mean | P50 | P95 | max |
|---|---:|---:|---:|---:|
| target prepare | 2.8524 | 2.5400 | 4.6992 | 5.1750 |
| target ready to quiesce | 0.0360 | 0.0154 | 0.0931 | 0.1085 |
| quiesce control | 0.0305 | 0.0095 | 0.0881 | 0.1034 |
| request drain | 0.3363 | 0.1011 | 1.0966 | 1.3341 |
| KV stash | 0.9467 | 1.2047 | 1.6589 | 1.6687 |
| weight transfer | 0.8174 | 0.8755 | 1.4862 | 1.5578 |
| KV transfer | 4.2636 | 4.1996 | 6.7664 | 7.0406 |
| target inject total | 6.9641 | 6.6022 | 11.5041 | 11.8837 |
| KV inject exclusive | 3.5532 | 4.7728 | 4.9585 | 4.9874 |
| routing switch | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| routing to first request | 0.0597 | 0.0560 | 0.0984 | 0.1008 |
| first request to first decode | 2.7952 | 3.2194 | 4.3412 | 4.4250 |
| exposed service downtime | 11.8043 | 11.2909 | 15.7384 | 16.2751 |
| total migration wall | 17.5744 | 16.4659 | 21.2622 | 21.9721 |

Bytes and effective bandwidth:

```text
weights : 52,244,840,448 B, 100% P2P, mean 13.77 GB/s
KV      :  1,860,280,320 B, 100% P2P, mean  0.14 GB/s
          115 requests, 40,515 tokens moved, 0 skipped over cap
```

## Reading of the timing

Routing is ~12 microseconds and is not the cost. Weight transfer is not the
cost either: 52.2 GB moved at 13.77 GB/s mean is NVLink working as intended.

The cost is the KV path. KV transfer and the exclusive target-side inject
together average 7.82 s of the 11.80 s exposed downtime -- 66% -- and the KV
transfer moves its bytes at **0.14 GB/s over the same P2P link on which the
weights move at 13.77 GB/s**, a factor of about 100. A payload of 1.86 GB
should not take 17 seconds of link time on hardware that moved 52.2 GB in 3.8
seconds. Under the criteria in the handoff (section 12) that is an
implementation-stall signature, not intrinsic work cost: the bytes and the
measured link bandwidth do not explain the duration.

This is recorded as a finding, not acted on: the run failed its correctness
gate, and a performance change on top of an unfinished gate is not evidence of
anything.

## Status

```text
STOP B -- new concrete integration blocker found in D2.
No D3, no tau calibration, no c_i reprofiling, no sweep.
```
