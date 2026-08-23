# Prerequisite: can a same-GPU activation demand the slot before the verdict?

Question: between a `DeactivateReqInput` going out and the engine's verdict
being applied, can another model's activation on the same GPU be issued and
demand that worker slot?

**Answer: not from the issuing side — proved below. But the GPU scheduler drains
its two inboxes in an order that can invert the two events after they arrive, so
impossibility is _not_ proved end to end. STOP, with a one-line plumbing
proposal that closes it.**

Nothing implemented. Runtime is the frozen `e3aa0ad`; the working tree's patch
blob is `843815cf…`, identical to `HEAD`.

## 1. Issuing side: proved impossible

**(a) Activations and deactivations are never in the same batch.**
`action_order_v6.build_action_batches` returns, for the configured arm
(`overlap_migration=True`, `kv_migration=True`):

```
(ordinary activations, 1)
(prepares,             1)
(middle,               n)
(deactivations,        n)
(commits,              1)
```

Every activation that can demand a slot is in a batch strictly *before* the
deactivations.

**(b) Batches run sequentially.** `controller_global.execute_actions` runs each
batch as `with ThreadPoolExecutor(max_workers=...) as threads:` — the context
manager joins on exit, so batch *k+1* cannot start until batch *k* has fully
completed.

**(c) The only batch after the deactivations cannot demand a slot.** `commits`
carry `phase="commit"`, and `worker_pool.handle_activate_model` short-circuits
on that phase: it looks up `self._model_to_worker.get(model_name)` and returns
False if absent. It never calls `get_idle_worker` or `assign_worker`.

**(d) Each deactivation blocks until the engine has ruled.**
`DeactivateAction.execute` is a blocking `requests.post(f"{url}/deactivate")`,
and under `overlap_migration` the handler awaits
`_send_req_and_wait_for_response(req)`. The response body carries `success` and
`memory_usage`, both taken from the engine's `DeactivateReqOutput` — so the HTTP
call cannot return before the engine has produced its verdict.

**(e) The one path that pairs activate with deactivate directly is sequential
and cross-GPU.** `OverlapMigration.execute` runs `activate.execute(...)` on
`target_gpu_id` to completion, returns False if it fails, and only then runs
`deactivate.execute(...)` on `source_gpu_id`. Source and target differ by
definition of a migration.

Therefore: **no activation requiring a worker slot on GPU g is ever issued while
a deactivation on GPU g is outstanding.** The next cycle's activations are
issued only after `execute_actions` has returned, which is after every
deactivate verdict of this cycle.

## 2. Consuming side: not proved

The proof above orders the *issuing* of the two messages. It does not order the
scheduler's *processing* of them, and they arrive on different channels:

- the verdict arrives as `DeactivateReqOutput` over Redis
  (`engine_to_gpu_scheduler:{gpu_id}`), consumed by `_recv_from_engine`;
- the activation arrives as `ActivateReqInput` over ZMQ, consumed by
  `_recv_from_request_handler`.

`GPUScheduler._recv_requests_loop` (gpu_scheduler.py:341) drains them in this
order:

```python
reqs = self._recv_from_frontend_queue()
self.queue.add_requests(reqs)
self._recv_from_request_handler()      # ActivateReqInput
self._recv_from_engine()               # DeactivateReqOutput
```

The request handler is drained **first**. §1 guarantees the verdict is enqueued
before the activation is enqueued, but if the receive loop is stalled long
enough that both are pending when it next runs, the activation is handled first.

With the deferral in place, that inversion is not benign:
`handle_activate_model` would find `get_idle_worker(tp_size)` returning None,
return False, and `gpu_scheduler` sets the model to `"deactivated"` — a silently
failed activation.

The window is large: it requires the receive loop to be blocked for the whole of
engine→handler verdict + HTTP response + the controller's next-cycle
computation + HTTP `/activate` + ZMQ send. In attempt 3 the observed
deactivate-succeeded → activate-received gaps were 1.6 s and 4.0 s, and the
receive loop has no sleep. So it is implausible, not impossible — and
"implausible" is what the previous three failures were made of.

## 3. Minimal plumbing proposal

**Swap the two drains so the scheduler applies verdicts before it accepts new
control requests:**

```python
reqs = self._recv_from_frontend_queue()
self.queue.add_requests(reqs)
self._recv_from_engine()               # verdicts first
self._recv_from_request_handler()
```

Why this and nothing else:

- It makes the scheduler's processing order match the causal order §1 already
  guarantees for enqueueing. The verdict is always enqueued first; now it is
  always applied first.
- No forced reclaim of a slot another model holds, and no double assignment —
  the situation those would be needed for can no longer arise.
- No new state, no retry, no timeout, no heuristic. One statement reordered.
- Nothing depends on control requests being handled before verdicts. Applying a
  verdict earlier only makes `_model_states` and the slot table *more* current
  for everything later in the same iteration, including
  `_recv_from_frontend_queue`'s eligibility check on the next pass.

Classification: **CORRECTNESS-PLUMBING**. It touches no scheduling decision, no
τ, no c_i, no Algorithm 1 or 2, no migration or activation policy, no KV
allocator, no workload or SLO, and no admission ordering.

It does change the successful path — verdicts land one drain earlier — so it
needs its own test alongside the deferral tests:

- a verdict and an activation pending in the same iteration: the verdict is
  applied first and the activation finds its slot;
- the same scenario on the current order, asserted to fail, as a negative
  control;
- ordinary interleavings unchanged.

## 4. What is approved and unchanged

The rest of §3 of the classification stands as approved:

| verdict | model state | queue | placeholder | engine payload | `_alg2_adoption_requested` | worker | frontend deliveries |
|---|---|---|---|---|---|---|---|
| SUCCESS | `deactivated` | popped | discarded | released | cleared | released | exactly 1 per ordinary request |
| FAIL | `activated` | untouched | untouched | retained | untouched | retained | 0 |

No new adoption retry trigger; `_alg2_request_adoption` keeps its single caller.

## 5. State

- STOPPED, awaiting a decision on §3. Runtime `e3aa0ad`, verified byte-identical
  to the frozen patch blob. Attempts 1-3 preserved, c_i untouched.
- Nothing implemented.
