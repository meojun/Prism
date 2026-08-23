# run8 → run10: where the time went

Offline analysis of existing artifacts. **No code changed, no run executed.**
Same workload (`bursty_r20_s1.pkl`, sha `666c453234`), same τ = 0.07, all runs.

## A. The wait segment

Per-request timestamps exist in the client's `*_output_requests.json`
(`arrival_time`, `gpu_scheduler_queue_time`, `gpu_scheduler_dispatch_time`,
`out_queue_time`, `prefill_finish_time`), so TTFT decomposes exactly. Records
with impossible ordering or > 1 h span were dropped (119 / 87 / 51 of ~8,400).

| stage | run8 | run10 | runDIAG | Δ vs run8 (run10) | share of the increase |
|---|---|---|---|---|---|
| arrival → scheduler intake | 1.190 s | 3.944 s | 4.128 s | +2.75 s | 4.2 % |
| **scheduler queue → dispatch** | **5.941 s** | **67.936 s** | **59.945 s** | **+61.99 s** | **95.5 %** |
| dispatch → engine fetch | 0.108 s | 0.273 s | 0.286 s | +0.16 s | 0.3 % |
| prefill execution | 0.047 s | 0.068 s | 0.069 s | +0.02 s | 0.0 % |
| **TTFT** | 7.286 s | 72.221 s | 64.427 s | +64.9 s | |

**95 % of the increase is in one segment: the wait inside the GPU scheduler
between receiving a request and dispatching it.** Not the backend fetch (+0.16 s),
not prefill (+0.02 s). The queue split touches transport, and transport is where
the time did *not* go.

## B. The runtime behaviour behind that segment

Decode, from the engine logs:

| | run8 | run10 | runDIAG |
|---|---|---|---|
| decode batches logged | 1,220 | 826 | 834 |
| run span | 456 s | 633 s | 607 s |
| **decode batches / second** | **2.68** | **1.30** | **1.37** |
| mean running requests per batch | 28.1 | 41.0 | 40.8 |
| **mean generation throughput** | **611.8 tok/s** | **415.3 tok/s** | **386.0 tok/s** |
| mean KV token usage | 0.447 | 0.605 | 0.671 |
| retractions | 10 | 8 | 5 |

run10 executes decode iterations at **half** run8's rate, with **larger** batches
and **higher** KV pressure, and generates ~32 % fewer tokens per second. Requests
therefore occupy engines longer, `_mh_outstanding_prefills` stays high, and the
scheduler holds new work in its local queue — which is precisely the segment that
grew.

Placement, time-weighted from activation/deactivation events:

| | GPU0 mean active | GPU1 mean active | balance |
|---|---|---|---|
| run8 | 2.62 | 2.62 | even |
| run10 | 2.22 | 3.15 | **imbalanced** |
| runDIAG | 2.70 | 3.05 | imbalanced |

Model-seconds with no host: run8 13.2 %, run10 11.0 % — comparable, so
"the model was unavailable" is **not** the cause.

## C. Can the behaviour be recovered while keeping the correctness fix?

**Not answerable yet, and two plausible answers were refuted by measurement
rather than argued away.**

- **Refuted: instrumentation overhead.** runDIAG carries the heaviest
  instrumentation of the three (52,021 events plus client receipts) and is the
  *fastest* of the GPU-scoped runs (14.4 vs 13.75 vs 11.64). Instrumentation
  load and throughput move in opposite directions.
- **Refuted: shrunken dispatch window.** `dispatch_budget` derives from the
  count of active models per GPU. runDIAG averages 2.70 / 3.05 against run8's
  2.62 / 2.62 — *more* active models — and is still slower. The window is not
  the constraint.
- **Not established: the queue split as the cause.** The split changes where a
  request is stored between dispatch and fetch. That interval is `dispatch →
  engine fetch`, measured at 0.108 s → 0.273 s: **0.3 % of the regression**. No
  trace links the split to the decode rate, the KV pressure or the placement
  imbalance, and Algorithm 1's placement reads GPU memory and measured rates,
  never this queue.

The cross-GPU work-sharing hypothesis (task 3) is **not supported** by the one
measurement that would show it: if the old shared queue had let a target engine
drain work the source had enqueued, that would appear as a shorter `dispatch →
fetch` interval in run8. It is 0.108 s there and 0.273 s in run10 — a 0.16 s
difference, against a 65 s regression.

**What is missing to close this:** run8 is the only successful old-queue run in
the archive (the other seven all carried orphaned sequences and died), so there
is no old-queue distribution to compare against. Both remaining candidates —
that the decode-rate difference is a property of the build, or a property of that
one run — require repeated measurement to separate, and no artifact can settle it.

## Recorded

- The regression is real and localised: the scheduler-side queue wait.
- Its proximate cause is a **halved decode iteration rate under higher KV
  pressure**, not a transport delay.
- The link from that to the GPU-scoped queue is **unproven**, and the two
  mechanisms by which the split could plausibly have caused it were measured and
  found not to.

**STOP.** No implementation.
