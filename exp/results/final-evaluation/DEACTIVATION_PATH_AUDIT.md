# Deactivation path audit: is discarding the adoption placeholder safe?

Question: can the GPU scheduler discard an `alg2_resumed=True` adoption
placeholder at deactivation instead of returning it to the frontend, without
ever losing the real request payload the engine holds?

**Answer: no. STOP. Do not apply the discard.** There are reachable
deactivation paths on which the engine never releases
`_alg2_pending_adoption`, and on those the placeholder is the request's only
remaining representation.

No code was changed.

## 1. Who can hold a pending adoption

`_alg2_pending_adoption` is written only by `_alg2_hold_for_reentry`
(scheduler.py:2501), which returns early into the waiting queue unless
`_alg2_runtime_gate` is on. It has **two** producers:

| producer | site | requires v6 KV? |
|---|---|---|
| KV-migration resume | scheduler.py:2324, inside `_v6_inject_migrated_kv` | **yes** — the function returns early when `_v6_kv_enabled()` is false |
| **decode retraction** | scheduler.py:1313 | **no** — plain memory-pressure retraction during decode |

The second producer is the one that breaks the safety argument: pending
adoptions are *not* confined to configurations where V6 KV migration is on.

## 2. Engine-side deactivation paths

`Scheduler.handle_deactivate_request` (scheduler.py:1947) has two eviction
sites and three exits:

- **V1** :1990 — `if recv_req.evict_waiting_requests or (recv_req.preempt and preempt_mode == RECOMPUTE)`
- **V2** :2055 — `if self._v6_kv_enabled()`
- **E1** :1982 — already deactivated → return before everything
- **E2** :2052 — `_v6_stash_captured()` false → sets `_activated = True`, returns
- otherwise falls through V2 to `deactivate_model_runner()`

`_evict_all_waiting_requests` (:2195) is the *only* code that releases
`_alg2_pending_adoption`, and it is also the only place other than an adoption
grant that clears `_alg2_adoption_requested` (:2212).

## 3. Producers of `DeactivateReqInput`

| producer | evict_waiting_requests | preempt | mode |
|---|---|---|---|
| `simple_global.py:850` | **True** | – | – |
| `simple_global.py:958` | **True** | – | – |
| `overlap_migration.py:41` | **False** | **False** | RECOMPUTE |
| `controller_global.py:539` (activation rollback) | **False** | **False** | RETURN (default) |
| `io_struct.py:437` default | True | False | RETURN |
| `action.py:112` `DeactivateAction` default | **False** | – | – |

`overlap_migration` sets `preempt_mode="RECOMPUTE"` but `preempt=False`, so V1's
second disjunct is false. Both `evict=False` producers therefore depend entirely
on V2.

## 4. Path matrix

| path | conditions | pending adoption possible? | scheduler placeholder action | engine payload action | safe to discard? |
|---|---|---|---|---|---|
| **P0** | E1: `not _activated and _v6_prepared_model is None` | yes (see P4/P2 leave it set) | popped → frontend, **unconditionally** | **none** — returns before V1 | **NO** |
| **P1** | V1 taken: `evict=True`, or `preempt and RECOMPUTE` | yes | popped → frontend | `_evict_all_waiting_requests` → frontend, exactly once | yes |
| **P2** | V1 skipped, `_v6_stash_captured()` false | yes | popped → frontend | **none** — returns; `_activated` restored to True | **NO** |
| **P3** | V1 skipped, stash ok, `_v6_kv_enabled()` true | yes | popped → frontend | V2 → `_evict_all_waiting_requests` → frontend, exactly once | yes |
| **P4** | V1 skipped, stash ok, `_v6_kv_enabled()` **false** | **yes, via decode retraction** | popped → frontend | **none** — V1 and V2 both skipped | **NO** |

The scheduler's action is identical on every row: the
`DeactivateReqInput` branch at gpu_scheduler.py:705 calls
`pop_model_requests(model_name)` and `_send_waiting_reqs_to_frontend_queue`
**unconditionally**, before and independently of the engine's outcome. The
attempt-3 log confirms the ordering empirically: the scheduler sent at
02:08:28.728 and the engine reported its eviction at 02:08:28.739, 11 ms later,
with `dequeued_before_dispatch: []` because the queue was already empty.

## 5. Reachability of the unsafe rows

**P4 — reachable, and configured.** Three arms combine `--enable-moore-hodgson`
(so `_alg2_runtime_gate` is on and placeholders exist) with `--overlap-migration`
(so `evict_waiting_requests=False`) and **no** `PRISM_V6_KV_MIGRATION`:

| arm | moore-hodgson | overlap-migration | V6 KV |
|---|---|---|---|
| `paper-faithful-v3` | yes | yes | **no** |
| `paper-faithful-v3-alg2only` | yes | yes | **no** |
| `paper-faithful-v4` | yes | yes | **no** |
| `paper-faithful-v6` | yes | yes | yes |

On those three, a decode retraction populates `_alg2_pending_adoption` and an
overlap migration then deactivates with V1 and V2 both skipped. Nothing releases
the payload. Not reachable in the final pipeline, which runs only
`paper-faithful-v6` and `released-prototype` — but reachable in the code and in
the committed experiment arms, so it cannot be discharged as impossible.

**P2 — reachable in every arm, including `paper-faithful-v6`.**
`_v6_stash_captured()` returns false when the relay queues are missing or the
acknowledgement is wrong or does not arrive within its 30 s `get(timeout=30)`.
It is an ordinary error path with its own log line. It did not fire in any of
the three attempts (`relay did not acknowledge stash`: 0 occurrences in all
three), but nothing prevents it.

P2 is the worst row, because the loss is permanent rather than merely awkward:
the engine keeps the payload, restores `_activated = True`, and the rid stays in
`_alg2_adoption_requested`. `_alg2_request_adoption` (:2531) skips any rid in
that set, and the set is cleared only by `_evict_all_waiting_requests` — which
this path did not run. So no further adoption request would ever be issued for
it. With the placeholder returned to the frontend, as today, the request
survives (as a duplicate). With the placeholder discarded, it is **lost with no
recovery path**.

**P0 — reachable only as a consequence of P2 or P4.** `_activated` is set false
only inside this handler, which then evicts unless it returns at E2 (which
restores it) or falls through P4. So a pending adoption can survive into a
subsequent deactivate only where P4 already applies. Listed as unsafe for
completeness; it is not an independent hazard.

**Rows proven safe.** P1 and P3 both reach `_evict_all_waiting_requests`, which
moves every `_alg2_pending_adoption` entry into `waiting_queue`, frees its
injected KV slots, and sends it to the frontend once, then clears both the
pending map and `_alg2_adoption_requested`. With the placeholder discarded the
frontend would receive exactly one copy, which is the intended behaviour.

## 6. Verdict

Per the stated criterion — *if any reachable path does not guarantee engine
payload cleanup, do not apply the discard* — **P2 and P4 are reachable and do
not guarantee it. STOP.**

The plain "discard the placeholder" fix is not safe as it stands. It would turn
today's duplicate into tomorrow's silent request loss on exactly the paths that
are hardest to observe: a stash-relay failure, and three experiment arms that
run Algorithm 2 without V6 KV.

## 7. What to design first (not implemented)

The audit points at a cleaner invariant than conditioning the discard:

> Releasing `_alg2_pending_adoption` is part of *every* deactivation, not of the
> eviction policy. Exactly one component returns the request, and it is the one
> holding the payload.

Concretely, the release of pending adoptions would move out of
`_evict_all_waiting_requests` into an unconditional step of
`handle_deactivate_request` that runs before **both** early returns, so P0, P2
and P4 all return the payload exactly once. Only then does discarding the
scheduler placeholder become unconditionally safe, and the duplicate at the
origin disappears without a duplicate guard anywhere.

That change touches the deactivation control flow, including an error path that
currently keeps the model active (P2), so it needs its own ownership design and
its own tests before anything is written:

- P2 with a pending adoption: the model stays active after the failed stash --
  decide whether the payload is returned to the frontend or retained *and*
  re-requested by clearing `_alg2_adoption_requested`, and prove no double
  delivery either way.
- P4 with a decode-retracted request and V6 KV off.
- P0 reached after P2 or P4.
- P1 and P3 unchanged: exactly one frontend copy, placeholder discarded.
- An assertion that no rid ever appears twice in the frontend queue for one
  deactivation.

## 8. State

- Pipeline STOPPED. Runtime unchanged at `e3aa0ad`. Attempts 1-3 preserved.
- Stage 01 c_i untouched.
- No placeholder discard, duplicate guard, timeout, or overwrite defence added.
