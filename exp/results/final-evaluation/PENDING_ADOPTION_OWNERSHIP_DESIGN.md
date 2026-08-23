# `_alg2_pending_adoption` ownership: state machine and the P2 decision

Design only. **Nothing is implemented.** Runtime remains at `e3aa0ad`.

## 0. The invariant being designed to

> Every request has exactly one authoritative owner at every instant.
> Ownership moves to the frontend only when the request has genuinely left the
> source. A deactivation that rolls back leaves ownership where it was.

## 1. State machine

States a re-entrant request can occupy on one GPU:

```
        ┌──────────────────────────────────────────────┐
        │                                              │
  (retraction | KV inject)                             │
        ↓                                              │
  ENGINE_HELD ──adoption_requested──→ ENGINE_HELD+REQUESTED
        │                                   │
        │                          scheduler queues placeholder
        │                                   ↓
        │                          SPLIT(engine=payload, sched=proxy)
        │                                   │
        │                              dispatch(seq)
        │                                   ↓
        │                            grant → ENGINE_WAITING ──→ served
        │                                   
        └── deactivation ──┬── success  ──→ FRONTEND        (payload shipped)
                           └── rollback ──→ ENGINE_HELD     (payload retained)
```

Owner by state:

| state | authoritative owner | frontend copy | placeholder |
|---|---|---|---|
| ENGINE_HELD | source engine (`_alg2_pending_adoption`) | none | none |
| ENGINE_HELD+REQUESTED | source engine | none | none (grant in flight) |
| SPLIT | source engine — the placeholder is a **proxy, not a request** | none | in scheduler queue |
| ENGINE_WAITING | source engine (waiting queue, sequence assigned) | none | consumed |
| FRONTEND | frontend queue | one | none |

The bug in attempt 3 was that SPLIT was treated as two owners: the scheduler
returned the proxy to the frontend as if it were a request, while the engine
returned the payload. SPLIT must collapse to exactly one owner on every exit.

## 2. P2 decided first: stash capture fails, source stays active

### Alternative A — return the payload to the frontend

| criterion | assessment |
|---|---|
| request loss | none — the frontend holds it until any GPU activates the model |
| duplicate frontend delivery | none **iff** the placeholder is discarded; two copies otherwise (the attempt-3 bug) |
| `_alg2_adoption_requested` | cleared by `_evict_all_waiting_requests` (:2212) — already correct |
| placeholder lifecycle | discarded at deactivation |
| adoption retry | not needed; the request re-enters as an ordinary frontend arrival |
| source serving after rollback | irrelevant — the request no longer depends on this GPU |
| single owner | yes: engine → frontend, one transition |

Sound, and implementable with no other repair. But it **violates the stated
principle**: the source remains active, yet its requests are shipped away.

### Alternative B — the engine keeps the payload (the preferred principle)

| criterion | assessment |
|---|---|
| request loss | **YES, as the code stands — see the proof below** |
| duplicate frontend delivery | none (nothing is sent) |
| `_alg2_adoption_requested` | must be cleared for the retained rids, or no re-request is possible |
| placeholder lifecycle | discarded; must be recreated by a fresh adoption request |
| adoption retry | requires an explicit `_alg2_request_adoption(...)` call — see §3 |
| source serving after rollback | **NO — the scheduler never restores it** |
| single owner | yes in principle: ownership never leaves the engine |

### Proof that B cannot be implemented as the code stands

Three independent code facts, all on the rollback path:

1. **The scheduler never restores the model state.**
   `gpu_scheduler.py:405` is `if success: self._set_model_state(model_name, "deactivated")`
   with **no else branch**. On `success=False` the state remains
   `"deactivating"`, set at the start of the deactivate branch.

2. **`"deactivating"` excludes the model from both intake and admission.**
   `_recv_from_frontend_queue` (:359) pulls only for states in
   `("activating", "activated")`. `RequestQueue.admission_control`
   (request_queue.py:206) skips any model whose state is
   `("deactivating", "deactivated")`.

3. **The worker slot is already gone.** `worker_pool.handle_deactivate_model`
   calls `release_worker(model_name)` (worker_pool.py:231) *before* forwarding
   the request to the engine, popping the model from `_model_to_worker` and
   pushing the slot onto `_free_workers` — where another model may claim it.

Meanwhile the engine sets `self._activated = True` and logs *"keeping source
active"* (scheduler.py:2037-2039).

So after a P2 rollback the model is a zombie: **active in the engine, deactivating
in the scheduler, with its worker slot returned to the free pool.** A payload
retained under Alternative B could never be adopted, because no dispatch will
ever be issued for a `deactivating` model. The retained request would be lost as
surely as under a naive discard — just more quietly.

This is a **pre-existing defect independent of adoption**: P2's rollback does not
actually restore the source. It has never fired in any of the three attempts
(`relay did not acknowledge stash`: 0 occurrences), which is why it has gone
unnoticed.

### Decision

**Alternative B is the correct target and is blocked.** It becomes correct only
once the rollback is repaired so that `success=False` genuinely restores the
source:

- `gpu_scheduler.py` gains the missing `else` branch, restoring the state to
  `"activated"`;
- the worker slot released in `handle_deactivate_model` is re-acquired for the
  model on a failed deactivation (or, better, released only after a successful
  verdict);
- only then does the engine clear `_alg2_adoption_requested` for its retained
  rids and re-issue `_alg2_request_adoption(reason="stash-rollback")`.

Until that repair exists, **Alternative A is the only safe behaviour for P2**,
and it must be labelled for what it is: a fallback that ships requests away from
a source the engine believes is still serving, chosen because the source's claim
to be serving is not true at the scheduler.

Recommended order: repair the P2 rollback first (it is a real defect on its own),
then adopt B for P2, then the placeholder discard becomes unconditionally safe.

## 3. Retry path for a retained payload (needed by B)

`_alg2_request_adoption` (:2518) is called from exactly one place —
`_alg2_hold_for_reentry` (:2516). There is no periodic re-request. It also skips
any rid already in `_alg2_adoption_requested` (:2531), and that set is cleared
only by `_evict_all_waiting_requests` (:2212) or by an adoption grant (:2606).

Therefore retaining a payload requires an explicit rollback step:

```
for rid in self._alg2_pending_adoption:          # retained, not shipped
    self._alg2_adoption_requested.discard(rid)
self._alg2_request_adoption(reason="stash-rollback")
```

This reuses the existing adoption path rather than adding a mechanism: the
request re-enters through the same `AdoptResumedReq`, keeps its original
`arrival_time` and `slo`, and takes a sequence only when the target's
Algorithm 2 selects it. It is inert unless the scheduler-side rollback above is
also fixed, because the placeholder it queues would sit in a `deactivating`
model's queue and never be dispatched.

## 4. Final path table (proposed design, after the P2 rollback repair)

Payload release moves out of `_evict_all_waiting_requests` into an
unconditional step of `handle_deactivate_request`, ahead of **both** early
returns; the scheduler discards `alg2_resumed=True` placeholders instead of
returning them.

| path | deactivation outcome | authoritative owner after | placeholder action | payload action | frontend deliveries | retry path |
|---|---|---|---|---|---|---|
| **P0** | no-op (already inactive) | frontend | discard | released → frontend | **1** | ordinary frontend dispatch |
| **P1** | success (V1) | frontend | discard | evicted → frontend | **1** | ordinary frontend dispatch |
| **P2** | **rollback, source restored active** | **source engine** | discard | **retained** | **0** | clear `_alg2_adoption_requested`, re-issue `_alg2_request_adoption` |
| **P3** | success (V2) | frontend | discard | evicted → frontend | **1** | ordinary frontend dispatch |
| **P4** | success, V6 KV off | frontend | discard | released → frontend | **1** | ordinary frontend dispatch |

**Exactly one owner, every row.** The placeholder is never an owner — it is a
proxy that is either consumed by a dispatch or discarded, so SPLIT always
collapses to one side. On P0/P1/P3/P4 ownership transfers engine → frontend
once. On P2 it never leaves the engine.

**Frontend deliveries are 0 or 1, every row.** 1 wherever ownership transfers,
0 on the only path where it does not. The two-copy case is structurally
impossible because the only component that can deliver to the frontend is the
one holding the payload.

## 5. What must be proven by tests before implementing

- P2 with a pending adoption: payload retained, placeholder gone, model restored
  to `activated`, worker slot re-acquired, adoption re-requested, request served.
- P2 without the rollback repair: assert the design refuses to retain (guard
  against shipping this in the wrong order).
- P4: decode-retracted request, V6 KV off, released exactly once.
- P0 reached after P2 or P4.
- P1/P3 unchanged: one frontend copy, placeholder discarded.
- No rid ever appears twice in the frontend queue for one deactivation.
- No rid is ever simultaneously authoritative in two of
  {frontend, engine pending/waiting, scheduler queue}.

## 6. State

- Pipeline STOPPED. Runtime `e3aa0ad`. Attempts 1-3 preserved. c_i untouched.
- Nothing implemented: no placeholder discard, no rollback repair, no duplicate
  guard, no timeout, no overwrite defence.
