# Migration-lifecycle stall forensic — T0 (τ=0) steady_r8_s3 attempt 1

Offline only, from the preserved artifact. No GPU run, no runtime code change, no
instrumentation added, no τ/window/cooldown change. Both the stalled attempt 1
and the passing attempt 2 are preserved.

```
VERDICT = LATENT_LIFECYCLE_BUG_SUPPORTED
```

---

## 1. Executive summary

The stall is **not** a τ=0 artefact and **not** a lost downstream response. It is
a **state-inconsistency bug in the migration rollback path**, exposed by a KV
stash acknowledgement failure.

The chain, fully evidenced in the logs:

1. Migration 1 activates `model_4` on the destination (GPU 1), then deactivates
   the source (GPU 0).
2. The source deactivation **tears down GPU 0's WorkerPool**
   (`WorkerPool GPU 0 cleanup completed`, 11:06:56.298).
3. The KV stash acknowledgement comes back `None`, so the implementation takes
   its defensive fallback: **`relay did not acknowledge stash; keeping source
   active model=model_4`** and rolls the migration back by deactivating the
   destination.
4. **The rollback restores the logical placement but not the torn-down source
   WorkerPool.** GPU 0 can no longer admit new work. It drains its in-flight
   requests and goes silent at 11:07:05.332. `GPU_Scheduler_0` never logs again.
5. τ=0 lets a second `model_4` migration fire at the cooldown boundary
   (t+30.1 s). Its destination activation succeeds, and the controller sends the
   source deactivate **to the GPU-0 scheduler that no longer exists**.
6. No acknowledgement can ever arrive. The controller's placement loop blocks
   for **601.8 s** with no timeout, until it gives up, rolls back the
   destination and resumes at cycle 8.

The τ value only determined *when the second migration fired*. The broken state
was created by migration 1 and would persist under any τ that permits migration.

---

## 2. Exact timeline of the second `model_4` migration

| time | component | event |
|---|---|---|
| 11:07:05.332 | GPU 0 Worker 0 | **last GPU-0 activity of the entire run** |
| 11:07:24.890 | GlobalController | cycle 7 decides MIGRATE model_4 0→1 (cooldown expired at t+30.1 s) |
| 11:07:24.893 | controller | `Sending activate request to GPU scheduler 1` |
| 11:07:24.896 | GPU 1 Worker 2 | `Processing activate request` rid `f1e4b575…` |
| 11:07:24.897 | GPU 1 Worker 2 | `[PAPER-KV-V6] stale-clear acknowledged` |
| 11:07:25.903 | GPU 1 Worker 2 | `Activate model model_4 … time cost: 1.0056s` / `Model runner activated` |
| 11:07:25.905 | worker pool | `[V5-HOP] {"action":"ActivateReqInput","gpu_id":1,"wait_for_engine_s":1.011}` — **ack generated and consumed** |
| **11:07:25.910** | controller | **`Sending deactivate request to GPU scheduler 0`** ← last confirmed step |
| — | GPU 0 | **nothing. No `Processing deactivate request`, ever.** |
| 11:17:26.015 | controller | `Sending deactivate request to GPU scheduler 1` (destination rollback, 600.1 s later) |
| 11:17:26.658 | GlobalController | cycle 8 — placement loop resumes |

**LAST CONFIRMED LIFECYCLE STAGE:** destination activation completed, its
acknowledgement generated *and consumed* by the controller
(`V5-HOP ActivateReqInput`, gpu_id=1, 11:07:25.905), and the source
deactivate request **successfully sent** to GPU scheduler 0 at 11:07:25.910.

**FIRST MISSING EXPECTED EVENT:** GPU 0's
`Processing deactivate request` for `model_4`, and its corresponding
`[V5-HOP] {"action":"DeactivateReqInput","gpu_id":0}` acknowledgement.
Neither appears. In migration 1 the same pair appeared **23 ms** after the send
(11:06:56.167 → 11:06:56.190).

---

## 3. Why the source could not respond

`GPU_Scheduler_0`'s final two lines in the entire run:

```
[11:06:56.298 GPU_Scheduler_0] Removed IPC file: gpu_scheduler_0_to_worker_0
[11:06:56.298 GPU_Scheduler_0] WorkerPool GPU 0 cleanup completed
```

That is **during migration 1**, 29 seconds before the deactivate of migration 2
was sent. Line counts during the stall window (11:08–11:16):

| component | lines |
|---|---:|
| `GPU_Scheduler_0` | **0** |
| `GPU_Scheduler_1` | 7715 |
| GPU=0 workers | **0** |
| GPU=1 workers | 3613 |
| `GlobalController` (queue tracking) | 4257 |

The controller **process** stayed alive throughout — only its placement loop was
blocked. GPU 1 kept serving `model_3` and `model_6` normally. GPU 0 was gone.
`model_4` and `model_5` accumulated 101 and 140 waiting requests respectively
with `num_running_reqs: 1`, which is the source of the 1596 aborted requests.

`GPU_Scheduler_1` shows **no cycle gap > 30 s** anywhere in the run: the
downstream path was healthy and responsive. The request simply had no recipient.

---

## 4. The trigger: KV stash acknowledgement failure in migration 1

All ten `[PAPER-KV-V6]` events in the run; the two that matter:

```
11:06:56.207 GPU=0 Worker 0 (model_4)  stash failed: invalid stash acknowledgement: None
11:06:56.208 GPU=0 Worker 0 (model_4)  relay did not acknowledge stash; keeping source active model=model_4
```

The fallback is deliberate and correct in intent — on a failed stash, keep the
source authoritative rather than lose KV state. It is followed at 11:06:56.213
by `Sending deactivate request to GPU scheduler 1`, i.e. the destination is
rolled back. The controller's logical view is consistent afterwards:
`current_placement` shows `model_4: 0` for every one of the 16 cycles.

**But the physical source was already dismantled.** The deactivate at
11:06:56.167 had run to completion (`request_drain`, `Retract running batch`,
`V5-HOP DeactivateReqInput gpu_id=0` at 11:06:56.209) and the WorkerPool cleanup
followed at 11:06:56.298. Nothing in the rollback re-creates it.

`model_4` on GPU 0 continued to *decode* until 11:07:05 — finishing the batch it
already held — which is why the failure is invisible for ~9 seconds.

**Why the stash acknowledgement was `None` cannot be determined from these logs.**
That trigger remains **INCONCLUSIVE** and would need instrumentation that this
task forbids.

---

## 5. Side-by-side with the passing attempt 2

Identical source hash, τ, window, cooldown and trace.

| | attempt 1 (STALL) | attempt 2 (PASS) |
|---|---|---|
| `stash failed` events | **1** | **0** |
| `keeping source active` | **1** | **0** |
| `WorkerPool GPU n cleanup completed` | **1** (GPU 0, 11:06:56.298) | **0** |
| `GPU_Scheduler_0` final line | cleanup, 11:06:56 | `[V5-LOOP]` at 11:37:50 (alive) |
| `GPU_Scheduler_0` lines, last 5 min | 0 | 5811 |
| migrations executed | 2 | 6 |
| controller cycles | 16 | 85 |
| max controller gap | **601.8 s** | none > 30 s |
| aborted | 1596 | 0 |

In attempt 2 every migration follows the clean pattern
`activate dest → deactivate source → activate` and both schedulers stay alive to
the end. The single discriminating event is the stash acknowledgement failure.

---

## 6. Verdict and classification

```
VERDICT = LATENT_LIFECYCLE_BUG_SUPPORTED
```

Ruled out by evidence:

- **DOWNSTREAM_RESPONSE_LOSS** — rejected. The downstream that *was* alive
  (`GPU_Scheduler_1`) answered every request promptly and never gapped. The
  GPU-0 response was not lost in transit; there was no process left to produce it.
- **CONTROLLER_CONSUMPTION_FAILURE** — rejected. The controller consumed the
  destination activation acknowledgement normally (`V5-HOP` logged at
  11:07:25.905) and proceeded to send the next request 5 ms later.

Two distinct defects, both latent and **neither specific to τ=0**:

1. **Rollback does not restore the source WorkerPool.** The `keeping source
   active` fallback repairs the logical placement but leaves the source GPU
   physically unable to admit work. The system is silently degraded from that
   moment: one of two GPUs is dead while the controller believes both are live.
2. **The source deactivate has no timeout or liveness guard.** A request to a
   torn-down scheduler blocks the controller's placement loop for ~600 s. Any
   τ permitting migration can reach this state; τ=0 only made the second
   migration fire 30 s later instead of never.

Severity: defect 1 alone silently halves capacity and would corrupt any
measurement taken after it, **without necessarily producing a stall**. That is
the more dangerous of the two, because it can pass a run's validity gates.

---

## 7. Fix proposal (NOT implemented)

No code was changed. Proposed, in priority order:

1. **Make the rollback symmetric.** When the stash fallback decides to keep the
   source active, it must re-establish the source WorkerPool that the
   deactivation tore down, or the deactivation must be deferred until the stash
   acknowledgement is confirmed. Preferred: **acknowledge-then-deactivate** —
   do not release the source until the destination confirms the stash, making
   the sequence fail-safe by construction.
2. **Bound every control-plane request.** Give `ActivateReqInput` /
   `DeactivateReqInput` a timeout with an explicit failure path, so a
   non-responding scheduler degrades the migration rather than blocking the
   placement loop for ten minutes.
3. **Add a source-liveness precondition.** Before emitting a migration, verify
   the source scheduler is alive; abort the migration and log a diagnostic
   otherwise.
4. **Promote the inconsistency to a run-validity failure.** A
   `WorkerPool GPU n cleanup completed` that is not part of shutdown should
   invalidate the run immediately, rather than letting it complete with one GPU
   silently dead.

All four are runtime-semantics changes and are **out of scope** for τ
calibration. They must be made, verified and re-frozen deliberately, not folded
into this phase.

---

## 8. Status

Stage B remains **STOPPED**. Per the branch rule, because the evidence strongly
supports a specific correctness bug, no τ candidate was added, removed or
altered — `TAU_CANDIDATES_FROZEN.json` is unchanged — and T2/T3/T4/T5 were not
resumed.

Preserved:
- `raw/prism-T0/steady/rate_8/seed_3.stall-attempt1` (INVALID, the stall)
- `raw/prism-T0/steady/rate_8/seed_3` (PASS, attempt 2)
- `PROGRESS.attempt1.jsonl`, `PROGRESS.jsonl`

Base rate: **1 stall in 46 Prism runs project-wide (2.2 %)**; no other run has
ever shown a controller gap above 30 s or a mid-run WorkerPool cleanup.

```
ESTIMATOR_FIX_STATUS   = FIX VERIFIED
WINDOW_STATUS          = WINDOW_60_ADOPTED_FOR_PAPER_FIDELITY
TAU_STATUS             = NOT STARTED (Stage B halted)
E2E_IMPROVEMENT_STATUS = NOT VERIFIED
FINAL_BASELINE_STATUS  = NOT VERIFIED
```

---

> **Correction (failure-containment phase).** Any attribution in this document
> of the defect to the migration **rollback path** is **withdrawn**. Both
> source-restoration paths — the engine's stash fallback and
> `_handle_deactivate_result` — are correct and fired correctly in the incident.
> The real defect is **failure containment**: after an unexpected GPU scheduler
> shutdown, nothing reconciled the controller's view with physical state.
> `ROLLBACK_PATH_BUG = DISPROVEN`; the shutdown trigger itself remains
> `INCONCLUSIVE`. See `P4HET_MIGRATION_LIFECYCLE_FAILURE_CONTAINMENT_REPORT.md`.
