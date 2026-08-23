# GPU 0 seqs 3113–3115: where they stopped

Trace only. **No code changed.** Runtime `88f54f9`.

## 1. Timeline

| time | event | evidence |
|------|-------|----------|
| 08:38:37 | GPU0 seq **3112** `model_3#1006` dispatch → admit → prefill_start → prefill_complete | 4 `PAPER-ALG2-RUNTIME` events |
| 08:38:37 | prefill_start(3112) advances the shared token to **3113** | `compare_and_advance_int` at `scheduler.py:1055`, and no advance failure was raised (0 occurrences of "admission token advance failed") |
| 08:39:03.728 | `ActivateReqInput(commit)` for model_1 → state `committing` | gpu_scheduler log |
| 08:39:03.731 | engine: `target_inject`, `target_commit`, `_activated = True` | engine log |
| 08:39:03.732 | `ActivateReqOutput(success=True)` → state `activated` | gpu_scheduler log |
| 08:39:03.740 | **dispatch seq 3113** `model_1#388` | `PAPER-ALG2-RUNTIME` |
| 08:39:03.741 | **dispatch seq 3114** `model_1#389`, **3115** `model_1#390` | `PAPER-ALG2-RUNTIME` |
| — | backend admit / prefill_start for 3113–3115 | **never** |

## 2. Were they enqueued to `backend:model_1`? — **YES**

Proved from the code path, not inferred. `_send_to_backend_queue`
(`gpu_scheduler.py`) does, per request, in one loop iteration:

```python
self._mh_runtime_log("dispatch", BatchRunReq(...), record)   # the log line we have
self.redis_client.send_pyobj(
    key=f"{backend_generate_request_key_prefix}:{model_name}", obj=req)
```

There is no condition, no `continue` and no token check between the two. The
send is unconditional. Three `dispatch` events exist for 3113/3114/3115, so
three `send_pyobj` calls to `backend:model_1` were executed. No exception was
logged for that loop.

**The admission-token/scheduler branch is therefore excluded.** The token gates
what the *engine promotes*, never what the scheduler enqueues.

## 3. Did the engine consume them? — **NO**

The engine promotes a staged request only when its sequence equals the shared
token (`scheduler.py:650-656`), and the event loop logs every promotion:

```python
if self._activated:
    recv_generation_requests = self.recv_generation_requests()
    if len(recv_generation_requests) > 0:
        logger.info(f"Received {len(recv_generation_requests)} generation requests")
```

Counting those lines after the commit, by worker:

| worker | promotions after 08:39:03 |
|--------|---------------------------|
| GPU1 Worker 3 (model_4) | **508**, last at 08:39:46 |
| **GPU0 Worker 1 (model_1)** | **0** |

model_1 promoted nothing, ever, while GPU 1 went on serving for another 43
seconds. GPU 0 admitted nothing for the rest of the run.

## 4. Two hypotheses excluded

- **Queue-full starvation.** `max_fetch_count = max_prefill_count −
  len(waiting_queue) − len(staged)`, and the commit calls
  `_restore_waiting_requests()`, which could in principle fill the queue and
  drive that to zero. It cannot here: `waiting_queue_stash` appears exactly
  three times in the file — initialised at 342, drained at 2741–2742 — and is
  **never appended to**. `_restore_waiting_requests()` is a no-op in this build.
- **Scheduler failing to keep dispatching.** GPU 0's `admitted: 0` is correct
  behaviour, not a second fault: with 3 outstanding prefills and one active
  model, `dispatch_budget = max(0, pipeline_window − outstanding) = max(0, 1−3)
  = 0`. The scheduler stopped because the three were outstanding, which is the
  symptom, not the cause. Its loop stayed alive (467 iterations in the final
  window).

## 5. Classification

Per the stated scheme: **backend consumer / engine path.** The requests reached
`backend:model_1`; the engine never took them into a batch.

## 6. What is NOT proved, and must not be guessed

Within that branch two sub-cases remain, and **the artifacts cannot separate
them**:

- **(b1) the loop ran and the token did not match.** The engine would fetch
  3113–3115 into `_alg2_staged_generation_reqs` and promote none, silently.
  Staging is not logged, and the Redis instance is gone, so neither the staged
  list nor the token's actual value can be recovered.
- **(b2) the loop did not run.** No traceback, no `Killed`, no CUDA error, and
  no worker-death message appears; the engine simply stops logging after
  `target_commit`. But an activated idle engine logs nothing either, so silence
  does not distinguish the two.

The expected token value is 3113 by construction, since GPU 0's last
prefill_start was 3112 and no advance failure was raised. That is an inference
from the code path, **not** a reading of the token, and it is exactly the fact
that would settle b1 versus b2.

What would settle it, on the next occurrence: log the token value and the staged
list length alongside each fetch, and record engine liveness independently of
whether it has work. Neither exists today.

**STOP here.** No fix, no timeout, no retry, no heuristic.

## 7. State

Pipeline STOPPED. Runtime `88f54f9`, untouched. Valid calibration inputs remain
`tau_0p00035/seed_0` and `tau_0p00035/seed_42`.
