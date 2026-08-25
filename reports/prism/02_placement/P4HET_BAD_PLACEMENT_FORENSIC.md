# Phase 7b — Forensic: why the corrected planner stabilizes a compute-hostile placement

Offline only. No E2E run, no τ/window/Algorithm-1/scheduler/workload change, no
push. Artifacts: `exp/analysis/estimator_correction/bad_placement_forensic/`.

---

## 1. Executive summary

The bad placement is **not** an estimator artifact, **not** a greedy
tie-breaking accident, **not** an actuation failure, and **not** an
implementation mismatch.

At every large-large episode start in the two regressing conditions, the plan
Alg1 chose was the **global KVPR optimum** — rank 1 of 14 valid placements — and
the best large-separated alternative was **2.0 % to 25.4 % worse** under the
KVPR objective. Offline replay reproduces the recorded plan in **342/351
(97.4 %)** of cycles.

The cause is the demand ranking, and it is a property of the **trace**, not of
the estimator:

| condition | model with the highest weighted demand | large models end up |
|---|---|---|
| seed 1 (r8, r10) | **Llama-3.2-3B** (a *small* model), top in 83–88 % of cycles | paired |
| seed 2 (r8, r10) | **Llama-3.1-8B** (a *large* model), top in 76–94 % of cycles | separated |

Algorithm 1 sorts by weighted token rate and isolates the heaviest memory
consumer. When the heaviest consumer is a small model, the two large models are
left to share a GPU. KVPR contains **no term for compute contention**, so this
pairing is invisible to the objective. That is Algorithm 1 as the paper
specifies it.

The ranking is essentially **identical in OLD and NEW** (seed1 top-model share
83.5 % → 88.4 %). What the estimator correction changed is that Alg1 now *acts*
on the preference instead of thrashing around it: planned co-location in
steady r8 s1 went **38.8 % → 76.8 %** of cycles, actual residency
**36.5 % → 67.4 %**.

So: **the estimator fix did not create the bad placement. It removed the
measurement noise that was accidentally preventing Alg1 from reaching its own
optimum.** The previous arm was not better-behaved; it was less able to execute.

---

## 2. Scope / frozen conditions

Runtime OLD `6618671`; NEW `6618671` + estimator patch (`2b5430c1b04b21e9`),
estimator semantics only. τ = 0.00035, window 30 s, cooldown 30 s, overlap,
SLO, models, traces all unchanged. Prism only, 4 conditions.

---

## 3. Run integrity — **PASS**

| arm | cond | rc | verdict | completed/offered | aborted | Alg2 viol | migr | trace sha256 |
|---|---|---|---|---|---|---|---|---|
| OLD | steady_r8_s1 | 0 | PASS | 3373/3373 | 0 | 0 | 9 | `bdebbdd1ad5f` |
| NEW | steady_r8_s1 | 0 | PASS | 3373/3373 | 0 | 0 | 9 | `bdebbdd1ad5f` |
| OLD | steady_r8_s2 | 0 | PASS | 3318/3320 | 0 | 0 | 10 | `ced4e84d8d40` |
| NEW | steady_r8_s2 | 0 | PASS | 3320/3320 | 0 | 0 | 6 | `ced4e84d8d40` |
| OLD | steady_r10_s1 | 0 | PASS | 4138/4139 | 0 | 0 | 10 | `2e6bd7550c3b` |
| NEW | steady_r10_s1 | 0 | PASS | 4138/4139 | 0 | 0 | 10 | `2e6bd7550c3b` |
| OLD | steady_r10_s2 | 0 | PASS | 4181/4181 | 0 | 0 | 6 | `d0d9b92e6dee` |
| NEW | steady_r10_s2 | 0 | PASS | 4179/4181 | 0 | 0 | 4 | `d0d9b92e6dee` |

OLD and NEW use the **byte-identical canonical trace** in all four conditions,
each matching the frozen workload manifest. Trace paths were read from each
run's own `STAGE_CMD.sh`, not assumed. All failed-gate lists empty; Alg2 order
violations and staged-return failures zero everywhere.

Noted, not a failure: 1–2 `client_errors` appear in `OLD r8s2`, `OLD r10s1`,
`NEW r10s1`, `NEW r10s2`. They are present in **both** arms, predate this phase,
and sit inside the accepted gate suite.

---

## 4. Large-model co-residency episodes

`large_large_episodes.csv`. An episode is a maximal run of controller cycles
whose **actual residency** places Llama-3.1-8B and Qwen2.5-7B on one GPU.

| arm | episodes | total duration | classification |
|---|---|---|---|
| OLD | 10 | 282 s | 6 actuation-lag, 3 direct, 1 intermediate |
| NEW | 7 | **506 s** | 3 actuation-lag, **4 direct** |

NEW has **fewer** episodes but **1.8× the exposure**. The two dominant NEW
episodes are the story:

| cond | cycles | duration | plan co-located fraction | class |
|---|---|---|---|---|
| steady r8 s1 | 40 | **197.3 s** | **0.97** | DIRECT_SELECTION |
| steady r10 s1 | 31 | **152.1 s** | **1.00** | DIRECT_SELECTION |

During these, Alg1's *target plan* co-locates the two large models in
essentially every cycle. This is deliberate placement, not a swap in progress.

The short 25 s episodes (plan fraction 0.0–0.5) are genuine actuation lag —
consistent with the 30 s migration cooldown — but they are a minority of
exposure and occur in both arms.

---

## 5. Exact Alg1 replay

Offline re-implementation of `kvpr_global_v3._greedy_placement`, fed the exact
per-cycle weighted rates recovered by the Phase 6 solver, run without reordering
or improving anything:

```
ALG1_REPLAY = 342/351 cycles (97.4%)
```

The recorded decisions are reproducible from the recorded rates plus the
specified semantics. **H5 is not supported.**

---

## 6. Chosen vs large-separated

`kvpr_enumeration.csv`. All 16 assignments of 4 models to 2 GPUs were
enumerated; the 14 with both GPUs non-empty are the valid set.

**Definition:** `objective_margin = best_separated_peak_KVPR − chosen_peak_KVPR`.
**Positive means the best separated plan is worse than what Alg1 chose.**

At every critical (episode-start) cycle:

| cond | cycle | chosen co-located | chosen peak KVPR | best separated | rel. margin | chosen rank |
|---|---|---|---|---|---|---|
| steady r8 s1 | 14 | yes | 0.086760 | 0.092327 | **+6.4 %** | **1 / 14** |
| steady r8 s1 | 26 | yes | 0.084299 | 0.085955 | **+2.0 %** | **1 / 14** |
| steady r8 s1 | 49 | yes | 0.072941 | 0.075551 | **+3.6 %** | **1 / 14** |
| steady r10 s1 | 30 | yes | 0.114937 | 0.126615 | **+10.2 %** | **1 / 14** |
| steady r10 s1 | 54 | yes | 0.092132 | 0.115504 | **+25.4 %** | **1 / 14** |

The co-located placement is **strictly KVPR-optimal** at every bad-episode start,
by a margin far above any tie tolerance. Answer to the question posed in §6 of
the brief: **category 1 — clearly better under KVPR.**

---

## 7. Objective flatness

| cond | rel. margin p10 | p50 | p90 | within 1 % | within 5 % | within 10 % | co-located is optimal | separated strictly better |
|---|---|---|---|---|---|---|---|---|
| steady r8 s1 | −0.0294 | **+0.0642** | +0.3003 | 25.3 % | 38.9 % | 55.8 % | **61.1 %** | 16.8 % |
| steady r10 s1 | −0.0653 | +0.0000 | +0.1007 | 36.9 % | 59.5 % | 89.3 % | **41.7 %** | 25.0 % |
| steady r8 s2 | 0.0000 | 0.0000 | 0.0000 | **93.0 %** | 96.5 % | 98.8 % | 1.2 % | 7.0 % |
| steady r10 s2 | 0.0000 | 0.0000 | 0.0000 | **98.8 %** | 98.8 % | 100 % | 0.0 % | 2.3 % |

Flatness is **condition-dependent, and it is the opposite of the story we
expected**. In seed 2 the objective is almost perfectly flat (93–99 % of cycles
within 1 %) and the planner nevertheless lands on separated placements. In
seed 1 the objective is **not** flat at the decisions that matter: co-location is
strictly optimal in 42–61 % of cycles and the median margin against the best
separated plan is +6.4 % (r8).

Flatness therefore does **not** explain the bad placement.

---

## 8. Greedy-path analysis

`greedy_steps.csv`. Not required as an explanation: the greedy result is
**rank 1 of 14** at every critical cycle, i.e. it found the global optimum of the
stated objective. There is no better large-separated plan for greedy to have
missed. **H3 is disproven** — this is a paper-specified greedy consequence
operating correctly, not an ordering artifact.

The ordering does explain the *composition*: Alg1 places in descending weighted
rate, so the top-demand model is isolated first and everything else fills in
around it.

---

## 9. Estimator-history analysis

### H6 as stated — not supported

Weighted rate binned by time since that model last migrated:

| cond | arm | model | 0–15 s | 15–30 s | 30–60 s | > 60 s | n migr |
|---|---|---|---|---|---|---|---|
| r8 s1 | OLD | Llama-3.1-8B | 1.434 | 2.020 | 2.313 | **2.627** | 3 |
| r8 s1 | OLD | Qwen2.5-7B | 1.523 | 2.222 | 2.253 | 2.033 | 5 |
| r8 s1 | **NEW** | Llama-3.1-8B | 1.674 | 1.406 | 1.385 | 1.725 | 2 |
| r8 s1 | **NEW** | Qwen2.5-7B | 1.901 | 1.751 | 2.165 | 1.483 | 6 |

The warm-up ramp H6 predicts is clearly present in **OLD** (1.43 → 2.63, a
monotone climb) and **absent in NEW** (flat and non-monotone). The corrected
window does not under-represent a just-migrated model in the way hypothesised.
Sample sizes (1–6 migrations per model) are small, so this is stated as
**not supported / inconclusive**, not disproven.

### A residual endogeneity that is real

Weighted rate split by whether the two large models are currently co-resident:

| cond | arm | model | co-resident | separated | ratio |
|---|---|---|---|---|---|
| r8 s1 | NEW | Qwen2.5-7B | 1.451 | 1.955 | **0.74** |
| r8 s2 | OLD | Qwen2.5-7B | 1.016 | 1.667 | **0.61** |
| r10 s1 | NEW | Qwen2.5-7B | 2.356 | 2.523 | 0.93 |
| r8 s1 | NEW | Llama-3.1-8B | 1.891 | 1.748 | 1.08 |

The decode component is still **achieved** production, merely windowed.
Windowing changed its timescale, not its endogeneity. A co-resident,
compute-starved Qwen2.5-7B produces fewer decode tokens, so it *measures* as
lower demand, which makes co-location look cheaper. This is a **lock-in**
feedback rather than the oscillation feedback of Phase 6. It is present in OLD
too, so it is not introduced by the correction, and at ~0.74–0.93 it is a
contributing amplifier, not the primary cause.

**Limitation, stated plainly:** offered/latent demand is not measurable from
these artifacts. Only admitted input tokens and achieved decode production are.
Any claim about what these models *would* have consumed is out of reach here.

---

## 10. Plan vs runtime actuation

| classification | OLD | NEW | NEW exposure |
|---|---|---|---|
| DIRECT_SELECTION | 3 | **4** | **~440 s of 506 s** |
| STALE_OR_FAILED_ACTUATION | 6 | 3 | ~66 s |
| INTERMEDIATE_STATE | 1 | 0 | 0 |

The Phase 3 mistake — reading 3+1 states as unfinished swaps — is explicitly not
repeated: classification is by the *target plan* recorded in the same cycles, not
by shape. The dominant NEW episodes have plan-colocated fractions of 0.97 and
1.00. **H4 is disproven for the exposure that matters**, while remaining the
correct description of the short 25 s episodes.

---

## 11. Placement ↔ latency

| cond | arm | co-located share | TTFT>30 s total | inside co-loc | rate inside | rate outside |
|---|---|---|---|---|---|---|
| r8 s1 | OLD | 38.0 % | 11 | 5 (45.5 %) | 0.40 % | 0.28 % |
| r8 s1 | **NEW** | 77.7 % | **727** | **724 (99.6 %)** | **30.09 %** | **0.31 %** |
| r10 s1 | OLD | 27.3 % | 12 | 10 (83.3 %) | 0.87 % | 0.07 % |
| r10 s1 | NEW | 52.1 % | 46 | 18 (39.1 %) | 0.88 % | 1.34 % |

For **steady r8 s1** the association is overwhelming: 724 of 727 stalled
requests arrive inside a co-residency window, and the stall rate inside is
**97× the rate outside**. This is association on aligned timelines, not a
controlled causal test — but the alternative explanations (a single stall, an
aborted run) are excluded by §3 and by the 64–420 s spread.

For **steady r10 s1 the association is absent** (0.88 % inside vs 1.34 %
outside). That condition's regression is therefore **not** explained by a TTFT
mechanism, and is not claimed to be. Its loss is consistent with TPOT, which
this section does not resolve.

---

## 12. Hypothesis verdicts

| | verdict | strongest evidence |
|---|---|---|
| **H1** estimator-driven KVPR preference | **SUPPORTED (partial)** | Co-located is rank 1/14 with +2.0 %…+25.4 % margin at every critical cycle. NEW's planned co-location 38.8 % → 76.8 % vs OLD, and NEW's large-model rates in r8 s1 are ~20 % lower (L3.1-8B 2.269 → 1.845). **But** the demand *ranking* is unchanged (top-model share 83.5 % → 88.4 %), so the estimator shifted magnitude and stability, it did not create the preference. |
| **H2** objective flatness / near-tie | **DISPROVEN** as the cause | At bad-episode starts the margin is +2.0 %…+25.4 %, not a tie. Flatness is instead characteristic of the *well-behaved* seed 2 (93–99 % of cycles within 1 %). |
| **H3** greedy-ordering artifact | **DISPROVEN** | Chosen plan is rank 1/14 at every critical cycle — greedy reached the global optimum. Paper-specified greedy consequence. |
| **H4** runtime actuation mismatch | **DISPROVEN** for dominant exposure | 197 s and 152 s episodes have plan-colocated fraction 0.97 / 1.00. Remains valid for the short 25 s cooldown-lag episodes. |
| **H5** implementation mismatch | **DISPROVEN** | Offline replay 342/351 = 97.4 %. |
| **H6** window-history under-representation | **NOT SUPPORTED / INCONCLUSIVE** | Predicted post-migration ramp present in OLD (1.434 → 2.627), absent in NEW (1.674 → 1.406 → 1.385 → 1.725). n = 1–6 migrations per model is too small for a firm verdict. |

**Root cause:** KVPR is a pure memory-pressure objective. Algorithm 1 isolates
the highest weighted-memory-demand model. In seed 1 that model is a *small* one
(Llama-3.2-3B), so the two compute-heaviest models are left sharing a GPU — a
choice the objective cannot see as costly because it has no compute-contention
term. The corrected estimator made the planner stable enough to hold that
optimum for 197 s at a stretch.

---

## 13. Correction to earlier wording

Phase 6 and the Phase 7 report both stated that the residual reversals were
**"attributable to objective flatness."** That claim is **withdrawn as
unproven.**

What Phase 6 actually established is narrower: holding the endogenous decode
term constant leaves 80/94 reversals standing. That rules the *estimator
counterfactual* out as a sufficient explanation; it does **not** establish
flatness, which was never measured at the time. This phase measures it directly
and finds the objective is **not** flat in the seed-1 conditions where the
damage occurs (median margin +6.4 %), while being nearly perfectly flat in the
seed-2 conditions that behave well. Flatness and bad outcomes are, if anything,
anti-correlated here.

The four failure modes should be kept distinct going forward:

- **planner stability** — was the real defect; measured and **fixed** (plan lifetime +96 %, reversals −23 %).
- **placement quality** — the live problem; Alg1 optimises the right objective and still lands on a compute-hostile composition.
- **KVPR objective limitation** — memory-only, compute-blind. Paper-specified.
- **implementation artifact** — largely excluded (replay 97.4 %; residual co-residency demand suppression ~0.74–0.93, present in both arms).

---

## 14. Recommended next step

**Outcome B.** The remaining issue is memory-objective compute-blindness in
placement composition, which is inherent to Algorithm 1 as specified — not a
window problem, not a τ problem, and not an implementation bug.

**Recommended single next step — offline, zero GPU:** extend the exhaustive
enumeration already built here across **all** existing 4-HET conditions
(bursty and steady, both arms, all 40 runs) and measure how often KVPR's
optimum co-locates the two large models, and what it costs. This decides whether
seed 1 is a quirk of two traces or a systematic property of this 4-model
configuration — and that determination changes what the next *experiment* should
even be. It reuses `forensic.py` and needs no new runs.

**Explicitly not recommended next: the 30 s vs ~60 s window test.** A.4's ~60 s
remains a real fidelity gap and should be closed eventually, but the evidence
says it will not address this: the demand *ranking* that produces the pairing is
identical under both estimators, and a longer window smooths magnitudes without
reordering models. Running it now would spend GPU time on a variable this
forensic has already shown to be off the causal path.

**Distinguishing baseline from variant, so the next intervention is honest:**

- A **paper-faithful baseline** must keep KVPR memory-only and must report this
  behaviour as a finding, not repair it. Co-locating the two large models when a
  small model dominates memory demand is what the specified algorithm does.
- An **improved Prism variant** would need a compute-contention term in
  placement. That is a research contribution and a deviation from the paper — it
  must be built and labelled as a variant, never folded silently into the
  baseline.

---

## 15. STOP status

No E2E run, no τ sweep, no window change or run, no many-model workload, no
Prototype rerun, no Algorithm-1 / scheduler / migration / workload / SLO change,
no git push. Raw logs untouched.

---

> **Correction (4-HET-wide analysis).** §1 and §12 of this document state that
> the estimator correction *"did not create the preference; it removed the noise
> preventing Alg1 from reaching its own optimum."* Measured across the paired
> conditions, the **KVPR optimum itself** shifted by **+10.4 pp**, comparable to
> plan (+9.4 pp) and residency (+8.9 pp) — so the estimator changed *which*
> placement is optimal about as much as it changed realization. The directional
> claim survives: it amplifies each condition's pre-existing lean, inverts none,
> and changes no rank-1 model. See
> `P4HET_KVPR_PLACEMENT_QUALITY_ANALYSIS.md` §13.
>
> Separately, the "seed 1" framing in this document is superseded: the highest
> large-large optimality in the whole 4-HET set is **bursty seed 2 (50.3 %)**.
> The governing variable is which model is rank-1 in weighted demand, not the
> seed index.
