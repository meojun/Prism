# Prism reproduction — final baseline and regime analysis

Target paper: *Prism: Cost-Efficient Multi-LLM Serving via GPU Memory
Ballooning*, OSDI 2026 camera-ready.

```
ESTIMATOR_FIX_STATUS   = FIX VERIFIED
WINDOW_STATUS          = WINDOW_60_ADOPTED_FOR_PAPER_FIDELITY
MIGRATION_LIFECYCLE_FAILURE_CONTAINMENT_FIX = FIX VERIFIED
TAU_STATUS             = FINALIZED  (0.012859417696566448)
FINAL_BASELINE_STATUS  = VERIFIED
E2E_IMPROVEMENT_STATUS = REGIME-DEPENDENT (see section 15)
```

---

## 1. Executive summary

A corrected, paper-faithful Prism implementation was frozen and evaluated in two
deliberately different regimes on the same hardware, with the same runtime, the
same τ, and byte-identical paired traces.

| regime | Prototype | Prism | Δ | conditions Prism wins |
|---|---:|---:|---:|---:|
| **Prism-favorable many-model** (6 models, high consolidation, shifting hot set) | 2.2295 | 2.4048 | **+7.9 %** | **7 / 10** |
| **Final 4-HET hold-out** (4 models, low consolidation, all active) | 4.7686 | 3.5937 | **−24.6 %** | **0 / 20** |

The same frozen configuration wins by 7.9 % in one regime and loses by 24.6 % in
the other. **Prism's advantage is a property of the workload regime, not of the
implementation.**

Within the favourable regime the advantage is further bounded by load: +31.6 % at
r8, but −3.6 % at r2 and −14.8 % at r10.

Three correctness defects were found and fixed during the reproduction, and one
was found and deliberately left unfixed. None of them changes the conclusion
above: the corrected implementation is more accurate and more stable, and a more
stable planner pursues the same memory-only objective more consistently.

---

## 2. Reproduction goal

Reproduce Prism's paper-specified mechanisms faithfully enough that a comparison
against the released prototype measures the *algorithm*, not an implementation
artefact — then evaluate where that algorithm helps.

## 3. Paper-faithful mechanisms implemented

- **Algorithm 1** — KVPR global placement. `KVPR_i = Σ w_token_rate / shared_kv_i`,
  `w_token_rate = token_rate × token_size / SLO`, models placed in descending
  weighted rate, destination minimising resulting KVPR, migration gated by τ.
- **Algorithm 2** — Moore-Hodgson local arbitration.
- **token_rate** — §4: input tokens of newly admitted requests **plus** decode
  tokens of running requests, per unit time.
- **Sliding-window moving average** — Appendix A.4.
- **TPOT SLO weighting**, `shared_kv = C − Σ resident model weights`,
  elastic KV via kvcached, model eviction threshold ≈ 45 s (A.4).

## 4. Implementation-added correctness infrastructure

Kept strictly separate from paper mechanisms. None of it is in the paper; all of
it exists to make the measurement trustworthy:

- structured GPU-scheduler shutdown reasons
- fail-closed propagation of unexpected scheduler termination
- bounded control-path timeout
- scheduler-endpoint liveness precondition
- offline lifecycle validity gate
- per-run Algorithm-2 interaction gate, trace-hash and runtime-hash gates

## 5. Correctness and fidelity issues found

| # | issue | status |
|---|---|---|
| 1 | `token_rate`'s decode component was a cached short-interval achieved-throughput scalar, not a windowed rate — only the input half obeyed the sliding window | **FIXED** |
| 2 | monitoring window was 30 s, believed unspecified; Appendix A.4 specifies a sliding-window moving average and reports ~60 s | **CORRECTED** (documentation error: our design doc read A.4 as absent because the arXiv version has no A.4) |
| 3 | unexpected GPU-scheduler exit silently removed one GPU while the controller believed it live; control-path wait was unbounded (601.8 s observed) | **FIXED** (containment) |
| 4 | `nccl_port` check-then-use race at engine startup → EADDRINUSE, server never starts | **FOUND, NOT FIXED** — fixing it after the baseline freeze would be a runtime change mid-experiment |
| 5 | τ = 0.00035 had no calibrated standing under the corrected estimator | **RECALIBRATED** |

Issue 4 occurred in 2 of ~110 runs (~2 %), each retried once under the
harness-only retry rule with nothing changed and the failed attempt preserved.

## 6. Migration lifecycle failure and containment

The most instructive defect. A KV-stash acknowledgement failure triggered a
rollback; the rollback logic itself was **correct** on both sides, but the GPU-0
scheduler had already left its loop and was garbage-collected, deleting its IPC
endpoints. The controller, unaware, later sent a deactivate to a scheduler that
no longer existed and blocked for **601.8 s**; 1596 requests were lost.

```
ROLLBACK_PATH_BUG           = DISPROVEN
GPU_SCHEDULER_UNEXPECTED_SHUTDOWN = PROVEN
SHUTDOWN_TRIGGER_ROOT_CAUSE = INCONCLUSIVE
UNBOUNDED_CONTROL_WAIT      = PROVEN
SILENT_CAPACITY_DEGRADATION_RISK = PROVEN
```

The trigger that set `_shutdown_event` could not be determined from the preserved
logs and was **not guessed at**. The fix therefore fails closed on the
unexplained case too: any non-normal scheduler stop now ends the run explicitly.
Verified by 16/16 targeted tests, a 113/113 specificity audit, and 2/2 E2E
regressions. `FIX VERIFIED` here means the *containment* is verified — the
original trigger is not claimed to be eliminated.

## 7. Token-rate estimator fidelity correction

Paper §4 counts both input and decode tokens per unit time; A.4 puts both under
one sliding-window moving average. The implementation applied the window to the
input half only. After correction: plan changes/min −51.7 %, median plan lifetime
+96.0 %, reversals −23.1 %, ping-pong −23.1 %, migrations −17.1 %.

Crucially, the correction **did not** improve end-to-end performance uniformly —
it made the planner accurate, which let Algorithm 1 reach its own optimum more
often. Where that optimum is compute-hostile, stability made things worse.

## 8. 30 s vs 60 s window study

16 paired runs. A.4's stabilisation claim **did not reproduce**: rank-1 switches
did not fall (median 0.559 → 0.698/min) and median plan lifetime roughly halved
(26.5 → 13.9 s). Effects were regime-split — steady improved, bursty degraded
(goodput −0.80 median, 0/4 improved). 60 s was adopted anyway, **for paper
fidelity**, and the responsiveness cost is reported as a finding rather than
tuned away.

## 9. τ calibration

48 runs on calibration seeds 3/4, selected by a pre-declared mechanistic rule;
goodput used for neither selection nor tie-breaking.
`FINAL_TAU = 0.012859417696566448` (pooled positive-Δr P50). The historical
0.00035 was **too permissive** — beaten on both regret and cost. The
regret/cost trade-off proved non-monotone: below the selected point, migrating
less produced *less* residual regret, because the extra migrations were churn.

## 10. Final frozen environment

| | |
|---|---|
| RUN_CODE_COMMIT | `413f9ee44aa7563afd7570f06ca74100b738dad8` |
| RUNTIME_SOURCE_TREE_HASH | `7fbd431c6a636df0c72bb6a40324f851d002d204` |
| runtime base commit | `595ec1f170e75a43897a7a2ad58ac5a9820aa2e8` |
| FINAL_TAU / window / cooldown | 0.012859417696566448 / 60 s / 30 s |
| hardware | 2 × A100-SXM4-80GB, NVLink NV4 |
| driver / CUDA / Python / PyTorch | 580.173.02 / 12.1 / 3.10.21 / 2.4.0+cu121 |

## 11. Prism-favorable workload design

Six models, high consolidation, 90 % of demand on a moving hot pair rotating
A→B→C in 180 s phases, bursty arrivals from the canonical historical generator.
Hot pairs built from **static metadata only**, pre-declared, deliberately
separating the two largest models. 180 s was chosen so the 60 s estimator can
observe, Algorithm 1 can respond, a migration can complete, and its cost can be
amortised. This is an applicability **upper bound**, not a production
distribution.

## 12. Prism-favorable many-model results

+7.9 % overall, 7/10 conditions won, peaking at +31.6 % at r8; −3.6 % at r2 and
−14.8 % at r10. Mechanism: Prism loses TTFT in **10/10** conditions (−2.2 to
−8.7 pp, monotone in load) and buys TPOT (up to +10.2 pp). Joint-SLO is dominated
by TPOT — TTFT-only failures never exceed 2.4 % — so the trade nets positive
where TPOT improves and reverses at r10 where it does not.

TTFT p50 is unchanged (0.07–0.09 s both arms); TTFT p99 is 20–23× worse. The cost
is concentrated in a small number of requests blocked during migration.

## 13. Final 4-HET results

−24.6 % overall, **0/20 conditions won**; steady −21.8 %, bursty −27.8 %.
Here Prism loses TTFT *and* TPOT — TPOT by −19 to −30 pp. Bursty moves 80 GB per
run for that outcome; the Prototype migrates zero times.

## 14. Memory-optimal vs compute-optimal placement

The unifying explanation, established before the final runs by exhaustive offline
enumeration over all valid placements:

- Large-large co-residency (Llama-3.1-8B + Qwen2.5-7B) is the **globally
  KVPR-optimal** placement in 24.3 % of 4-HET cycles — up to 50.3 % in some
  conditions. It is not a greedy artefact: at every critical cycle it was rank 1
  of 14 valid placements, with the best separated alternative 2.0–25.4 % worse.
- Whether it is optimal is decided almost entirely by **which model tops the
  weighted-demand ranking**: small-model rank-1 → 57.6 % chance large-large is
  optimal; large-model rank-1 → 0.7 %. An 82× separation.
- KVPR has **no compute-contention term**, so pairing the two compute-heaviest
  models is invisible to the objective.
- The Prototype's static 2+2 placement happens to separate them in all 10 steady
  4-HET conditions (0.0 % large-large, 0 migrations). Part of its advantage is an
  **accidental compute-friendly pairing** that Algorithm 1 gives up to reduce
  peak KVPR.

This is the specified algorithm operating correctly on a regime its objective
does not serve. It is **not** relabelled as a correctness bug.

## 15. Regime comparison

| dimension | Prism-favorable | Final 4-HET |
|---|---|---|
| models | 6 | 4 |
| consolidation | high | low |
| active set | shifting hot pair, 90/10 | all models active |
| framing | deliberate best case | unbiased hold-out |
| **overall Δ** | **+7.9 %** | **−24.6 %** |
| **conditions won** | **7/10** | **0/20** |

The regimes differ by ~32 percentage points. The design assumptions Prism states
— many models, consolidation, idle memory to reclaim, demand that shifts — are
exactly the axes on which these two workloads differ, and the result tracks them.

**E2E_IMPROVEMENT_STATUS = REGIME-DEPENDENT.** There is no single answer to "is
corrected Prism faster than the prototype".

## 16. Mechanism → resource → latency → SLO

```
shifting hot set  →  KV pressure moves  →  windowed demand estimate shifts
                  →  rank-1 changes     →  Algorithm 1 re-plans
                  →  migration (~5/trace, ~30 GB)
                  →  KV headroom rebalanced
                  →  TPOT improves (p50 and p95), TTFT tail worsens
                  →  Joint-SLO net positive  IF TPOT is binding AND load
                     leaves headroom to amortise the migration
```

Every arrow was measured. The conditional in the last line is where 4-HET and the
r2/r10 ends of many-model fail.

## 17. Hypothesis verdicts

| scope | hypothesis | verdict |
|---|---|---|
| many-model | H1 resource advantage | SUPPORTED |
| many-model | H2 performance translation | PARTIALLY SUPPORTED |
| many-model | H3 load dependence | SUPPORTED, non-monotone |
| many-model | H4 active-set adaptation | SUPPORTED |
| τ | H1 τ controls migration cost | SUPPORTED |
| τ | H2 regret/cost trade-off | PARTIALLY SUPPORTED |
| τ | H3 low τ causes churn | SUPPORTED |
| τ | H4 high τ collapses adaptation | SUPPORTED |
| τ | H5 τ effect regime-dependent | SUPPORTED |
| τ | H6 goodput optimum matches mechanistic τ | DESCRIPTIVE ONLY |

## 18. Applicability boundary

Corrected Prism helps when **all** of these hold:

1. enough models and consolidation that memory ballooning has something to reclaim
2. demand that actually shifts, so re-placement has value
3. phases long enough to amortise ~30 GB of migration
4. TPOT, not TTFT, is the binding SLO
5. load high enough to create KV pressure, low enough to leave migration headroom

Drop any one and the advantage disappears. 4-HET fails 1, 2 and 5; many-model r2
fails 5 from below; r10 fails 5 from above.

## 19. Limitations

- Two GPUs; two seeds per condition — no population-level significance is claimed
- Synthetic workloads; the favourable regime is bursty-only, so cross-regime
  comparison uses 4-HET's bursty half
- τ, window and estimator were calibrated on seeds 3/4 and evaluated on 5/6 and
  7/8, but all seeds come from one generator
- The `nccl_port` race remains unfixed by design
- The shutdown trigger behind the lifecycle stall remains INCONCLUSIVE
- Differences from the authors' internal environment are unknown

## 20. Final conclusion

The reproduction is faithful on every mechanism that could be verified against
the camera-ready, its correctness infrastructure is verified, and its baseline
was frozen before any hold-out data was touched.

Under those conditions, **corrected Prism does not universally outperform the
released prototype.** It wins by 7.9 % in a regime built to suit it and loses by
24.6 % in an untouched hold-out where its memory-only objective actively selects
a compute-hostile placement.

The most useful result of this reproduction is not a number but a boundary:
**Algorithm 1 optimises memory pressure, and memory-optimal is not
compute-optimal.** Where those coincide, Prism delivers. Where they diverge — and
in a low-consolidation, all-active, heterogeneous deployment they diverge
systematically — pursuing the memory objective more accurately makes the outcome
worse, not better.

Adding a compute-interference term to placement would be a research contribution
and a deviation from the paper. It is **not** implemented here, and the
paper-faithful baseline is left reporting this boundary rather than repairing it.
