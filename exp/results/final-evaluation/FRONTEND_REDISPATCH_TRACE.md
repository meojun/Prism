# The frontend redispatch path is not where the 1,204 were lost

Trace only. **No code changed.** Runtime `205e0e9`.

## 1. Two corrections to my own earlier readings

Both were premature and are withdrawn:

- I reported GPU 0's scheduler as **stuck at `committing`** for model_3. Wrong: the
  `model_states` snapshot I read is printed *when an activate request is
  received* (10:13:21.864), before the ACK. The ACK did arrive —
  `10:13:25.124 Model model_3 (instance 0) activation succeeded on GPU 0` — and
  the engine then fetched continuously from seq 2994 through 3033+. model_3 on
  GPU 0 was `activated` and serving.
- Earlier I read the missing `Scheduler activated` line as a stalled engine.
  That line exists only in the `full` branch; the commit branch returns before
  it.

## 2. What the instrumentation settled

| question | answer | evidence |
|---|---|---|
| b1 — loop ran, token never matched? | **no** | 252 `staged_not_promoted` records, all momentary: `staged_head − shared_token` is 1 (202×), 2 (44×), 3 (5×), 0 (1×). The engine fetches one or two ahead of the token and clears as soon as the predecessor completes. No persistent mismatch. |
| b2 — loop stopped? | **no** | all 12 engines beat until 10:18:0x–10:18:12, seconds before teardown at 10:18:45 |
| Algorithm 2 ledger | **clean** | GPU0 3,185/3,185 admitted, GPU1 5,178/5,175, **STALE 0 on both** |
| engines at the end | **alive and empty** | every heartbeat reports `waiting/running/staged = 0/0/0` |

The 3113–3115 stall did not recur, and neither hypothesis explains this run.

## 3. The frontend path, measured

Everything that was ever handed back to the frontend, counted from the logs:

| source | requests returned |
|---|---|
| scheduler queue → frontend (`Sending N queued requests`) | **336** |
| engine waiting queue → frontend (`Evicted N requests`) | **0** |
| pending adoption → frontend (`delivered_to_frontend`) | 2 |
| backend drain → frontend (`returned_to_frontend`) | 2 |
| **total** | **~340** |
| **requests the client never got a response for** | **1,204** |

**Roughly 340 of 1,204.** The majority were never returned to the frontend at
all, so they cannot have been dropped on the way back from it.

And what *was* returned was picked up again: each stranded model was
re-activated after its last frontend return —

| model | last frontend return | next activation |
|---|---|---|
| model_3 | 10:13:21 | **10:13:25** |
| model_4 | 10:06:39 | **10:06:42** |
| model_6 | never returned | 10:04:35 |

So redispatch after eviction worked. **The frontend redispatch path is not the
drop point.**

## 4. Nor were they lost at deactivation

Every final deactivation found its engine already empty:

```
10:13:46  model_6 GPU1  waiting queue 0, running batch 0, preempt False
10:14:13  model_4 GPU1  waiting queue 0, running batch 0
```

The engines drained themselves; nothing was holding these requests when their
models were released.

## 5. `no measured load` is not a demand signal

`kvpr_global_v4.py:133` sets that reason when `peak_now <= 0` — the peak KVPR
across GPUs, computed from measured request *rate*. The arrival burst had long
since ended, so the rate was zero and no migration was warranted. It says
nothing about pending work and is not implicated.

Related, and checked: `FinishReq` is emitted only where the client's response
generator sees `finished` (`request_handler_worker_pool.py:554`). A request
evicted or migrated therefore produces no `FinishReq`, so the controller's
tracker does **not** silently drop it on eviction.

## 6. What I cannot yet name, and will not guess

The requests were not in an engine, not in the Algorithm 2 ledger, and mostly
never in the frontend queue. The engines regard them as done; the client never
received a response; and this run's client had **zero** descriptor failures and
exactly one `Server disconnected`, itself teardown fallout.

That points at the response-delivery path — engine `stream_output` → detokenizer
→ tokenizer manager → HTTP stream — which **the instrumentation I added does not
cover**. It observes the fetch, the token and engine liveness, which is what it
was asked to settle, and it settled it.

I am not naming a transition on this evidence. I have made two premature calls
in this investigation already; a third would be worth less than the admission
that the decisive record does not exist yet. What would produce it: a per-rid
record at `stream_output` and at the point the response generator completes or
is abandoned, so a request the engine finished can be matched against a response
the client received.

**STOP.** No fix, no retry, no timeout, no heuristic proposed.

## 7. State

Pipeline STOPPED. Runtime `205e0e9`, untouched. Valid calibration inputs remain
`tau_0p00035/seed_0` and `tau_0p00035/seed_42`.
