# D3 Algorithm 2 x migration interaction gate

## Verdict

```text
D3 INTERACTION GATE : PASS   (run 8)
```

Eight runs. The first seven each failed, and each one named a different place
where a request could reach prefill, or leave a GPU, without Algorithm 2's
runtime ledger knowing. The eighth completed.

## Setting

```text
arm            paper-faithful-v6  (KVPR global + Moore-Hodgson + overlap
                                   migration + KV migration)
Algorithm 2    ON
migration      ON
tau            0.07
workload       bursty rate 20, seed 1  (same trace, hashes unchanged)
run            one
```

## Result

```text
completed          8,422 of 8,423
aborted                1
throughput         18.49 req/s
mean TTFT       7,393.6 ms      P99 TTFT   84,953.5 ms
mean TPOT         143.7 ms      P99 TPOT      877.3 ms
migrations            10        weight transfers 22, all P2P
KV transfers           7, all P2P
rc 0, watchdog COMPLETE
```

| check | result |
|---|---|
| Algorithm 2 ran | PASS |
| no Algorithm 2 order violation | PASS |
| runtime raised no ordering error | PASS |
| sequence tokens monotonic | PASS |
| sequence tokens have no unexplained gaps | PASS |
| admission ordered across migrations | PASS |
| outstanding work retired | PASS |
| pipeline rc zero | PASS |
| no deadlock | PASS |
| no request loss | PASS |
| no migration-induced abort | PASS |
| no fatal CUDA or NCCL | PASS |

## What each run found

| run | completed | what it named |
|---|---:|---|
| 1 | 882 | a request resumed from migrated KV re-entered prefill with no entry in the target's ledger; the completion gate failed closed |
| 2 | 2,895 | a request dispatched into a model's Redis queue and not yet fetched was retired by no eviction path |
| 3 | 2,019 | decode retraction returns a request to the waiting queue, and it re-enters prefill unregistered |
| 4 | 1,882 | holding several requests re-asked for the earlier ones, so one was queued twice and took two sequences |
| 5 | 6,164 | the inactive-model activation branch had no feasibility test, so an activation blocked its engine's event loop |
| 6 | 1,624 | a worker slot is reused, so an adoption grant could be admitted under the wrong model |
| 7 | 8,253 | an admitted request could not be scheduled because every pool on the GPU reported full for a device-wide reason |
| 8 | **8,422** | **PASS** |

The handoff invariant those fixes were written against is in
`ALG2_MIGRATION_HANDOFF_INVARIANT.md`; the ownership question run 7 raised is
answered in `KV_OWNERSHIP_DIAGNOSIS.md`.

## The last change, and its evidence

Run 8 differs from run 7 by one thing: `available_size()` on the elastic v0
path is pool-local again.

That clamp was added (ad8a76c, reinstated 824caa7) to stop the KV pools taking
memory an incoming activation needed -- D2 run 4's
`create_empty_gpu_model_from_cpu_model` died with 185.25 MiB free three seconds
after the planner had cleared it. Checking whether it was still needed turned up
that **when it was chosen, the engine's own guard at that allocation was dead
logic**: `model_gpu_mem_usage` was 0 until 70ad7b8, which lands after 824caa7,
so the wait in `load_gpu_model` collapsed to `free < min_reserve_mem` and the
allocation that OOMed had nothing in front of it.

It does now. The wait requires weights plus `min_reserve_mem` and sits
immediately before that same call, and since 56636e1 the planner refuses a
target without them on both its branches. Across the nine runs since the guard
came alive there were **zero activation OOMs**; the guard engages when the
planner misses (run 5, 1,765 waits, before the activation branch was gated) and
is not needed once it does not (runs 6, 7, 8: none).

The clamp's cost had been measured in the ownership trace: `_physical_free_size`
is device-wide, so one GPU tightening made every pool on it report as nearly
full and cut every `PrefillAdder` budget at once -- model_2 reporting 360,847
tokens used while its live requests held 400, with 573,041 still available, and
four independent pools losing availability together inside one 0.84 s step. That
is what left run 7's sequence 5472 unable to start on a pool that was nearly
empty.

So the conclusion this change records:

> The physical clamp was needed while the activation guard was dead logic. The
> planner feasibility gate and the engine's allocation guard now cover that
> condition directly and in two places, and the clamp was left doing nothing for
> activation safety while cutting KV prefill liveness.

Run 8 bears that out on all three of the conditions that would have said
otherwise: **zero CUDA OOMs, zero activation waits, and zero decode
retractions** -- the last meaning no pool was driven into genuine KV pressure,
so the existing backpressure path was never needed rather than broken.

## Status

```text
D3 interaction: PASS
Next: 6-model c_i reprofiling, then tau calibration, then the final sweep.
```
