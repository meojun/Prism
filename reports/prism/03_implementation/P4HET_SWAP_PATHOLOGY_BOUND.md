# Phase 4 Stage A — offline achievable-gain bound for the intermediate 3+1 state

**No benchmark was run for this stage.** Existing artifacts only. Nothing was
modified. Data: `exp/analysis/swap_pathology_bound/`.

## Question

How much of the 4-HET steady degradation could plausibly be attributed to time
spent in the intermediate 3+1 state that Algorithm 1's swap plans pass through?

## A1. Exposure — wall-clock and decode-time are different quantities

| condition | run span | time in 3+1 | **run exposure** | decode time | decode under 3 models | **decode exposure** | episodes |
|---|---:|---:|---:|---:|---:|---:|---:|
| steady 8 s1 | 432 s | 300 s | **69.5%** | 21 711 s | 12 789 s | **58.9%** | 5 |
| steady 8 s2 | 432 s | 153 s | 35.4% | 19 320 s | 6 186 s | 32.0% | 5 |
| steady 10 s1 | 429 s | 159 s | 37.0% | 28 387 s | 9 620 s | 33.9% | 5 |
| steady 10 s2 | 430 s | 91 s | 21.2% | 24 044 s | 3 869 s | 16.1% | 3 |

Decode exposure runs consistently **below** wall-clock exposure (e.g. 58.9% vs
69.5%), so the two are reported separately rather than one standing in for the
other.

Per-model decode exposure to 3-model co-residency:

| condition | Llama-3.2-3B | Qwen2.5-3B | Llama-3.1-8B | Qwen2.5-7B |
|---|---:|---:|---:|---:|
| steady 8 s1 | 66.0% | 75.0% | 77.8% | 35.6% |
| steady 8 s2 | 20.8% | 40.0% | 25.2% | 44.3% |
| steady 10 s1 | 28.0% | 45.1% | 30.1% | 33.8% |
| steady 10 s2 | 25.4% | 26.9% | **0.0%** | 23.9% |

Exposure is uneven across models and seeds — it is not a uniform tax.

## A2. Placement-conditioned decode cost (same run, same model)

From Phase 3 `qwen7b_by_placement.csv`, ITL p50 by co-residency:

| model | 1 model | 2 models | 3 models | slowdown 3 vs 2 |
|---|---:|---:|---:|---:|
| Llama-3.2-3B | 0.0110 | 0.0217 | 0.0300 | **1.38×** |
| Qwen2.5-3B | — | 0.0229 | 0.0306 | **1.34×** |
| Llama-3.1-8B | 0.0139 | 0.0266 | 0.0437 | **1.65×** |
| Qwen2.5-7B | 0.0128 | 0.0259 | 0.0407 | **1.57×** |

TPOT p50 slowdown 3-vs-2 for Qwen2.5-7B: 0.0599 / 0.0417 = **1.44×**
(steady 8 s1) and 0.0594 / 0.0449 = **1.32×** (steady 10 s1).

## A3. Counterfactual bound — descriptive, not causal

Construction: for each request that *began decoding* while its own GPU held 3
models, its TPOT is replaced by the **same run's, same model's** empirical
2-model-GPU TPOT quantile (p50 central, p75 low/conservative, p25 high). TTFT is
left untouched; the primary SLO is unchanged. Where no same-run/same-model
2-model sample existed the request was left unmodified — that case did not arise
(`n_exposed_unavailable = 0` in all four runs).

### Aggregate TPOT

| condition | observed mean | cf low | cf central | cf high | recovery low | **central** | high |
|---|---:|---:|---:|---:|---:|---:|---:|
| steady 8 s1 | 0.0379 | 0.0336 | 0.0313 | 0.0294 | 11.4% | **17.4%** | 22.3% |
| steady 8 s2 | 0.0357 | 0.0324 | 0.0317 | 0.0312 | 9.2% | **11.3%** | 12.8% |
| steady 10 s1 | 0.0415 | 0.0393 | 0.0379 | 0.0367 | 5.4% | **8.7%** | 11.6% |
| steady 10 s2 | 0.0355 | 0.0344 | 0.0342 | 0.0339 | 2.9% | **3.6%** | 4.5% |

### Joint-SLO goodput (only exposed requests' TPOT replaced)

| condition | exposed reqs | Prototype | observed | cf low | **cf central** | cf high | recovery | **share of the Prototype gap closed** |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| steady 8 s1 | 1585 | 6.795 | 3.335 | 4.736 | **5.754** | 5.754 | +72.5% | **69.9%** |
| steady 8 s2 | 805 | 6.840 | 4.624 | 4.945 | **5.917** | 5.917 | +28.0% | **58.4%** |
| steady 10 s1 | 1042 | 6.659 | 4.276 | 4.868 | **5.329** | 5.329 | +24.6% | **44.2%** |
| steady 10 s2 | 603 | 7.234 | 4.674 | 4.641 | **5.185** | 5.755 | +10.9% | **20.0%** |

The goodput leverage far exceeds the TPOT leverage — a 3.6–17.4% TPOT change
maps to a 10.9–72.5% goodput change — which is the SLO-cliff amplification
already established in Phase 2 §7 acting in the favourable direction here.

**This is an exposure-weighted bound, not a prediction.** It says: *if* the
exposed requests had behaved like the same run's 2-model-GPU requests, the
aggregate would have looked like this. It does **not** say that removing the
cooldown will produce this, because (a) co-residency is not randomly assigned,
(b) shortening the intermediate state changes migration timing and may change
load distribution, and (c) some 3+1 exposure would remain regardless (a two-move
swap has no atomic form in this implementation).

## A4. Verdict

**INTERMEDIATE_3_1_EFFECT_SIZE = MATERIAL.**

Numerically: 16–59% of decode time occurs under 3-model co-residency; that
co-residency carries a measured 1.32–1.65× decode slowdown in the same runs; and
neutralising it in the counterfactual closes **20.0–69.9%** of the
Prototype-versus-Prism steady goodput gap, with a central TPOT recovery of
3.6–17.4%.

The bound is large enough to justify the minimal discriminating experiment, and
the spread across seeds (20% to 70% of the gap) is itself informative: exposure
varies a lot between runs, so a mechanism test must look at exposure, not only
at goodput.

Proceeding to Stage B.
