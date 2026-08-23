# A request re-entering the same GPU twice: frontend eviction and KV adoption

Status: **NEW correctness class. Pipeline STOPPED. No runtime fix applied.**

Run: `cal-0p00035-s0`, attempt 3, runtime `e3aa0ad` (dispatch-seq ownership).
Attempts 1-3 all preserved under `02-tau-calibration/raw/tau_0p00035/`.

## 1. The dispatch-seq ownership fix did its job

It fired **once** in the whole run, retiring exactly one sequence -- 1977,
`model_2#135`, on GPU 1 -- and nothing else. No sequence was retired that
should not have been, and the leak class that pinned GPU 0 at seq 1159 for 26
minutes did not recur.

| attempt | runtime | dispatched | admitted | per-GPU |
|---------|---------|-----------|----------|---------|
| 1 | pre-A+B | 519 | 517 | GPU0 488, GPU1 31 |
| 2 | A+B | 4,639 | 4,634 | GPU0 **1,163**, GPU1 3,476 |
| 3 | + ownership | 4,834 | 4,826 | GPU0 **2,458**, GPU1 2,376 |

GPU 0 went from stalling at 1,163 sequences to running 2,458. Both GPUs stayed
balanced instead of one dying early.

The watchdog fix also worked: the run was failed at **602 s** of phase time
instead of running to the 1800 s hard timeout, and it was killed for
`no actual progress` rather than by the backstop.

## 2. What failed instead

Not a deadlock. The runtime **fail-closed** on a genuine ordering violation and
shut itself down, which is the gate behaving correctly:

```json
{"event": "backend_admit", "gpu_id": 1, "alg2_seq": 2373,
 "actual_alg2_seqs": [2372], "actual_rids": ["model_2#176"],
 "actual_model": "model_2", "order_ok": false}
```

The acknowledgement carried sequence **2372**; the ledger record for that rid
said **2373**. One request, two sequences.

## 3. Two live copies of one request

`model_2#176` was dispatched twice on GPU 1, 23 ms apart:

```
02:08:39.215  GPU 1 dispatch seq 2372  model_2#176
02:08:39.239  GPU 1 dispatch seq 2373  model_2#176      <- second copy
02:08:39.251  GPU 1 dispatch seq 2374  model_2#177
02:08:39.264  GPU 1 dispatch seq 2375  model_2#177      <- second copy
02:08:39.308  GPU 1 backend_admit for 2372, record says 2373 -> order_ok false
```

The second dispatch overwrote `_mh_outstanding_prefills[rid]`, because the
ledger is keyed by rid and cannot hold two sequences for one request. The
acknowledgement for the first copy then no longer matched the record.

How both copies arrived:

```
02:07:15  GPU 1  seq 1885  model_2#176 dispatched, admitted, prefilled, completed
          GPU 1  migrated_away reason=kv-stash, 117 requests -- model_2 moves to GPU 0
          GPU 0  adopt_resumed queues model_2#176
02:08:28  GPU 0  migrated_away reason=evicted-to-frontend, rids=[model_2#176, model_2#177]
                 -> both go back to the frontend
02:08:39  GPU 1  frontend hands model_2#176 back    -> dispatch 2372
02:08:39  GPU 1  "adoption grant for an unknown request: model_2#176"
                 KV-migration adoption delivers the SAME request -> dispatch 2373
```

Two independent re-entry paths delivered the same request to the same GPU at
the same instant, and neither knew about the other. The frontend copy came from
GPU 0's eviction; the adoption copy came from the KV-migration machinery. The
engine's own log names the collision explicitly: *adoption grant for an unknown
request*.

## 4. Causal chain

> model_2#176 finished on GPU 1, was stashed with model_2's migration to GPU 0
> and queued there for adoption; GPU 0 then deactivated and evicted it to the
> frontend, so the frontend re-dispatched it to GPU 1 as sequence 2372 while the
> KV-migration path independently granted its adoption on the same GPU as
> sequence 2373; the rid-keyed ledger kept only the later record, so when the
> backend acknowledged 2372 the record said 2373, the admission gate found a
> mismatch it is designed never to tolerate, raised, and shut the run down at
> 4,545 of 8,423 completions.

## 5. Classification

**New class, and not caused by the ownership patch.** The sweep never touched
`model_2#176` or `model_2#177` -- it retired one sequence all run, for a
different request. The defect is that eviction-to-frontend and KV-migration
adoption are two re-entry paths for the same request with no reconciliation
between them, so a request caught mid-migration by a deactivation can be handed
back twice.

Attempts 1 and 2 never reached this state: both died earlier, on the leak
classes since fixed. Each fix has moved the run further and exposed the next
defect underneath, which is what a fail-closed gate is for.

## 6. Proposed minimal fix -- NOT APPLIED

Two candidates, both narrow; the second is preferable.

**(a) Reconcile at the point of re-entry.** `_handle_mh_adopt_resumed` already
refuses to queue a request that is in the ledger or the queue -- that guard was
written for D3 run 4. It failed here because the frontend copy had already been
dispatched *and removed from the queue* in the same 24 ms window, so neither
check saw it. Extending the guard to the dispatched-and-unacknowledged records
would close this instance, but it is a race window, not a rule: the reverse
order (adoption first, frontend second) is not covered, because the ordinary
dispatch path has no duplicate guard at all.

**(b) Make eviction and stash mutually exclusive for a given request.** A
request whose KV was stashed for migration is owned by the migration; it should
not also be evicted to the frontend by the source's later deactivation. Whoever
holds the request at eviction time should skip requests already handed to a
stash. This removes the second copy at its origin rather than catching it after
both exist, and it needs no duplicate detection anywhere.

Either way, the invariant to state first is the one this run lacked:

> A request has exactly one owner at any time. Eviction to the frontend and
> KV-migration adoption are alternative transfers of that ownership, never
> concurrent ones.

Before implementing, the full ownership timeline of `model_2#176` from stash
through adoption grant to eviction should be traced on the engine side, to
establish which component still believed it owned the request at 02:08:28 --
the same discipline applied to the previous two classes.

Tests to write first: the exact replay above; the reverse ordering (adoption
before the frontend copy); a request stashed but never evicted; a request
evicted but never stashed; and an assertion that one rid never holds two live
sequences on one GPU.

## 7. State

- Pipeline STOPPED at stage 02. `STOP` marker present, `FAILURE_AUTOPSY.json`
  written by the harness.
- Attempts 1, 2, 3 all preserved.
- Stage 01 (c_i) untouched, frozen c_i unchanged, runtime remains at `e3aa0ad`.
- No runtime file modified for this finding.
