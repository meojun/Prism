# D3 Algorithm 2 x migration interaction gate

## Verdict

```text
D3 INTERACTION GATE : FAIL -- concrete integration gap
STOP C: no tau calibration, no c_i reprofiling, no sweep.
```

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

## What happened

The run served 882 requests and then stopped. No CUDA fault, no NCCL error, no
OOM, no crash, and no request was lost -- the server simply stopped making
progress and the watchdog ended it.

```text
13:22:11  MIGRATE model_4 GPU0 -> GPU1
13:22:1x  17 [PAPER-KV-V6] "resume" events: model_4's in-flight requests are
          rebuilt on GPU1 from their migrated KV
13:22:22  PAPER-ALG2-RUNTIME prefill_complete, model_4, 17 rids,
          alg2_seq null, expected_rid null, order_ok FALSE
13:22:22  GPU_Scheduler_1: "WorkerPool GPU 1 cleanup completed"
13:22:31  last GPU0 activity
13:22:44  last served response
13:22:50  controller sends a deactivate and never returns
```

The ordering gate did exactly what it was built to do. `_handle_mh_prefill_complete`
looks each completing request up in `_mh_outstanding_prefills`, finds nothing,
and fails closed -- `_shutdown_event.set()` and a raise -- rather than accept a
completion it cannot place in the global order. GPU scheduler 1 went down with
it, GPU scheduler 0 kept looping with nothing to do, and the controller blocked
on a deactivate that would never be acknowledged.

## The gap

Algorithm 2's runtime bookkeeping is per GPU scheduler. A request is entered
into `_mh_outstanding_prefills` on the GPU that *dispatched* it, carrying that
GPU's monotonic `alg2_seq`, and is removed when its prefill completes there.

KV migration moves in-flight requests to another GPU. `build_resumed_request`
rebuilds them on the target and they re-enter prefill there. Nothing hands them
to the target's Algorithm 2 bookkeeping: they were dispatched under GPU0's
sequence and complete under GPU1's gate, which has no record of them.

The 17 rids in the failing message are exactly the 17 resume events, and their
request numbers (`model_4#1`, `#2`, `#7`, `#10` ...) are the old, long-running
requests that were in flight when model_4 moved -- not the freshly dispatched
`model_4#100..#102` that carried sequences 642-645 moments earlier.

So this is not a scheduling-policy question and not a Moore--Hodgson defect. It
is an integration gap between two mechanisms that were each verified alone:

```text
D1  Algorithm 2 correct, migration off   PASS
D2  migration correct, Algorithm 2 off   PASS (runs 5, 6, 9)
D3  both on                              FAIL, at the seam
```

## Gate output

`exp/scripts/check_alg2_interaction.py`, full output in
`d3_run1_interaction.json`. The same script passes the D1 Algorithm 2 evidence
and fails a run with Algorithm 2 off, so it discriminates in both directions.

| check | result |
|---|---|
| Algorithm 2 ran | PASS (3,521 runtime events) |
| no Algorithm 2 order violation | **FAIL** (1, the resumed batch) |
| sequence tokens monotonic | PASS |
| sequence tokens have no gaps | PASS |
| admission ordered across migrations | **FAIL** (same event) |
| outstanding work retired | **FAIL** (net 3 on each GPU) |
| pipeline rc zero | **FAIL** (143) |
| no deadlock | **FAIL** |
| no request loss | PASS |
| no migration-induced abort | PASS |
| no fatal CUDA or NCCL | PASS |

`outstanding work retired` failing with a net of 3 per GPU is the same defect
seen from the accounting side: requests that left one GPU's outstanding map by
migrating never arrived in the other's.

## What a fix has to decide, and why it is not made here

Two shapes are possible, and they are not equivalent:

1. **Adopt resumed requests into the target's schedule.** The target assigns
   them a fresh `alg2_seq` when it rebuilds them, so they are ordered and
   accounted like any other request on that GPU. This is the more faithful
   reading -- a resumed request really does occupy prefill capacity on the
   target, and Algorithm 2 is supposed to be the order in which that capacity
   is granted.

2. **Exempt them from the ordering check** while still counting their work as
   outstanding. Smaller, but it puts requests through prefill on a GPU whose
   global order did not schedule them, which weakens exactly the invariant D1
   established.

Choosing between them changes what "the global order" means for a migrated
request, so it is a decision to be taken deliberately rather than folded into a
bug fix. Nothing was changed in response to this run.

## Status

```text
STOP C. D2 stays PASS; D3 fails at the Algorithm 2 x migration seam.
No tau calibration, no c_i reprofiling, no final sweep.
```
