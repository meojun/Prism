# Migration rollback / lifecycle correction

Scope: correctness and lifecycle only. No Algorithm 1 or Algorithm 2 semantics
change, no τ/window/cooldown change, no placement-policy change, no performance
work, no structural change aimed at the stash root cause. τ calibration Stage B
remains stopped; `TAU_CANDIDATES_FROZEN.json`, the seed 3/4 traces and the
selection rule are untouched.

```
MIGRATION_ROLLBACK_FIX = PARTIAL — NOT DECLARED FIX VERIFIED
```

The success criterion set for this task requires a targeted failure-path test
that **forces a stash failure** and then demonstrates retained source service
capability, physical/logical state agreement, and no unexpected WorkerPool
cleanup. That test has not been run, and the reason is a genuine design blocker
described in §3. Declaring FIX VERIFIED now would be wrong.

What *is* implemented and verified turns the observed fatal, silent, unbounded
stall into a **bounded, explicit, detected failure**.

---

## 1. Corrected understanding of the defect

The Phase-7c forensic identified the source WorkerPool teardown as the damage.
Reading the code changes the attribution:

**The rollback path is already correct.** On a failed stash,
`scheduler.py:2097-2113` sets `self._activated = True`, returns
`DeactivateReqOutput(success=False)` and **never calls
`deactivate_model_runner()`**. The engine keeps the model. On the scheduler side
`gpu_scheduler.py:_handle_deactivate_result` sets the model state back to
`"activated"` and returns, releasing nothing — its docstring says so explicitly,
and the log confirms it fired (`deactivation_rolled_back`, 11:06:56.209).

The damage came from somewhere else. `GPU_Scheduler_0` **left its scheduling
loop** 88 ms later and fell off the end of `run_gpu_scheduler_process`, so the
object was garbage-collected and `__del__ → cleanup()` deleted its IPC
endpoints. There is no shutdown marker, no `Receiver error`, no traceback, and
`kill_parent_process()` did not run — so no exception escaped, which means
`_shutdown_event` was set.

The only setters are `shutdown()` and **three Algorithm-2 order-violation
tripwires** (`gpu_scheduler.py:625, 658, 697`), each of which also raises. None
of their log signatures appears.

> **Which of them fired cannot be determined from the preserved logs. This is
> recorded as INCONCLUSIVE and was not guessed at.**

This matters for the fix design: *the rollback cannot restore a WorkerPool whose
owning process has already left its loop.* Requirement 2 as written is not
achievable by patching the rollback path.

---

## 2. What was implemented

One runtime file, `multi_model/request_handler_worker_pool.py`
(`control_path_guards.patch`, **52 added / 1 removed**). Runtime hash moves
`2b5430c1b04b21e9` → `846355fb5bb99d67`.

### 2.1 Bounded control path (requirement 3)

`_send_req_and_wait_for_response` awaited `state.event.wait()` with **no
timeout**. That single line is where the controller sat for 601.8 s. It is now
`asyncio.wait_for(..., timeout=CONTROL_REQUEST_TIMEOUT_S)`; on expiry it logs
`[LIFECYCLE-GUARD] control_request_timeout` and returns `(False, None)`.

`(False, None)` is not a new contract — it is exactly what the existing
stash-failure path already returns, so callers roll the migration back through
code that already exists. Default bound 120 s (`PRISM_CONTROL_REQUEST_TIMEOUT_S`),
against observed legitimate round trips of 0.6–1.3 s.

### 2.2 Liveness precondition (requirement 4)

A GPU scheduler that leaves its loop removes its own IPC file — in the stalled
run, `request_handler_to_gpu_scheduler_0` at 11:06:56.298. A ZMQ `PUSH` socket
accepts sends to a departed peer silently, which is why the request vanished.
The endpoint path is now retained per GPU and checked before issuing any control
request; if absent, the request is refused immediately with
`[LIFECYCLE-GUARD] control_target_not_alive`.

It **fails open**: an unknown `gpu_id` or a missing registry is treated as alive,
so it can only refuse a request it is certain about.

### 2.3 Run-validity gate (requirement 5)

`exp/scripts/lifecycle_validity_gate.py` — offline, no runtime semantics.
Detects, per run:

1. runtime `WorkerPool GPU n cleanup completed` with no shutdown signal
2. controller cycle gap > 30 s
3. a control request left unacknowledged > 30 s (sends and acks matched FIFO per
   action+GPU, so concurrent requests to different GPUs and the final in-flight
   request of a run are not false-flagged)
4. either lifecycle guard firing

---

## 3. What is blocked, and why

**Requirement 1 (fail-safe ordering)** — the engine already refuses to release
the source until the stash is confirmed (§1). The ordering defect is not at the
engine. No change made.

**Requirement 2 (rollback invariant)** — **BLOCKED.** The invariant "source GPU
scheduler alive, WorkerPool alive, serving capability restored" cannot be
established from the rollback path, because in this incident the scheduler had
already exited for a reason outside the rollback. Restoring it would require
either:

- changing what the Algorithm-2 order-violation tripwires do (kill the GPU
  scheduler → log and continue), which **changes Algorithm-2 semantics** and is
  forbidden by this task; or
- determining why `_shutdown_event` was set, which the preserved logs do not
  support and which instrumentation is forbidden.

This is a decision for the project owner, not one to make silently. Options in
§6.

**Tests A (forced stash failure), B (post-failure invariants), C (E2E
reproduction), D (τ=0.00035 regression)** — not run. C and D are ~10 min per run
and would demonstrate only that the stall did not recur at a 2.2 % base rate;
the task itself states that E2E passes are not sufficient to declare the fix
verified. Running hours of E2E before the §3 design question is resolved would
not change the verdict.

---

## 4. Verification performed

### 4.1 Targeted control-path tests — 6/6 PASS

`exp/analysis/migration_rollback_fix/test_control_path.py`, exercising the real
methods on an instance built without `__init__`:

| test | asserts |
|---|---|
| endpoint exists → alive | liveness true |
| endpoint removed → not alive | liveness false |
| unknown gpu / no registry | **fails open** (never refuses on uncertainty) |
| **deactivate to a torn-down scheduler** | returns `(False, None)`, **nothing sent**, no waiter leaked |
| **no reply ever arrives** | returns `(False, None)` in ~0.2 s not indefinitely, request genuinely sent, waiter cleaned up |
| normal reply | still returns `(True, out)` — the healthy path is unbroken |

### 4.2 Validity gate — 1/1 sensitivity, 44/44 specificity

Against the preserved stalled run:

```
FAIL …/seed_3.stall-attempt1
  - runtime WorkerPool cleanup on GPU(s) ['0'] with no shutdown signal
  - controller cycle gap 601.8s between cycle 7 and 8
  - deactivate request to GPU 0 at 11:07:25.910 was never acknowledged (600.7s unanswered)
```

It independently rediscovers the exact request the forensic identified by hand.

Against **all 44 valid Prism runs** in the project (4-HET paired, cooldown
diagnostic, estimator correction, both window-calibration arms): **44/44 PASS**,
zero false positives. Two earlier drafts of check 3 produced 3 and 5 false
positives respectively — a bare send/ack count difference, then a strictly
sequential pairing that mis-handled two legitimate concurrent sends 1 ms apart.
Both were corrected before the result above.

---

## 5. Before / after on the observed incident

| | attempt 1, pre-fix | with the guards in place |
|---|---|---|
| deactivate to departed GPU-0 scheduler | accepted by the PUSH socket, silently lost | **refused before send**, `control_target_not_alive` |
| controller placement loop | blocked **601.8 s** | bounded; returns `(False, None)` |
| migration outcome | indefinite hang | explicit failure → existing rollback |
| requests lost | 1596 aborted | request loss bounded by the timeout |
| detection | none; harness mislabelled it `INVALID_CLIENT_FD_EXHAUSTION` | validity gate names the exact unacked request |
| GPU 0 still dead afterwards | yes | **yes — unchanged (requirement 2 blocked)** |

The last row is the honest limit of this patch. It stops the stall and makes the
degradation visible; it does not yet restore the lost GPU.

---

## 6. Recommended decision

The remaining work needs a ruling on the Algorithm-2 tripwire, which is out of
this task's authority:

- **(a)** Treat an Alg2 order violation as fatal to the *run* rather than to the
  *GPU scheduler* — set a validity flag and stop the run cleanly, instead of
  silently leaving one GPU dead while the controller believes it is live. This
  arguably preserves the tripwire's intent better than the current behaviour.
- **(b)** Keep the tripwire as is and rely on the validity gate to invalidate any
  run in which it fires. No semantics change; degraded runs are caught after the
  fact rather than prevented.
- **(c)** Instrument the three tripwire sites to log which one fired, run the
  reproduction until it recurs (~2.2 % per run), then decide with evidence.
  Requires lifting the instrumentation ban.

Until one is chosen, `MIGRATION_ROLLBACK_FIX` stays **PARTIAL** and Stage B
stays stopped.

---

## 7. Status

```
ESTIMATOR_FIX_STATUS   = FIX VERIFIED
WINDOW_STATUS          = WINDOW_60_ADOPTED_FOR_PAPER_FIDELITY
MIGRATION_ROLLBACK_FIX = PARTIAL (control path bounded and guarded; rollback
                         invariant blocked on an Alg2-tripwire decision)
TAU_STATUS             = NOT STARTED (Stage B halted)
E2E_IMPROVEMENT_STATUS = NOT VERIFIED
FINAL_BASELINE_STATUS  = NOT VERIFIED
```

Preserved: `seed_3.stall-attempt1` (INVALID), `seed_3` (PASS),
`PROGRESS.attempt1.jsonl`, `TAU_CANDIDATES_FROZEN.json`, seed 3/4 traces,
selection rule.

---

> **Correction (failure-containment phase).** Any attribution in this document
> of the defect to the migration **rollback path** is **withdrawn**. Both
> source-restoration paths — the engine's stash fallback and
> `_handle_deactivate_result` — are correct and fired correctly in the incident.
> The real defect is **failure containment**: after an unexpected GPU scheduler
> shutdown, nothing reconciled the controller's view with physical state.
> `ROLLBACK_PATH_BUG = DISPROVEN`; the shutdown trigger itself remains
> `INCONCLUSIVE`. See `P4HET_MIGRATION_LIFECYCLE_FAILURE_CONTAINMENT_REPORT.md`.
