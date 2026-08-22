# D3 run 7 tail hang: diagnosis

Diagnosis only. Nothing was changed in response to this, and no run was
repeated. The Algorithm 2 ordering and integration sub-gate passed in run 7 and
is treated as settled here; nothing below revisits it.

## The causal chain

> `model_2#1217` was backend-admitted on GPU1 as Algorithm 2 sequence **5472**
> at 16:11:01.591, but model_2's KV pool was at 100% occupancy (930,401 tokens,
> `available_size()` effectively 0), so `get_new_batch_prefill` could never form
> its prefill batch; because the shared per-GPU admission token advances **only
> at prefill start**, it stayed at 5472 for the remaining 17 minutes, gating
> every later request on GPU1 — sequences 5473, 5474, 5475 and the ~170 behind
> them across model_1, model_2, model_4 and model_6; no decode step was left to
> trigger `retract_decode` and free KV (zero retractions in the entire run), and
> the controller planned nothing for the idle GPU0 (19.82 GB free) because all
> four models were already marked active on GPU1 and that planner branch only
> activates *inactive* models.

## Timeline

```text
16:10:54.545  GPU_Scheduler_0: model_states all 'deactivated' -- GPU0 empty
16:10:55      GPU0's last engine event (model_5 deactivation, 0.67 s)
16:10:56.852  gpu1 dispatch seq 5471  model_6#1279
16:10:57.113  gpu1 dispatch seq 5472  model_2#1217
16:10:57.277  gpu1 dispatch seq 5473  model_2#1218
16:10:57.981  gpu1 dispatch seq 5474  model_4#928
16:11:00.027  model_2 last decode: #running-req 1, #token 930401, usage 1.00
16:11:01.589  gpu1 prefill_start seq 5471
16:11:01.590  model_2 engine: "Received 1 generation requests"   <- 5472
16:11:01.591  gpu1 backend_admit seq 5472  model_2#1217
16:11:01.657  gpu1 prefill_complete seq 5471
16:11:01.663  gpu1 dispatch seq 5475  model_4#929
16:11:10.250  GPU1 model_6 last decode: #running-req 1, usage 0.98
              -- no engine log of any kind after this, on either GPU --
16:28:02      benchmark hits its 1500 s limit, rc 124, 8,253 of 8,423 done
```

## Evidence for each link

**The frontier holder.** GPU1's runtime counts end at dispatch 5475, backend
admit 5472, prefill start 5471, prefill complete 5471. So one request was
admitted and never started (5472) and three were dispatched and never admitted
(5473-5475). Sequence 5472 is `model_2#1217`, which is also in the benchmark's
list of tasks still being waited on.

**Why its prefill never started.** model_2's engine logged `token usage: 1.00`
continuously from 16:10:49 to its last decode at 16:11:00, with `#token`
930,401. `available_size()` for an elastic pool is
`min(physical_free_blocks, kv_allocator.available_size())`; the physical side
was not the constraint — the controller read **3.96 GB free on GPU1** every
cycle to the end, far above the 0.5 GB the check keeps back — so the binding
term was the allocator's own availability, which at `token usage 1.00` is zero.
No free slots, no prefill batch.

**Why nothing freed KV.** `retract_decode` runs only when a decode step finds
insufficient memory. The whole run recorded **zero** retractions, and model_2's
decode batches stop at 16:11:00, so no decode step remained to trigger one.

**Why the GPU stopped rather than skipping the request.** The shared per-GPU
admission token is advanced by the engine at prefill start
(`compare_and_advance_int` in `get_new_batch_prefill`). With 5472 unable to
start, the token stays at 5472; every engine on GPU1 promotes a staged request
only when its sequence equals the token, so no model on that GPU could admit
anything further. This is the token's liveness property, not an ordering
defect: the ordering gate has no complaint, and none was raised.

**Whether the engines were dead or idle.** They were alive but idle-by-gate. The
GPU scheduler loop logged 386 further iterations and the controller 6,732 lines
after 16:11:10; the engines logged nothing because `get_next_batch_to_run()`
returned `None` on every pass — no runnable prefill (no KV), no running batch
left to decode.

**Whether the ~170 are blocked by one request or independently.** By one. All
four models still holding requests — model_1, model_2, model_4, model_6 — were
on GPU1 (`current_placement: {model_1: 1, model_2: 1, model_4: 1, model_6: 1}`),
and the admission token is per GPU, so all four queues sit behind sequence 5472.

**Whether the memory feasibility gate deferred them permanently.** No. The last
Algorithm 1 record shows `blocked: []` and `misplaced_models: []`, with
`migration_reason: "no measured load"`, and there is not one `No suitable GPU
found` line after 16:10:55. `rejected_by_memory` reached 45 over the run, but
none of those refusals is what left GPU0 idle: the planner branch that would
have used GPU0 activates **inactive** models, and all four models with pending
work were already active — on GPU1. Nothing asked for GPU0, so nothing was
refused.

## What this diagnosis does not establish

Why model_2's pool reports ~100% occupancy with one, then zero, running
requests. 930,401 tokens is far beyond what a single Qwen2.5-1.5B request can
hold, so the pool is carrying KV that no running request accounts for — but
whether that is unreturned KV from finished requests, from the run's 229
recompute rebuilds, or from the migration capture path cannot be settled by
reading logs. Establishing it needs allocator-level instrumentation
(allocated/freed blocks per request), which is a separate measurement and was
not run.

Two consequences follow from the chain regardless of that answer, and both are
about liveness rather than ordering:

- a request that is admitted but cannot be scheduled for a non-ordering reason
  stops its whole GPU, because the token has no way to move past it;
- a GPU with 19.82 GB free stayed idle while four models could not run, because
  the only branch that would place work there is the one for inactive models.
