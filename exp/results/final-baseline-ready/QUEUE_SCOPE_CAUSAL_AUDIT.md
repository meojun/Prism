# Does the GPU-scoped queue have a causal path to the regression?

Code + trace audit of existing artifacts. **No implementation, no run, no tuning.**

## Verdict

**No evidence that the queue scope change caused the performance regression.**
The chain breaks at its first link, and the break is measured, not argued.

## Step 1 — queue scope → scheduler state: BROKEN

The backend key is read at five sites. Four are transport (enqueue, fetch,
drain, pop_all) and feed no decision. Exactly one feeds a decision:
`model_backend_queue_lens` → `models_to_skip`
(`request_queue.py:188`, `request_queue_mh.py:107`), a set built from

```python
{m for m, n in model_backend_queue_lens.items() if n > self._skip_model_threshold}   # threshold = 10
```

The Algorithm 2 decision stream (`server.log.alg2_gpu*.jsonl`) records
`backend_queue_lens` on every round, so this is directly measurable:

| run | GPU | rounds | rounds where `models_to_skip` fired | max backend depth seen |
|---|---|---|---|---|
| run8 | 0 | 2,943 | **0 (0.0 %)** | 2 |
| run8 | 1 | 3,212 | **0 (0.0 %)** | 3 |
| run10 | 0 | 3,611 | **0 (0.0 %)** | 2 |
| run10 | 1 | 4,464 | **0 (0.0 %)** | 3 |
| runDIAG | 0 | 3,776 | **0 (0.0 %)** | 2 |
| runDIAG | 1 | 4,295 | **0 (0.0 %)** | 3 |

The observed depth never exceeded **3** against a threshold of **10**, in any
run, on either GPU, across ~22,000 scheduling rounds. Local depth (2–3) and the
global sum (≤ 5–6) are both far below the threshold, so **the set is empty under
either scope**. The one decision the queue scope can reach never changes.

**Nothing downstream can be attributed to the scope change, because the scope
change alters no decision.**

## Step 2 — Algorithm 1 placement: no path exists

`kvpr_global_v4.py` contains **zero** occurrences of `backend`. It reads
`model_queues` (the controller's `ModelQueueTracker`, fed by arrivals and
finishes) and GPU memory. It cannot see the backend queue, by key or by depth,
directly or indirectly.

So the placement imbalance (run8 2.62/2.62 → run10 2.22/3.15) **has no code path
from the queue change**. The correlation is real; the mechanism is absent.

## Step 3 — migration target/source selection: no path exists

Selection and timing in `_find_optimal_migrations` derive from KVPR, τ, the
memory feasibility check and the cooldown. None reads a queue depth. The same
grep result covers this: no `backend` reference anywhere in the policy.

## Step 4 — KV pressure: no path exists

KV usage (0.447 → 0.605) is a property of what the engines hold. The backend
queue holds requests *before* an engine takes them; its scope determines which
key an entry sits under, not how many entries exist or how long an engine keeps
a request. No code links the two.

## What the traces DO show, and where it points instead

One large behavioural difference is visible, and it is not the queue:

| run | GPU | mean local queue length | requests **blocked by model state** |
|---|---|---|---|
| run8 | 0 / 1 | 105.5 / 122.6 | **0 / 0** |
| run10 | 0 / 1 | 276.0 / 799.7 | **8,104 / 1,995** |
| runDIAG | 0 / 1 | 459.0 / 535.9 | **8,744 / 1,312** |

`blocked` is incremented in `request_queue_mh.py:117-125` when a queued request's
model is `deactivating`, `deactivated`, or `activating` without permission — or
is in `models_to_skip`, which we have shown is always empty. **So every one of
those blocks is a model-state block, not a queue-depth block.**

That is the same segment the timing decomposition isolated: requests sitting in
the scheduler's local queue, unable to be selected because their model is not
currently runnable on that GPU. It is a *placement and residency* effect, and
placement does not read the backend queue.

## Judgement

Per the stated rule, the chain is broken at step 1, so:

**No evidence that queue scope is the cause of the performance regression.**

`run8` remains a candidate **single lucky trajectory / run-to-run variance**, and
that reading is supported by what the archive shows: it is the only one of eight
old-queue runs that did not hit an orphaned sequence, the other seven died, and
there is therefore no old-queue distribution to place it in.

The open question is no longer about the queue. It is why run10 and runDIAG spend
so much scheduler-queue time blocked on model state while run8 spent none — a
placement/residency question, on a path the queue change cannot reach.

**STOP.** No design proposed, because the branch that would call for one was not
taken.
