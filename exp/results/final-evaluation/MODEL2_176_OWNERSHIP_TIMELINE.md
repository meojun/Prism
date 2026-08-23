# `model_2#176` — ownership timeline, and the verdict

Run: `cal-0p00035-s0` attempt 3, runtime `e3aa0ad`.
No code was changed to produce this document.

Legend for each row: **owner** = the component that authoritatively holds the
request; **FE** = a copy exists in the frontend queue; **ADOPT** = migration /
adoption pending; **ENG** = engine waiting / staged / running; **SCH** =
scheduler queue / outstanding ledger.

## 1. Timeline

| # | time | transition | authoritative owner | FE | ADOPT | ENG | SCH |
|---|------|-----------|--------------------|----|-------|-----|-----|
| 1 | 02:07:15.337 | GPU1 dispatch seq **1885** | GPU1 scheduler | no | no | no | queue→outstanding{1885} |
| 2 | 02:07:15.400 | GPU1 backend_admit 1885 | GPU1 engine | no | no | staged→waiting | outstanding{1885} ACKed |
| 3 | 02:07:15.404 | GPU1 prefill_start 1885 | GPU1 engine | no | no | running | outstanding{1885} |
| 4 | 02:07:15.435 | GPU1 prefill_complete 1885 | GPU1 engine (decoding) | no | no | running | ledger entry deleted |
| 5 | 02:07:27.011 | GPU1 **kv-stash**, 117 rids incl. #176 | GPU1 stash (migration) | no | **yes** | removed from waiting | `retired:[]`, `dequeued:[]` |
| 6 | 02:07:27→38 | capsule sent GPU1 → GPU0 | migration transport | no | yes | no | no |
| 7 | 02:07:38.408 | GPU0 injection **fails** — `Boolean value of Tensor with more than one value is ambiguous` → `build_recomputed_request` → `_alg2_hold_for_reentry` | **GPU0 engine `_alg2_pending_adoption`** | no | **yes** | held out of waiting, by design | no |
| 8 | 02:07:38.408 | GPU0 `adoption_requested` rids=[#176] | GPU0 engine | no | yes | held | `_alg2_adoption_requested` |
| 9 | 02:07:38.409 | GPU0 `adopt_resumed` → placeholder `GenerateReqInput(alg2_resumed=True)` queued | **split: engine holds payload, scheduler holds proxy** | no | yes | held | **queued, never dispatched** |
| 10 | 02:07:38 → 02:08:28 | 50 s idle; GPU0 dispatches #176 **0 times** | both halves live | no | yes | held | queued |
| 11 | **02:08:28.728** | GPU0 **scheduler** `pop_model_requests(model_2)` → `_send_waiting_reqs_to_frontend_queue`, "Sending 8 queued requests of model model_2 back to frontend queue" | frontend | **copy 1** (placeholder, `alg2_resumed=True`) | yes | held | queue emptied |
| 12 | **02:08:28.739** | GPU0 **engine** `_evict_all_waiting_requests`: `_alg2_pending_adoption` → waiting_queue → frontend; reports `evicted-to-frontend [#176,#177]` | frontend | **copy 2** (real request, `alg2_resumed=False`) | cleared | cleared | `dequeued_before_dispatch: []` — already popped at .728 |
| 13 | 02:08:39.215 | GPU1 dispatch seq **2372** | GPU1 scheduler | one copy left | no | no | outstanding{#176→2372} |
| 14 | 02:08:39.239 | GPU1 dispatch seq **2373** (same rid) | GPU1 scheduler | consumed | no | no | outstanding{#176→**2373**} — 2372 overwritten |
| 15 | 02:08:39.308 | backend_admit ACK `alg2_seqs:[2372]`, record says 2373 → `order_ok:false` | — | — | — | — | fail-closed, `_shutdown_event` set |
| 16 | 02:08:39.325 | `adoption grant for an unknown request: model_2#176` | — | — | — | engine holds nothing | the `alg2_resumed=True` copy |

Both rids in the eviction report behaved identically: `model_2#176` and
`model_2#177` were each dispatched **3** times on GPU 1 in total (once legitimately
at 1885/1890, then twice more at 2372/2373 and 2374/2375).

## 2. The 02:08:28 question, answered

**Was migration / adoption ownership still alive when GPU 0 deactivated? Yes —
both halves of it, and neither was stale.**

- The engine half: `_alg2_pending_adoption["model_2#176"]` was created at
  02:07:38.408 and popped only by `_evict_all_waiting_requests` at 02:08:28.739.
  It held the real rebuilt request — payload, `origin_input_ids`, sampling
  params — deliberately kept out of the waiting queue so the target's
  Algorithm 2 would schedule it rather than a batch picking it up unaccounted.
- The scheduler half: the `alg2_resumed=True` placeholder was queued at
  02:07:38.409 and popped only at 02:08:28.728. It was never dispatched, so it
  never became a sequence, and `dequeued_before_dispatch: []` in the engine's
  report 11 ms later is the direct evidence that the scheduler had just taken it.

So this is **not** a stale adoption copy resurfacing after ownership had ended.
The adoption was live, mid-flight, and 50 seconds old when the deactivation
arrived.

## 3. What actually duplicated the request

Two independent recovery paths each converted their own half of one live
adoption into a frontend request, 11 ms apart:

```
02:08:28.728  gpu_scheduler.py DeactivateReqInput branch
                waiting_reqs = self.queue.pop_model_requests(model_name)
                self._send_waiting_reqs_to_frontend_queue(...)      -> copy 1
02:08:28.739  scheduler.py _evict_all_waiting_requests
                _alg2_pending_adoption -> waiting_queue -> frontend -> copy 2
```

Neither knows about the other. For an ordinary request the scheduler's path is
correct — the queued object *is* the frontend request, so returning it is exact.
For an adoption placeholder it is not: the placeholder is a scheduling proxy for
a request the engine holds, and returning it to the frontend manufactures a
second, payload-less request carrying `alg2_resumed=True`.

That flag is the fingerprint. `_convert_req_to_frontend_reqs` never sets
`alg2_resumed`, so the engine's copy cannot produce an adoption grant. The
"adoption grant for an unknown request" at 02:08:39.325 therefore identifies the
phantom precisely: it is the placeholder copy, dispatched on GPU 1 as if it were
a resumed request, against an engine that had never held it.

## 4. Verdict

Per the stated criterion — migration ownership was alive and the request was
*also* evicted to the frontend — this is branch **(b)**: a fix at the point of
origin, not a duplicate guard, timeout, or seq-overwrite defence downstream.

But the origin is narrower than "exclude migration-owned requests from frontend
eviction". The engine's eviction is the **correct** one: it returns the
authoritative request with its payload. The defect is that the **scheduler
returns the adoption placeholder as though it were a real frontend request**.

Invariant to state before implementing:

> An adoption placeholder is a scheduling proxy for a request the engine holds.
> It is never an independently deliverable request: it may be dispatched, or
> discarded when its model leaves, but it must never be returned to the
> frontend, because the engine returns the request it stands for.

**One condition must be verified before writing that fix**, and it is the reason
no code is being changed now: dropping the placeholder is only safe if the
engine's eviction is guaranteed to run on the same deactivation. In
`scheduler.py` `_evict_all_waiting_requests` is reached conditionally — at
:1993 under `recv_req.evict_waiting_requests or (preempt and RECOMPUTE)`, and at
:2055 under `self._v6_kv_enabled()`. If a deactivation can take neither branch
while `_alg2_pending_adoption` is non-empty, discarding the placeholder would
lose the request outright. That path audit is the next step, before any edit.

## 5. State

- Pipeline STOPPED, `STOP` marker present, runtime unchanged at `e3aa0ad`.
- Attempts 1-3 preserved. Stage 01 c_i untouched.
- No duplicate guard, timeout, or overwrite-prevention added.
