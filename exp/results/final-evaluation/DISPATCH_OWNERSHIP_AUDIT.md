# Dispatch ownership protocol: the invariant, and where it breaks

Audit only. **No code changed.** Runtime `16fa9b2`. Pipeline STOPPED.

## 1. The invariant

An issued `alg2_seq` must be in exactly one state at every instant:

```
scheduler-issued → backend-enqueued → engine-fetched/staged → admitted/running
                                                            → completed/retired
```

Never zero owners, never two.

## 2. The single structural fault

**`alg2_seq` is per-GPU. The backend queue is per-MODEL.**

| | key | scope |
|---|---|---|
| sequence ledger | `_mh_outstanding_prefills`, `_mh_next_backend_admit_seq` | **per GPU scheduler** |
| admission token | `…:alg2_next_admission:{gpu_id}` | **per GPU** |
| backend queue | `{backend_generate_request_key_prefix}:{model_name}` | **per MODEL, shared by every GPU** |

Producer and consumers of that shared queue — statically, all of them:

| # | site | role | reports the removal to |
|---|---|---|---|
| P | `gpu_scheduler.py:876` `_send_to_backend_queue` | enqueue, and record seq N outstanding on GPU g | — |
| C1 | `scheduler.py:648` `recv_generation_requests` | engine fetch → staged | nothing (correct: it will admit) |
| C2 | `scheduler.py:2209` `_drain_backend_queue` | drain → frontend | `engine_to_gpu_scheduler:{self.gpu_id}` |
| C3 | `gpu_scheduler.py:795` `_handle_deactivate_result` | `pop_all` → frontend | nothing |

C2 and C3 remove entries **by model**, so they take whatever any GPU put there.
Both report — or fail to report — against the *draining* side, never against the
GPU whose ledger issued the sequence.

**The transition where the owner disappears:**

> `_send_to_backend_queue` enqueues into `backend:<model>` and records seq N as
> outstanding on GPU g. Any drain of that key by C2 or C3 removes the entry.
> C3 reports nothing at all; C2 reports `migrated_away` to
> `engine_to_gpu_scheduler:{self.gpu_id}`, which is the draining GPU's ledger.
> Neither path can retire seq N on GPU g. Owner count for N goes from 1 to 0,
> GPU g's frontier stops on N, and because the model never left GPU g there is
> no departure event that could ever clean it up.

## 3. Both incidents are this, to the millisecond

**seq 2060 — today**

```
14:39:05.679  GPU1 model_1 deactivation succeeded
14:39:05.690  GPU0 model_1 activation succeeded (commit)
14:39:05.702  GPU0 dispatch seq 2060 model_1#166  → backend:model_1
14:39:05.714  GPU1 Release worker 3 from model_1  ← C3 pop_all(backend:model_1)
```

12 ms. `model_1#166` appears exactly twice in the whole system: the client's
arrival line and GPU0's dispatch. **No engine ever fetched it** — zero
`_alg2_obs_fetch` records for it. Live Redis at the stall confirmed the state
directly: only two keys existed, `alg2_next_admission:0 = 2060` and
`:1 = 4306`, and **no backend queue key at all**. Meanwhile GPU0's engines held
seq 2061 (model_1) and 2062 (model_4) staged, each waiting for a token that can
never reach them.

**seqs 1159/1160 — the 3113–3115 incident, same violation**

```
01:22:58.369  GPU1 model_4 activation succeeded
01:22:58.376  GPU0 dispatch seq 1159 model_4#140  → backend:model_4
01:22:58.377  GPU0 dispatch seq 1160 model_4#141  → backend:model_4
01:22:58.377  GPU0 receives deactivate for model_4  ← the drain
01:22:58.379  GPU0 "Sending 65 queued requests of model model_4 back to frontend"
```

1 ms. Both requests were re-dispatched later on GPU 1 as seqs 2110/2111 and
served — which is why nothing was ever *lost*, only the source ledger.

The two differ only in which GPU drained: in the older event the same GPU that
had just dispatched, in today's the other one. **Same invariant violation.**

## 4. Why the four previous fixes could not have prevented this

Every one of them keys off a *departure*:

| fix | trigger |
|---|---|
| dispatch-seq ownership sweep | model departs this GPU |
| staged request return | model departs this GPU |
| deactivation rollback | this GPU's deactivation |
| stale staged cleanup | this GPU's deactivation |

In both incidents the issuing GPU's model **did not depart** — model_1 stayed
resident on GPU 0, model_4 had just been activated on GPU 1. There is no
departure on the issuing side, so no sweep fires. This is why patching branch by
branch kept moving the failure instead of ending it: each patch closed one
departure path while the shared-queue race stayed open.

## 5. Enumerated: everything that can remove seq 2060 from the queue

1. **C1 engine fetch** — would have produced an `_alg2_obs_fetch` record. There
   is none for 2060 on GPU 0. Excluded by evidence.
2. **C2 `_drain_backend_queue`** — every drain in the window logs
   `backend_queue_drained drained=0`. Excluded by evidence.
3. **C3 `_handle_deactivate_result` `pop_all`** — logs nothing per request. The
   only path with no evidence trail, and the only one whose timing brackets the
   dispatch (05.702 inside 05.679→05.714). **Consistent with all evidence.**
4. Enqueue never happened — excluded: `_send_to_backend_queue` logs the dispatch
   and calls `send_pyobj` in the same loop iteration with no condition, no
   `continue` and no token check between them, and no exception was logged.
5. Redis eviction/expiry — excluded: no TTL is set on these keys.

Path 3 is the only one that survives. It is not proved by a log line, because
C3 emits none — which is itself part of the finding.

## 6. Proposed instrumentation — design only, NOT implemented

One ledger, written by every producer and consumer of `backend:<model>`, so the
next occurrence is decided by a record rather than by elimination:

```
[PAPER-SEQ-LEDGER] {event, time, seq, rid, model,
                    issuing_gpu,        # who owns the sequence
                    actor_gpu, actor,   # who is touching it now
                    queue_len_before, queue_len_after}
```

emitted at exactly five points:

| event | site |
|---|---|
| `enqueue` | `_send_to_backend_queue`, after `send_pyobj`, with the queue length before and after |
| `fetch` | engine `recv_generation_requests`, per request removed |
| `drain` | engine `_drain_backend_queue`, per request removed |
| `pop_all` | `_handle_deactivate_result`, **per request removed** — the gap today |
| `retire` | wherever a sequence is retired, naming the ledger it was retired from |

The decisive field is `issuing_gpu` vs `actor_gpu`: whenever they differ, a GPU
has removed a sequence it does not own, and that single comparison answers in
one line what took two incidents and this audit to establish.

It is observation only and adds no behaviour. **Not implemented.**

## 7. State

- Stalled run stopped through its recorded process group; `KILL_AUDIT.jsonl`
  records the signal, and `SERVER_DEATH.json` / `STALL_SNAPSHOT.json` hold the
  live Redis token values and process state. Preserved as `seed_42.b1stall1`.
- Runtime `16fa9b2`, untouched. No calibration re-run.
- Valid inputs: `tau_0p00035/seed_0`, `tau_0p00035/seed_42`, `tau_0p07/seed_0`.
