# What `ActivateReqOutput(success=True)` means

Contract study only. **No code changed.** Runtime `88f54f9`.

## 1. Correction to the previous autopsy

`COMMIT_ACTIVATION_STALL_DIAGNOSIS.md` concluded that model_1's engine "never
finished activating" because it never logged `Scheduler activated`, and that the
scheduler declared success "1 ms after dispatching the commit". **Both
inferences were wrong**, and the conclusion built on them does not stand:

- `Scheduler activated` is logged at `scheduler.py:1922`, inside the `else`
  branch of `handle_activate_request` — the **full**-activation path only. The
  `commit` branch returns at line 1830, before it. Its absence after a commit is
  normal and says nothing about readiness.
- The success line at 08:39:03.732 is the engine's own ACK arriving, not the
  scheduler self-reporting. The engine emits it *after* doing the commit work.

The observation that stands is narrower: worker 1's engine logged nothing after
`target_commit`, and GPU 0 admitted nothing thereafter.

## 2. Phase-by-phase contract

| phase | engine does | `_activated` set? | who emits `ActivateReqOutput(success=True)` | emitted before or after readiness | scheduler state on the ACK |
|-------|-------------|-------------------|---------------------------------------------|-----------------------------------|----------------------------|
| `prepare` | loads weights, sets `_v6_prepared_model`, logs `target_ready` | **no** | the engine, `scheduler.py:1930` | after the prepare work | `prepared` |
| `commit` | `_restore_waiting_requests()` → `_v6_inject_migrated_kv()` → `_activated = True` → logs `target_commit` | **yes** | the engine, `scheduler.py:1821` | **after** — the ACK is constructed below `_activated = True` in the same block | `activated` |
| `full` | `_restore_waiting_requests()` → `_v6_inject_migrated_kv()` → `_activated = True` → logs `Scheduler activated` | **yes** | the engine, `scheduler.py:1930` | after | `activated` |

`worker_pool.handle_activate_model` returning `True` for a commit is **not** the
ACK. It only reports that the message was dispatched to the worker's IPC socket;
the real verdict travels back over Redis from the engine. Two separate things
that both read as "success" in the logs.

State transitions on the scheduler side:

| event | state set |
|-------|-----------|
| `ActivateReqInput(prepare)` received | `preparing` |
| `ActivateReqInput(commit)` received | `committing` |
| `ActivateReqInput(full)` received | `activating` |
| `ActivateReqOutput(success, phase=prepare)` | `prepared` |
| `ActivateReqOutput(success, phase=commit\|full)` | `activated` |
| `ActivateReqOutput(success=False)` | **unchanged** |

Where requests may flow:

| gate | admits |
|------|--------|
| frontend intake (`_recv_from_frontend_queue`) | `activating`, `activated` only |
| admission (`request_queue.py:206`) | everything **except** `deactivating`, `deactivated` — so `preparing`, `prepared`, `committing` and `activating` all pass |

That admission gate is permissive, and worth noting: it would allow a dispatch
to a model in `committing`. **It is not what happened here.**

## 3. The actual ordering in the failure

```
08:39:03.728  ActivateReqInput(commit) received      -> state = committing
08:39:03.731  engine: target_inject, target_commit,  _activated = True
08:39:03.732  ActivateReqOutput(success=True)        -> state = activated
08:39:03.740  dispatch seq 3113  model_1#388         <- 8 ms AFTER the ACK
08:39:03.741  dispatch seq 3114, 3115
```

The dispatches follow the readiness ACK. The scheduler did not jump the gun.

## 4. The question, answered

> Does `ActivateReqOutput(success=True)` arriving guarantee the model is ready to
> fetch and prefill requests?

**YES.**

For `commit` and `full` alike the ACK is constructed and sent by the engine only
after `_restore_waiting_requests()`, `_v6_inject_migrated_kv()` and
`self._activated = True` have all completed, in the same straight-line block with
no intervening await or dispatch. A `prepare` ACK carries `phase="prepare"` and
moves the model to `prepared`, not `activated`, so it cannot be mistaken for
readiness.

## 5. Consequence

This is **not** an accounting-contract bug. The scheduler transitioned to
`activated` on a genuine readiness ACK and dispatched 8 ms later. So the fault is
**engine-side**: `model_1` on GPU 0 worker 1 declared itself activated and then
did not serve — it fetched none of 3113–3115 and emitted no further output for
five minutes, while GPU 0's other workers logged normally.

Per the instruction, the next step is to determine whether that engine was
**blocked or dead**, which the logs alone cannot settle. What is worth
establishing first:

- an idle activated engine in this codebase logs nothing on its own, so silence
  is not by itself proof of a stall — the distinguishing evidence is that it
  never fetched work that was waiting for it;
- whether the requests actually reached `backend:model_1` in Redis, or were held
  by the shared Algorithm 2 admission token and never offered to the engine at
  all. If the latter, the engine was ready and idle, and the defect is in the
  token, not the engine.

That second possibility was not checked before writing the earlier autopsy and
would change the classification again. No fix is proposed until it is.

## 6. State

Pipeline STOPPED. Runtime `88f54f9`, untouched. No timeout, retry or heuristic
added. Valid calibration inputs remain `tau_0p00035/seed_0` and `seed_42`.
