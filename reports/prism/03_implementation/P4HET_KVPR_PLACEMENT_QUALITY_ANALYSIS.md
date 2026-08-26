# 4-HET-wide analysis of KVPR-optimal placement quality

Offline only, existing artifacts. No GPU run, no Algorithm-1 / τ / window / SLO
change, no push. Artifacts: `exp/analysis/kvpr_placement_quality/`.

```
ESTIMATOR_FIX_STATUS   = FIX VERIFIED
E2E_IMPROVEMENT_STATUS = NOT VERIFIED
FINAL_BASELINE_STATUS  = NOT VERIFIED
```

---

## 1. Executive summary

**The pathology is not seed1-specific, and it is not steady-specific in its
occurrence — but it is steady-specific in its damage.**

Across all 20 historical Prism conditions, the large-large composition
(Llama-3.1-8B + Qwen2.5-7B on one GPU) is the **globally KVPR-optimal placement
in 24.3 % of replay-confirmed cycles**. The highest single rate is not a seed1
steady condition at all — it is **bursty seed 2 at 50.3 %**.

The mechanism is one variable, and it is quantified rather than narrated:

| rank-1 weighted-demand model | cycles | P(large-large is KVPR-optimal) |
|---|---|---|
| **small model rank 1** (Llama-3.2-3B or Qwen2.5-3B) | 635 | **57.6 %** |
| **large model rank 1** (Llama-3.1-8B or Qwen2.5-7B) | 856 | **0.7 %** |

An 82× separation. Algorithm 1 isolates the heaviest KV-memory consumer; when
that consumer is a *small* model, the two large models are left sharing a GPU,
and KVPR — having no compute-interference term — scores that as optimal.

The composition carries a real decode cost: within-run, Qwen2.5-7B TPOT p95 is
worse during co-residency in **17 of 21** runs (median ratio 1.204) and joint-SLO
attainment is lower in **15 of 21** (median −0.070). But the cost is
**regime-dependent**:

| regime | Qwen7B TPOT p95 ratio | attainment delta |
|---|---|---|
| steady | 1.193, worse in **10/11** | **−0.1015**, lower in **10/11** |
| bursty | 1.229, worse in 7/10 | **+0.0139**, lower in only 5/10 |

And the Prototype comparison is the cleanest evidence in this report:

> **In all 10 steady conditions the Prototype holds `{Llama-3.2-3B, Qwen2.5-7B} |
> {Qwen2.5-3B, Llama-3.1-8B}` for 100 % of snapshots — one large model per GPU,
> 0 migrations, 0.0 % large-large.** In bursty its large-large residency ranges
> 4.2 %–80.0 %.

The Prototype's steady advantage (goodput 5.03 vs 3.17, −36.9 % for Prism) and
its bursty parity (4.37 vs 4.28, −2 %) line up exactly with whether it happens to
separate the two large models. Its static initial assignment is
**accidentally compute-friendly in steady**, and Algorithm 1 gives that pairing
up in order to minimise memory pressure.

This is a **paper-faithful algorithmic limitation / applicability boundary**, not
a correctness bug.

---

## 2. Motivation

Phase 7b established that in two steady seed-1 conditions the large-large
placement was genuinely Alg1/KVPR-optimal. This analysis asks whether that was a
two-trace quirk or a property of the 4-model regime.

---

## 3. Scope and frozen data

4 models (Llama-3.2-3B, Qwen2.5-3B, Llama-3.1-8B, Qwen2.5-7B), A100-80GB ×2,
workloads bursty/steady × rates 2/4/6/8/10 × seeds 1/2. τ = 0.00035, window 30 s,
cooldown 30 s throughout. Three arms kept **strictly labelled and never pooled**:

- **A. Historical primary Prism** — 20 runs, **OLD** estimator
- **B. Estimator-corrected diagnostic Prism** — 4 runs, **NEW** estimator
- **C. Prototype** — 20 runs, static reference

---

## 4. Run inventory / integrity

```
TOTAL_EXPECTED_PRIMARY            = 40   (20 Prism + 20 Prototype)
TOTAL_FOUND_PRIMARY               = 40
TOTAL_USABLE_FOR_PLACEMENT_ANALYSIS = 40
   - 20 Prism   : [PAPER-ALG1-V4] cycle traces present in all (84-92 cycles each)
   - 20 Prototype: no ALG1 trace (different policy); placement recovered from its
                   own per-model "Instance N: ACTIVE ... gpu_ids: [g]" dump

TOTAL_EXPECTED_NEW_DIAGNOSTIC = 4
TOTAL_FOUND_NEW_DIAGNOSTIC    = 4
TOTAL_USABLE_NEW_DIAGNOSTIC   = 4
```

Nothing was silently excluded. Two Prototype directories carry attempt suffixes
(`bursty/rate_2/seed_1.harness-kill-attempt2`,
`…seed_1.inner-session-misnamed-attempt1`) and are the previously invalidated
attempts; they are excluded by the established naming convention, leaving exactly
20 valid Prototype runs. Trace pairing and per-run integrity for the 8
OLD/NEW comparison runs were verified in Phase 7b (`RUN_INTEGRITY = PASS`,
byte-identical canonical traces).

---

## 5. Exact Alg1 replay coverage

```
REPLAY_CONFIRMED     = 1491
REPLAY_MISMATCH      =  194     -> 1491/1685 = 88.5%
INSUFFICIENT_TELEMETRY = 382    (unsolvable cycles, mostly all-zero warm-up rates)
```

| estimator | workload | cycles | match |
|---|---|---|---|
| NEW | steady | 351 | **97.4 %** |
| OLD | steady | 854 | 87.0 % |
| OLD | bursty | 480 | 84.6 % |

Mismatch concentrates at low offered rate (r2 = 81 %, r6 = 94 %), where weighted
rates are near zero and candidate GPUs tie. The offline replay relaxes the
runtime's `gpu_available_memory` availability filter, so ties break differently;
this is a limitation of the offline reconstruction, not evidence of a runtime
defect. **All objective analysis below uses replay-confirmed cycles only.**

---

## 6. Exhaustive placement method

For each replay-confirmed cycle, all 2⁴ = 16 assignments of 4 models to 2 GPUs
were enumerated; those leaving a GPU empty are excluded, giving **14 valid
placements in every cycle** — confirmed per-cycle, not assumed (`n_valid` column).
For each candidate: per-GPU model set, resident weight sum, `shared_kv = 79.25 −
Σ model_size`, per-GPU weighted demand, KVPR per GPU, peak KVPR.

Definitions are imported verbatim from `bad_placement_forensic/forensic.py`; the
only modifications are the widened condition set, composition/shape bookkeeping,
and the latency join. **Enumeration is analysis-only and is not a replacement
algorithm.**

---

## 7. KVPR-optimal composition across 4-HET

Replay-confirmed cycles. `optLL` = large-large is globally KVPR-optimal;
`planLL` = recorded Alg1 plan is large-large; `residLL` = actual residency.

| stratum | cycles | optLL | planLL | residLL |
|---|---:|---:|---:|---:|
| OLD steady seed1 | 335 | 36.1 % | 55.5 % | 46.3 % |
| OLD steady seed2 | 408 | 8.3 % | 13.5 % | 7.6 % |
| OLD bursty seed1 | 235 | 16.2 % | 14.5 % | 15.7 % |
| **OLD bursty seed2** | 171 | **50.3 %** | 52.6 % | 36.8 % |
| OLD steady overall | 743 | 20.9 % | 32.4 % | 25.0 % |
| OLD bursty overall | 406 | 30.5 % | 30.5 % | 24.6 % |
| OLD seed1 overall | 570 | 27.9 % | 38.6 % | 33.7 % |
| OLD seed2 overall | 579 | 20.7 % | 25.0 % | 16.2 % |
| **OLD ALL** | **1149** | **24.3 %** | 31.8 % | 24.9 % |
| NEW steady (4 diag) | 342 | 27.2 % | 38.6 % | 28.7 % |

**The seed1 label is a red herring.** The highest large-large optimality in the
whole set is *bursty seed 2* (50.3 %), and bursty seed1 is among the lowest
(16.2 %). What tracks the outcome is not the seed index but which model tops the
demand ranking — see §8.

---

## 8. Demand-rank mechanism

| rank-1 model | cycles | share | P(optLL) | P(planLL) | P(residLL) |
|---|---:|---:|---:|---:|---:|
| **Llama-3.2-3B** (small) | 625 | 41.9 % | **57.1 %** | **77.6 %** | 46.1 % |
| **Qwen2.5-3B** (small) | 10 | 0.7 % | 90.0 % | 50.0 % | 30.0 % |
| Llama-3.1-8B (large) | 698 | 46.8 % | **0.3 %** | 0.4 % | 10.5 % |
| Qwen2.5-7B (large) | 158 | 10.6 % | 2.5 % | 2.5 % | 12.7 % |

Pooled: **small rank-1 → 57.6 %** large-large optimal (n = 635);
**large rank-1 → 0.7 %** (n = 856).

The mechanism claim is confirmed quantitatively: when a small model carries the
largest KV-weighted demand, Alg1 isolates it and the two large models become
co-resident. `rank1_model_summary.csv`.

Per-condition this is near-deterministic in steady: every steady seed-1
condition has Llama-3.2-3B as rank-1 (57–89 % of cycles) with optLL 29–46 %,
and every steady seed-2 condition has Llama-3.1-8B as rank-1 (63–94 %) with
optLL 1–15 %.

---

## 9. Objective margin analysis

`separated_penalty = (best large-separated peak KVPR − global best) / global best`

Raw percentiles (primary evidence):

| stratum | n | p50 | p90 | p95 | p99 | max |
|---|---:|---:|---:|---:|---:|---:|
| OLD steady | 743 | 0.0000 | 0.1475 | 0.2359 | 0.3641 | 0.4739 |
| OLD bursty | 406 | 0.0000 | 0.2422 | 0.2525 | 0.4517 | 0.5934 |
| OLD all | 1149 | 0.0000 | 0.2422 | 0.2430 | 0.4010 | 0.5934 |
| NEW steady diag | 342 | 0.0000 | 0.1585 | 0.2585 | 0.3495 | 0.4142 |

Descriptive bands (secondary): OLD all — ≤1 % 76.9 %, 1–5 % 2.7 %,
5–10 % 2.9 %, **>10 % 17.5 %**.

The distribution is **bimodal, not "close"**. In roughly three quarters of cycles
the memory objective is essentially indifferent between compositions (p50 = 0).
In the remaining ~17.5 % it prefers co-location **strongly** — up to 59 % worse
peak KVPR for the best separated alternative. So:

- `NEAR_TIE` describes the majority of cycles;
- `STRONG_COLOCATION_PREFERENCE` describes a substantial, non-negligible tail;
- `SEPARATION_PREFERENCE` is what holds whenever a large model is rank-1.

The tail is what matters, because those are the cycles that pin a plan in place.

---

## 10. Large-model co-residency and decode cost

Within-run, requests split by whether they arrived during large-large residency
(`composition_latency.csv`; runs needing ≥50 requests in both states):

| metric | n runs | median | direction |
|---|---:|---:|---|
| Qwen2.5-7B TPOT p95 ratio (coloc/sep) | 21 | **1.204** | worse in **17/21** |
| aggregate TPOT p50 ratio | 21 | 1.139 | worse in 13/21 |
| joint-SLO attainment delta | 21 | **−0.0704** | lower in **15/21** |

Extremes are large: `OLD bursty_r8_s2` 10.56×, `OLD bursty_r10_s2` 7.59×,
`NEW steady_r8_s1` 4.48×.

**Caveat, stated rather than buried:** these are within-run splits, which control
for workload, rate, seed and run identity, but *not* for time-varying offered
load. Co-residency may itself be more likely during heavy periods. The
association is consistent and sizeable; it is not a controlled causal test.

---

## 11. Steady vs bursty

| regime | Qwen7B TPOT p95 ratio | attainment delta |
|---|---|---|
| steady (n=11) | 1.193, worse in 10/11 | **−0.1015**, lower in **10/11** |
| bursty (n=10) | 1.229, worse in 7/10 | **+0.0139**, lower in 5/10 |

The *latency* penalty is similar in both regimes; the *SLO* penalty is not.
In bursty, attainment is essentially unaffected by composition.

This report does not force a single explanation. Consistent with the data:
bursty co-residency is less persistent (Prototype bursty large-large 4–80 % and
fluctuating vs steady 0 %), and burst gaps interrupt sustained decode contention
so the SLO cliff is not reached. Other bottlenecks dominating in bursty is
equally consistent. **Hypotheses, not conclusions.**

---

## 12. Prototype static pairing comparison

`prototype_pairing.csv`, parsed from the Prototype's own ACTIVE-instance dump.

| regime | dominant composition | large-large residency | migrations |
|---|---|---|---|
| **steady, all 10 conditions** | **`3+6 \| 4+5` at 100 % of snapshots** | **0.0 %** | **0** |
| bursty, 10 conditions | alternates `3+4 \| 5+6` and `3+6 \| 4+5` | 4.2 % – 80.0 % | 0 |

`3+6 | 4+5` = {Llama-3.2-3B, Qwen2.5-7B} | {Qwen2.5-3B, Llama-3.1-8B} — exactly
one large model per GPU, and shape `2+2` throughout.

In steady the Prototype never migrates and never co-locates the large models. In
bursty it co-locates them frequently. Its measured advantage follows the same
split: steady goodput 5.03 vs Prism 3.17 (−36.9 %), bursty 4.37 vs 4.28 (−2 %).

**Interpretation, kept within the evidence:** the Prototype's steady behaviour is
not a smarter placement decision — it performs *no* placement optimisation and
holds its initial assignment. That assignment happens to separate the two
compute-heavy models. Algorithm 1 abandons that pairing when a small model
dominates memory demand, because doing so lowers peak KVPR. Part of the
Prototype's steady advantage is therefore attributable to an accidental
compute-friendly static pairing. This is association across matched conditions
plus a coherent mechanism, not a controlled experiment.

---

## 13. OLD vs NEW estimator realization

Only the 4 paired diagnostic conditions.

| cond | est | optimum LL | plan LL | residency LL |
|---|---|---:|---:|---:|
| steady r8 s1 | OLD → NEW | 38.8 % → **64.0 %** | 67.3 % → 80.9 % | 55.1 % → 65.2 % |
| steady r10 s1 | OLD → NEW | 32.2 % → 43.2 % | 55.9 % → 69.1 % | 33.9 % → 49.4 % |
| steady r8 s2 | OLD → NEW | 8.8 % → 1.2 % | 13.8 % → 4.7 % | 8.8 % → 0.0 % |
| steady r10 s2 | OLD → NEW | 1.2 % → 0.0 % | 3.5 % → 0.0 % | 0.0 % → 0.0 % |
| **pooled** | OLD → NEW | 16.8 % → 27.2 % | 29.2 % → 38.6 % | 19.7 % → 28.7 % |

```
GLOBAL_OPTIMUM preference change = +10.4 pp
PLAN realization change          =  +9.4 pp
RESIDENCY realization change     =  +8.9 pp
```

### Correction to the Phase 7b wording

Phase 7b stated that the estimator correction *"did not materially create the
preference; it allowed Alg1 to realize and persist its existing preference."*
That is **only half right and is corrected here.** The three levels moved by
**comparable amounts** (+10.4 / +9.4 / +8.9 pp): the corrected estimator changed
*which placement is KVPR-optimal* about as much as it changed realization.

What survives is the directional claim: the estimator **amplifies each
condition's pre-existing lean** rather than creating a new one. No condition
inverts — seed-1 conditions were already large-large-leaning and became more so;
seed-2 conditions were already near zero and went to zero. The rank-1 model is
unchanged in every condition.

*Caveat:* OLD replay coverage for `steady_r8_s1` is only 49 cycles (58 %), so
that row rests on a subset.

---

## 14. 3+1 vs composition reinterpretation

Within-run Qwen2.5-7B TPOT p95 ratio, state ON vs OFF (`shape_vs_composition.csv`):

| explanatory variable | n runs | median ratio | worse in |
|---|---:|---:|---|
| composition (large-large) | 18 | **1.195** | **14/18 (78 %)** |
| occupancy shape (3+1) | 24 | 1.166 | 16/24 (67 %) |

**Refinement, not replacement.** Composition is the modestly better discriminator
on both effect size and consistency, and it is the variable with a mechanism
behind it (two compute-heavy models sharing one GPU). But the margin is small,
and 3+1 exposure retains real explanatory power. Earlier analyses that emphasised
`3+1 exposure` are **not withdrawn**; they are refined — occupancy count is a
partial proxy for what is actually a composition effect. Historical results are
kept as recorded.

---

## 15. Hypothesis verdicts

| | verdict | strongest evidence |
|---|---|---|
| **H1** systematic memory-vs-compute conflict | **SUPPORTED** | Large-large is globally KVPR-optimal in 24.3 % of all replay-confirmed OLD cycles (up to 50.3 % in bursty s2), and co-residency costs Qwen7B TPOT p95 in 17/21 runs (median 1.204) and attainment in 15/21 (median −0.070). |
| **H2** seed1-specific trace structure | **DISPROVEN** | The highest large-large optimality is **bursty seed 2 (50.3 %)**; bursty seed1 is 16.2 %. The governing variable is the rank-1 model, not the seed: small rank-1 → 57.6 %, large rank-1 → 0.7 %. |
| **H3** workload-regime dependence | **SUPPORTED** | Attainment delta steady −0.1015 (lower in 10/11) vs bursty +0.0139 (lower in 5/10), while the TPOT ratio is similar (1.193 vs 1.229). Occurrence is regime-neutral; damage is not. |
| **H4** Prototype pairing advantage | **SUPPORTED** | Prototype holds one large model per GPU in **100 % of snapshots in all 10 steady conditions** (0.0 % large-large, 0 migrations), and 4.2–80.0 % in bursty — matching its −36.9 % steady vs −2 % bursty advantage over Prism. |
| **H5** estimator creates bad preference | **PARTIALLY SUPPORTED** | The optimum itself moved +10.4 pp, comparable to plan (+9.4) and residency (+8.9). The estimator **amplifies** each condition's existing lean; it inverts none and changes no rank-1 model. Phase 7b's stronger claim is corrected in §13. |
| **H6** Alg1 objective limitation | **SUPPORTED** | Replay 88.5 % overall / 97.4 % on NEW; at Phase 7b's critical cycles the co-located plan was rank 1 of 14. A correct, faithful Alg1 selects a compute-hostile composition because KVPR represents memory pressure only. **Paper-faithful algorithmic limitation / applicability boundary — not a correctness bug.** |

---

## 16. Fidelity implications

Under this heterogeneous 4-model regime, **Prism's memory-pressure objective can
prefer a compute-hostile model composition.** The algorithm is implemented
faithfully and optimises what the paper specifies; the specification simply does
not represent decode interference between co-resident large models.

Kept strictly separate:

- **PAPER-FAITHFUL BASELINE** — Algorithm 1 as specified. This behaviour is a
  finding to report, not a defect to repair. Not modified in this task.
- **POSSIBLE VARIANT** — interference-aware placement. A research contribution
  and a deviation from the paper. **Not implemented here**, and it must never be
  folded silently into the baseline.

### Statistical caution

Only two seeds exist. Nothing here is a population-level significance claim. The
evidence is descriptive consistency, condition-level agreement, within-run
comparisons and effect sizes. Seed sensitivity is explicitly the *subject* of
this report, not a nuisance: the rank-1 model differs by seed, and that is what
drives the outcome.

---

## 17. Recommended next step

**Outcome A.** Large-large co-residency is frequently and materially
KVPR-optimal across valid conditions and is associated with decode degradation.
Treat this as an **Algorithm 1 applicability boundary** for the paper-faithful
baseline and record it as a headline finding of the 4-HET evaluation.

**Recommended single next step:** proceed to the calibration sequence —
**window calibration first, then τ recalibration, then a clean hold-out final
baseline** — with the placement-composition metrics from this report added as
standing reported outcomes.

The window test is now justified on a mechanism, not on Appendix A.4 alone: §8
shows the outcome is decided by *which model is rank-1 in weighted demand*, and
§13 shows the estimator's time semantics shift the KVPR optimum by +10.4 pp
without changing rank-1. A ~60 s window changes exactly that input's smoothing,
so it plausibly changes how often a transient small-model demand spike takes
rank-1. That is a testable, pre-declared causal path — which is what the Phase 7b
recommendation lacked.

**Calibration-data requirement (carried forward, not executed here):** the
seed 1/2 traces are now heavily analysed and are unsuitable as final evaluation
data. Any future protocol must separate **calibration traces** from **hold-out
final traces**, and must not tune against seed1. New traces are not generated in
this task.

---

## 18. STOP status

No new GPU runs. No full corrected 4-HET. No window test. No τ calibration or
sweep. No many-model workload. No Algorithm 1 modification. No compute-aware
placement. No git push. Raw logs untouched.
