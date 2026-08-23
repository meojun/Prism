# Classifying the P2 rollback repair before implementing it

Runtime is back at the frozen `e3aa0ad`, byte for byte: the working tree's patch
blob is `843815cf073d25fa94d15e4f6081a5fef5854a11`, identical to the one at
`HEAD`. An implementation had been started when the classification was asked
for; it was reverted rather than left half-applied.

**Result: one change in the first draft was NEW-POLICY. It is avoidable, and
removing it makes the whole repair pure state restoration.** The revised design
is in §3. Nothing is implemented.

## 1. Classification of the first draft

| # | change | paper-level invariant preserved | changes Alg1/2, τ, placement, priority? | changes the successful path? | adds behaviour beyond restoring the pre-failure state? | class |
|---|---|---|---|---|---|---|
| 1 | `worker_pool.handle_deactivate_model` no longer calls `release_worker`; the slot is released on the success verdict instead | a model's engine slot is not reassignable while the model may still own it | no | **yes, timing only** — the slot frees one engine round-trip later; no decision reads it in between, because activation of the next model already waits for the verdict | no | CORRECTNESS-PLUMBING |
| 2 | `gpu_scheduler` gains the missing `else`: on `success=False`, model state → `activated` | a rolled-back transition leaves the system in its pre-transition state | no | no — success branch untouched | no, it restores exactly the state set when the request went out | CORRECTNESS-PLUMBING |
| 3 | scheduler discards `alg2_resumed=True` placeholders instead of returning them to the frontend | one request, one authoritative owner; a proxy is not a request | no | **yes** — removes a delivery that fabricated a second copy; ordinary requests unaffected | no, it removes behaviour | CORRECTNESS-PLUMBING |
| 4 | engine releases `_alg2_pending_adoption` on P0 and P4, which previously released nothing | a request is never silently stranded without an owner | no — delivery uses the existing frontend path, and the receiving GPU's Algorithm 2 schedules it normally | P1/P3 unchanged in effect | no — it completes an ownership transfer that was already supposed to happen | CORRECTNESS-PLUMBING |
| 5 | on P2, clear `_alg2_adoption_requested` and call `_alg2_request_adoption(reason="stash-rollback")` | — | no | no | **yes** | **NEW-POLICY** |

### Why #5 is NEW-POLICY

`_alg2_request_adoption` has exactly one caller today, `_alg2_hold_for_reentry`:
a request is asked about once, when it is first held. Change #5 adds a second
trigger, on a new condition — "a deactivation rolled back" — which is a retry
rule. In aggregate it restores the pre-deactivation state, but only because
change #3 destroyed the placeholder first. Destroy-then-recreate is not
restoration; it is a policy that happens to land on the same state.

Per the stated rule, that is a STOP on the first draft.

## 2. The root of the problem

Change #3 discards placeholders at the moment the deactivate *request* is sent,
before the verdict is known. On a rollback that destroys part of the
pre-deactivation state, and #5 exists only to rebuild it. The scheduler is
making an irreversible decision before it knows whether the transition succeeded
— the same mistake as releasing the worker slot early, which change #1 already
fixes by deferring.

## 3. Revised design — pure restoration, no new policy

Apply the deferral consistently: **the scheduler changes nothing it cannot
un-change until the engine has ruled.**

| # | change | class |
|---|---|---|
| 1 | slot released on the success verdict, not at send | CORRECTNESS-PLUMBING |
| 2 | **queue pop and frontend return also deferred to the success verdict** | CORRECTNESS-PLUMBING |
| 3 | on the success verdict: pop the queue, return ordinary requests to the frontend, discard placeholders | CORRECTNESS-PLUMBING |
| 4 | on the failure verdict: state → `activated`. Nothing was popped, nothing was released | CORRECTNESS-PLUMBING |
| 5 | engine releases `_alg2_pending_adoption` on P0/P1/P3/P4; on P2 it simply does not release | CORRECTNESS-PLUMBING |

The re-request disappears. On P2 the pre-deactivation state is not rebuilt — it
was never taken apart:

- payload still in `_alg2_pending_adoption` (the engine never released it)
- rid still in `_alg2_adoption_requested` — **correct**, because the ask is still
  outstanding: its placeholder is still queued
- placeholder still in the scheduler's queue (never popped)
- worker slot still assigned (never released)
- model state back to `activated`

The existing Prism scheduler then decides again with no help: the model is
admissible once more, Algorithm 2 selects the placeholder in its own time, the
dispatch produces the adoption grant, and the engine promotes the payload it
still holds. Original `arrival_time` and `slo` are untouched throughout, so the
deadline Moore–Hodgson sees is the one the request always had.

`_alg2_request_adoption` keeps exactly one caller.

### What the revised design costs on the success path

Changes #1 and #2 both move work from send-time to verdict-time. Two timing
effects, no decision changes:

- the worker slot frees one engine round-trip later;
- queued requests return to the frontend one round-trip later — and they could
  not have been dispatched in that window anyway, because admission already
  refuses a model in state `deactivating` (`request_queue.py:206`).

Both need proving rather than asserting, and that is the first thing the tests
must establish: **that no same-GPU activation can be issued between the
deactivate request and its verdict.** If one can, change #1 has to become
"release on success, and on activation take the slot only if free" — still
plumbing, but a different shape.

## 4. Nothing here is PAPER

No change touches Algorithm 1, Algorithm 2, τ, `e_i = p_i/c_i`, the deadline or
arrival definition, the migration or activation policy, the KV allocator, the
workload, the SLOs, or admission ordering. The paper-level invariants these
changes serve are the plumbing ones the implementation already claims:

- one request has exactly one authoritative owner;
- a request is never silently dropped;
- a failed transition leaves the system as it was.

## 5. State

- Runtime reverted to `e3aa0ad` and verified identical to the frozen patch blob.
- The partially written test file was removed; the suite is green on the
  reverted tree.
- Pipeline STOPPED. Attempts 1-3 preserved. c_i untouched.
- Nothing implemented, pending confirmation of the revised design in §3.
