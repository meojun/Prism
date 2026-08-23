# tau calibration, tau=0.00035 seed 0: the ordering violation

Diagnosis only. No code was changed, no run repeated, and tau, c_i and every
policy are untouched.

## The causal chain

> `model_3#3` was dispatched on GPU1 as sequence 29 at 18:32:39.726 and fetched
> into its engine's `_alg2_staged_generation_reqs` within 73 ms; the deactivate
> that arrived at 18:32:39.798 found `waiting queue size: 0`, so
> `_evict_all_waiting_requests` took its early return -- draining the Redis
> queue and returning **before** the two lines that report staged requests as
> `staged-never-admitted` and clear the staged list -- leaving the request in a
> slot that was then reassigned to model_5; 66 seconds later that engine
> promoted it, because its sequence still matched the shared admission token,
> and announced the admission with `model=self.model_name`, which by then read
> `model_5`, so the per-GPU gate saw a sequence-29 admission for a request its
> ledger records under model_3 and failed closed.

## Timeline

```text
18:32:29.249  GPU1 Worker 1 activates model_3
18:32:34.252  "Waiting requests restored"; scheduler activated (7.45 s)
18:32:39.726  GPU1 scheduler dispatches seq 29, model_3#3, to model_3's Redis queue
18:32:39.798  the same engine receives DEACTIVATE
              "In handle deactivate request, waiting queue size: 0. Running batch size: 2"
18:32:39.799  backend_queue_drained: drained=0      <- the request was no longer in Redis
18:32:39.827  migrated_away_reported: reason=kv-stash, 2 rids  <- the running pair only
18:32:39.828  backend_queue_drained: drained=0
18:32:41.026  deactivate completes
   ...        the slot is reassigned; Worker 1 becomes model_5
18:33:45.331  Worker 1 (model_5): backend_admit seq 29, rid model_3#3, model model_5
              order_ok FALSE -- the ledger has seq 29 under model_3
18:33:45.331  the same worker logs first_request_on_target for model_5's migration
```

`drained=0` at 18:32:39.799 is the load-bearing observation: 73 ms after the
dispatch the request was already out of Redis, so it was in the engine's staged
list, which is the one place the deactivate never looked.

## A or B: both, and which is which

**B -- a stale request survived the reassignment. This is the root cause.**
`_evict_all_waiting_requests` reads:

```python
self._alg2_pending_adoption = {}
self._alg2_adoption_requested.clear()
if not self.waiting_queue:
    self._drain_backend_queue()
    return                      # <-- taken here: the waiting queue was empty
...
self._alg2_report_migrated_away([...], "evicted-to-frontend")
self._alg2_report_migrated_away([...staged...], "staged-never-admitted")
self._alg2_staged_generation_reqs = []
self.waiting_queue.clear()
self._drain_backend_queue()
```

The early return exists so the Redis drain still runs when the waiting queue is
empty -- which was the right instinct, since D3 run 2 stalled on exactly that
case. But it returns before the staged list is reported and cleared, so an
empty waiting queue silently means "leave the staged requests where they are".
The log confirms it: the deactivate reported `kv-stash` for the two running
requests and nothing else. No `staged-never-admitted` line was written, for a
request that was staged and never admitted.

That also means the source GPU never retired sequence 29, so its ledger kept
the entry and its frontier kept expecting it -- which is why the admission 66
seconds later was still the sequence the token was waiting on.

**A -- the admission carried the slot's current name. This is what made it
visible, not what caused it.** `process_input_gen_requests` builds

```python
BackendAdmitReq(rids=[req.rid], model=self.model_name, ...)
```

`self.model_name` is mutable: it follows the slot, not the request. The same
defect was fixed on the adoption path in 84f880f, where a grant is now checked
against the model it was issued for; the ordinary dispatch path still tags the
admission with whatever the slot is called at that moment.

With B fixed no stale request should reach that line. A is worth fixing anyway,
because the gate's evidence should not be able to be misled by a slot rename --
and because it is what turned a silent cross-model admission into a caught one.

## Why this did not appear before

tau = 0.00035 is the most aggressive candidate, so activations and
deactivations churn far more than at the tau = 0.07 D3 ran at. The window that
matters here is 73 milliseconds wide -- a dispatch landing between an engine's
fetch and a deactivate it has not yet processed. D3 never opened it.

## Minimal fix, proposed and not applied

1. **B**: move the staged-request report and the staged-list clear above the
   early return, so a deactivation always releases the staged list whatever the
   waiting queue holds. One block moved; no new rule, no new timeout, and the
   Redis drain the early return was added for keeps running.
2. **A**: have the ordinary admission carry the model the request was scheduled
   under rather than `self.model_name`, matching what 84f880f already does for
   adoption grants.

Both are accounting corrections at the same seam the handoff invariant
describes. Nothing about tau, c_i, Moore--Hodgson, migration policy or the
workload is involved.
