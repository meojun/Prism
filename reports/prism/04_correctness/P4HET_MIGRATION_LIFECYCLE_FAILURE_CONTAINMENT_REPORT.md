# Migration-lifecycle failure containment

Option (a) adopted: an Algorithm-2 order violation or any unexpected GPU
scheduler shutdown must terminate the **run**, explicitly and fail-closed, rather
than silently removing one GPU while the controller believes it is live.

**Algorithm 2's scheduling semantics are unchanged.** The three tripwires still
stop their scheduler and raise exactly as before; what changed is that the stop
is now *named* and *propagated*. τ, window, cooldown, Algorithm 1, placement
policy and SLO are untouched. τ calibration Stage B remains stopped.

```
ROLLBACK_PATH_BUG                            = DISPROVEN
SHUTDOWN_TRIGGER_ROOT_CAUSE                  = INCONCLUSIVE
CONTROL_PATH_TIMEOUT_FIX                     = FIX VERIFIED
SCHEDULER_LIVENESS_GUARD                     = FIX VERIFIED
FAIL_CLOSED_SHUTDOWN_PROPAGATION             = FIX VERIFIED
MIGRATION_LIFECYCLE_FAILURE_CONTAINMENT_FIX  = FIX VERIFIED
```

> **This does not mean the underlying shutdown trigger is fixed.** Whatever set
> `_shutdown_event` on GPU 0 can still happen. What is fixed is that it can no
> longer produce a silently degraded run: it now ends the run explicitly, with
> the reason and GPU id recorded.

---

## 1. Corrected causal attribution

The earlier `MIGRATION_ROLLBACK_BUG` claim is **withdrawn**.

**The rollback path's source-restoration logic is correct**, on both sides:

- `scheduler.py` — on a failed stash the engine sets `self._activated = True`,
  returns `DeactivateReqOutput(success=False)`, and **returns before**
  `deactivate_model_runner()`. The model runner is never released.
- `gpu_scheduler.py:_handle_deactivate_result` — on `success=False` it restores
  the model state to `"activated"` and returns, releasing nothing: not the
  queue, not the backend queue, not the worker slot.

Both fired correctly in the incident (`deactivation_rolled_back`, 11:06:56.209).

**The actual defect is failure containment.** `GPU_Scheduler_0` left its
scheduling loop 88 ms later, fell off the end of `run_gpu_scheduler_process`, and
was garbage-collected — `__del__ → cleanup()` removed its IPC endpoints. Nothing
told the controller. The controller kept planning for a GPU that no longer
existed, and 29 s later sent it a deactivate that could never be answered.

**Which setter fired remains INCONCLUSIVE.** The candidates are `shutdown()` and
the three Algorithm-2 tripwires; none of their log signatures appears, no
exception escaped (`kill_parent_process()` did not run), and no shutdown signal
was received. This was not guessed at, and the containment fix does not depend on
knowing it — it deliberately fails closed on the *unexplained* case too.

---

## 2. Changes

Two runtime files, **107 lines added / 6 removed**. Runtime hash
`2b5430c1b04b21e9` → **`49f47ebd7c75aecd`**.

### A. Explicit shutdown reason — `gpu_scheduler.py` (+55/−5)

`_shutdown_reason` plus a single recorder:

```python
def _request_shutdown(self, reason: str, **detail):
    if self._shutdown_reason is None:      # first reason wins
        self._shutdown_reason = reason
        logger.error("[LIFECYCLE-SHUTDOWN] " + json.dumps({...}))
    self._shutdown_event.set()
```

Every production setter now routes through it:

| site | reason |
|---|---|
| `shutdown()` (incl. SIGTERM/SIGINT handler) | `normal_shutdown` |
| tripwire, backend admission order | `alg2_backend_admit_order_violation` |
| tripwire, prefill order | `alg2_prefill_order_violation` |
| tripwire, prefill completion | `alg2_prefill_completion_violation` |
| receiver thread dying | `receiver_thread_error` |
| scheduler process exception | `scheduler_process_exception` |

First-reason-wins matters: a tripwire that fires and then triggers the ordinary
teardown must still be reported as the tripwire, not as a normal shutdown.

Logging only. No scheduling behaviour is altered at any of these sites.

### B. Fail-closed propagation — `gpu_scheduler.py`

After `run_scheduling_loop()` returns, the reason is inspected. Anything other
than `normal_shutdown` — **including `None`, the "loop returned with no reason"
case, which is precisely the observed incident** — logs
`run_fatal_unexpected_scheduler_shutdown` and calls `kill_parent_process()`.

Tripwire → **run-fatal**. Never tripwire → log-and-continue. Never degraded
serving on the surviving GPU.

### C. Preserved from the previous patch — `request_handler_worker_pool.py` (+52/−1)

Bounded control request (`asyncio.wait_for`, default 120 s, env-overridable) and
the scheduler-endpoint liveness precondition, both returning `(False, None)` —
the contract the existing stash-failure path already used.

---

## 3. Targeted tests — 16/16 PASS

Deterministic, no GPU; each forces one failure path and asserts the containment
property.

### `test_failure_containment.py` — 10/10

| test | asserts |
|---|---|
| normal shutdown labelled | reason `normal_shutdown` |
| each Alg2 tripwire | its own distinct reason recorded |
| **first reason wins** | tripwire + later normal teardown ⇒ still the tripwire |
| receiver error | classified as unexpected |
| **Alg2 tripwire ⇒ run-fatal** | `kill_parent_process()` called — not a lone-GPU stop |
| receiver error ⇒ run-fatal | `kill_parent_process()` called |
| **reasonless loop return ⇒ run-fatal** | the observed incident is contained |
| process exception ⇒ run-fatal | `kill_parent_process()` called |
| **normal shutdown ⇒ NOT fatal** | regression: ordinary teardown not escalated |
| stash fallback | failure branch re-activates, reports `success=False`, and returns **before** releasing the runner |

### `test_control_path.py` — 6/6

| test | asserts |
|---|---|
| endpoint present / removed / unknown | liveness true / false / **fails open** |
| **deactivate to a torn-down scheduler** | `(False, None)`, **nothing sent**, no waiter leaked |
| **reply never arrives** | returns in ~0.2 s not indefinitely; waiter cleaned up |
| normal reply | still `(True, out)` — healthy path unbroken |

---

## 4. Validity gate and previous-run audit

`exp/scripts/lifecycle_validity_gate.py` detects, per run: runtime WorkerPool
cleanup without a shutdown signal; controller cycle gap > 30 s; a control request
unacknowledged > 30 s (matched FIFO per action+GPU, so concurrent sends and the
final in-flight request are not false-flagged); unexpected scheduler shutdown
reasons; and either lifecycle guard firing.

**Sensitivity** — on the preserved stall it independently rediscovers what the
forensic found by hand:

```
FAIL …/seed_3.stall-attempt1
  - runtime WorkerPool cleanup on GPU(s) ['0'] with no shutdown signal
  - controller cycle gap 601.8s between cycle 7 and 8
  - deactivate request to GPU 0 at 11:07:25.910 was never acknowledged (600.7s unanswered)
```

**Specificity / previous-run audit (H)** — all valid Prism runs in the project:

| phase | runs | result |
|---|---:|---|
| 4-HET paired (historical baseline) | 20 | PASS |
| cooldown diagnostic | 4 | PASS |
| estimator correction | 4 | PASS |
| window calibration w30 | 8 | PASS |
| window calibration w60 | 8 | PASS |
| τ calibration T0 attempt 2 | 1 | PASS |
| **total** | **45** | **45/45 PASS** |

**No additional contaminated runs were found.** No unexpected scheduler shutdown
or runtime WorkerPool cleanup exists anywhere outside the one preserved stall.
**The historical and window-calibration results are preservable and remain
valid.**

Two earlier drafts of the unacked-request check produced 3 and 5 false positives
(a bare send/ack count difference, then a sequential pairing that mishandled two
legitimate concurrent sends 1 ms apart). Both were corrected before this result.

---

## 5. Minimal E2E regression (G)

Run only after all 16 targeted tests passed, on runtime `49f47ebd7c75aecd`.
Used solely as regression confirmation — **not** as evidence for any FIX VERIFIED
verdict.

| arm | τ | rc | verdict | completed | aborted | Alg2 viol | staged | migr | time |
|---|---|---|---|---|---|---|---|---|---|
| T0 | 0.0 | 0 | PASS | 3406/3406 | 0 | 0 | 0 | 4 | 594 s |
| T1 | 0.00035 | 0 | PASS | 3406/3406 | 0 | 0 | 0 | 2 | 594 s |

Lifecycle gate: **2/2 PASS**. Guards fired **0** times, `[LIFECYCLE-SHUTDOWN]`
records **0**, runtime WorkerPool cleanups **0** — the healthy path is untouched
by the new code.

---

## 6. Status taxonomy

| verdict | basis |
|---|---|
| **ROLLBACK_PATH_BUG = DISPROVEN** | Both source-restoration paths verified correct in code and confirmed firing in the incident log. Earlier claim withdrawn. |
| **SHUTDOWN_TRIGGER_ROOT_CAUSE = INCONCLUSIVE** | No setter's signature appears; no exception escaped; no shutdown signal. Not guessed at. |
| **CONTROL_PATH_TIMEOUT_FIX = FIX VERIFIED** | Returns `(False, None)` in ~0.2 s against an unanswered request, waiter cleaned up; normal reply still succeeds. |
| **SCHEDULER_LIVENESS_GUARD = FIX VERIFIED** | Refuses before sending to a removed endpoint, no waiter leaked, fails open on uncertainty. |
| **FAIL_CLOSED_SHUTDOWN_PROPAGATION = FIX VERIFIED** | All four unexpected-stop paths, including the reasonless one, call `kill_parent_process()`; normal shutdown is not escalated. |
| **MIGRATION_LIFECYCLE_FAILURE_CONTAINMENT_FIX = FIX VERIFIED** | All 16 targeted tests pass; gate 45/45 specificity, 1/1 sensitivity; E2E regression clean. |

**Explicitly:** the containment fix does **not** fix the underlying shutdown
trigger. A GPU scheduler can still stop unexpectedly. It can no longer do so
silently.

---

## 7. Incident during this work

The first regression launch reused the default output path and began overwriting
the preserved attempt-2 artifact. It was killed within ~90 s. Attempt 2 survived
intact — server logs still at their original 11:37:50 timestamps,
`VERIFICATION.json` reading PASS 3405/3406 with 6 migrations, matching
`PROGRESS.jsonl`, and passing the lifecycle gate. It is now explicitly preserved
as `seed_3.pass-attempt2`, and the runner takes a `PRISM_OUT_DIR` override
(default unchanged) so regressions write elsewhere.

---

## 8. Status and preservation

```
ESTIMATOR_FIX_STATUS   = FIX VERIFIED
WINDOW_STATUS          = WINDOW_60_ADOPTED_FOR_PAPER_FIDELITY
MIGRATION_LIFECYCLE_FAILURE_CONTAINMENT_FIX = FIX VERIFIED
TAU_STATUS             = NOT STARTED (Stage B halted, not auto-resumed)
E2E_IMPROVEMENT_STATUS = NOT VERIFIED
FINAL_BASELINE_STATUS  = NOT VERIFIED
```

Unchanged and preserved: `TAU_CANDIDATES_FROZEN.json`, the Stage A Δr quantiles,
the seed 3/4 calibration traces, the selection rule, `seed_3.stall-attempt1`
(INVALID), `seed_3.pass-attempt2` (PASS), `PROGRESS.attempt1.jsonl`.

**Note for Stage B:** the runtime hash has moved to `49f47ebd7c75aecd`. The two
regression runs are on the new runtime; the eight T1 runs previously verified as
reusable are on `2b5430c1b04b21e9`. Whether T1 may still be reused as a
calibration arm across that boundary is a decision for the Stage B restart, not
one taken here.

---

## 9. Frozen runtime identity

The serving runtime lives in `prism-research/`, a separate checkout that this
repository gitignores. Its identity is a base commit plus the complete
working-tree delta, as in every previous freeze in this project.

| | |
|---|---|
| base commit | `595ec1f170e75a43897a7a2ad58ac5a9820aa2e8` |
| worktree patch sha256 | `49f47ebd7c75aecdac2d67efea1b3d121b9e599693df514cfd71bfe10724881d` |
| **RUNTIME_SOURCE_TREE_HASH** | `7fbd431c6a636df0c72bb6a40324f851d002d204` |
| previous (estimator-corrected) | `2b5430c1b04b21e9623e1d8271c7edf806eee186a434248dd7586535fb6e3a41` |

`RUNTIME_SOURCE_TREE_HASH` is a real `git write-tree` hash of the live runtime,
computed through a throwaway index so the checkout's own index is untouched.
The freeze, its README and a rebuild recipe are committed under
`patches/lifecycle_containment/`.

### Source diff summary

Two files, **107 lines added / 6 removed**:

| file | change |
|---|---|
| `multi_model/request_handler_worker_pool.py` | bounded control-path wait (`asyncio.wait_for`), scheduler-endpoint liveness precondition, per-GPU endpoint registry |
| `multi_model/scheduling/gpu/gpu_scheduler.py` | `_shutdown_reason` + `_request_shutdown()` recorder on all four setters, fail-closed propagation at the process boundary |

Algorithm 1 and Algorithm 2 semantics, τ, window, cooldown, placement policy and
SLO definitions are unchanged. The three Algorithm-2 tripwires still stop their
scheduler and raise exactly as before; only the reason is recorded and the stop
is now propagated as run-fatal.

Machine-readable: `exp/analysis/correctness_fix/fix_verdicts.json`,
`regression_summary.csv`, `historical_lifecycle_audit.csv`.
