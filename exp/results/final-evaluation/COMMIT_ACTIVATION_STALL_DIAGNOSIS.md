# `cal-0p07-s0`: activation reported success while the engine never resumed

Classification: **A — ownership/lifecycle, on the activation side.**
**STOP. No fix applied.** Runtime unchanged at `88f54f9`.

## 1. Neither earlier fault recurred

| | |
|---|---|
| client descriptor exhaustion | **0** — the fd repair holds |
| staged requests discarded | **0** — the staged-return fix holds |
| GPU 1 stale sequences | **0** (4,956 dispatched, 4,954 admitted, 3 retired) |

The 225 `Server disconnected` are teardown fallout, not the cause: the first
one is at bench.log line 10,386 and the client had already been waiting since
line 8,237. The client process had its full limit (soft 65535, hard 524288).

## 2. What stalled

**GPU 0's admission frontier is pinned on three sequences**, and unlike every
previous case the model that owns them never left:

| GPU | dispatched | admitted | retired | **stale** |
|-----|-----------|----------|---------|-----------|
| 0 | 3,115 | 3,112 | 0 | **3** — 3113, 3114, 3115 |
| 1 | 4,956 | 4,954 | 3 | 0 |

```
08:38:37  GPU0 seq 3112  model_3#1006   dispatch → admit → prefill → complete
08:39:03  GPU0 seq 3113  model_1#388    dispatch — never admitted
08:39:03  GPU0 seq 3114  model_1#389    dispatch — never admitted
08:39:03  GPU0 seq 3115  model_1#390    dispatch — never admitted
```

`model_1` is still resident on GPU 0 at the end — `current_placement:
{"model_1": 0}`, `model_to_worker: {'model_1': 1}`. So the departure sweep
correctly never fired: there was no departure. The requests were dispatched to a
model the scheduler believed was serving, and it never fetched them. GPU 0
reported `admitted: 0` for the rest of the run.

## 3. The engine never finished activating

`model_1` was being brought up on GPU 0 by an overlap migration at that instant:

```
08:39:02.381  GPU=0 Worker 1 (model_1)  Model runner activated
08:39:02.382  GPU=0 Worker 1 (model_1)  [PAPER-OVERLAP-V6] target_ready
08:39:03.731  GPU=0 Worker 1 (model_1)  [PAPER-MIGRATION-TIMELINE] target_inject
08:39:03.731  GPU=0 Worker 1 (model_1)  [PAPER-OVERLAP-V6] target_commit
                                        ... and nothing, ever again
08:39:03.732  GPU_Scheduler_0           Model model_1 (instance 0) activation succeeded
08:39:03      GPU_Scheduler_0           dispatch 3113/3114/3115 to model_1
```

Every healthy activation in this same run ends with a `Scheduler activated` line
— `08:34:33 model_1 on worker 0, 2.20 s`; `08:36:24 model_6, 5.24 s`;
`08:37:23 model_3, 1.63 s`. **Worker 1's model_1 never logs it.** Its last line
is `target_commit`, and the engine produced no further output of any kind for
the remaining five minutes, while GPU 0's other workers went on logging normally
(model_3's deactivation on worker 3 completed at 08:39:16).

The scheduler recorded "activation succeeded" **1 ms after dispatching the
commit** — before the engine had done the work, and it never came back out.

Causal chain:

> An overlap migration committed `model_1` onto GPU 0; the GPU scheduler marked
> the activation succeeded on dispatching the commit rather than on the engine
> completing it, and the engine entered `target_inject`/`target_commit` and never
> emerged — it logged nothing further and never reached `Scheduler activated`.
> The scheduler, seeing an activated model, dispatched sequences 3113–3115 to it;
> nothing fetched them, and because `model_1` never departed there was no
> migrated-away report to retire them. GPU 0's frontier stayed on 3113, GPU 0
> admitted nothing for the rest of the run, 1,024 requests went unanswered, and
> the watchdog stopped the run at 8,001 of 8,227.

## 4. Why this is a distinct class

Every ownership defect fixed so far concerns a request **leaving** a GPU — a
sequence stranded by a departure, a payload dropped on the way out. This one is
the opposite: nothing left. The model arrived, was declared ready before it was,
and the sequences issued against that declaration have no departure event that
could ever retire them. The contiguous-prefix rule then does exactly what it
should and refuses to skip them.

Two things need separating before any fix, and the logs cannot settle it alone:

- **is the engine blocked, or dead?** No traceback, no `Killed`, no CUDA error
  appears; the process simply stops logging. Whether worker 1 is wedged inside
  KV injection or exited silently decides whether the fix belongs in the commit
  path or in liveness detection.
- **is `activation succeeded` supposed to mean the commit was dispatched, or
  that the engine is serving?** The commit branch of `handle_activate_model`
  returns True immediately after `send_pyobj`; the full-activation branch waits
  for the engine's `ActivateReqOutput`. If the commit phase is meant to be
  asynchronous, then dispatching work against it is the defect; if not, the
  verdict is being recorded too early.

**No fix is proposed until that is established.** Anything written now would be
guessing between a liveness bug and an accounting one.

## 5. State

- Pipeline STOPPED at stage 02. Runtime `88f54f9`. Artifacts preserved.
- Valid calibration inputs so far: `tau_0p00035/seed_0`, `tau_0p00035/seed_42`.
- `tau_0p07/seed_0` invalid on this attempt; earlier attempts preserved as
  `.invalid1` (fd exhaustion) and `.startupfail1` (server killed at startup).
- Phone alerts fired correctly for both the run failure and the stage failure.
