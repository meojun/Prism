# Phase 3 — Algorithm 1 / KVPR placement forensic

**Offline analysis only.** No benchmark, no GPU work, no runtime/scheduler/
Algorithm-1/τ/c_i/SLO/workload change, no git push, no history rewrite, no
handling of the 901 MB dump in local commit `51c5405`. Raw artifacts were read,
never modified. Scripts and data: `exp/analysis/alg1/`.

Confidence labels: **PROVEN** · **STRONGLY_SUPPORTED** · **SUGGESTIVE** ·
**INCONCLUSIVE** · **DISPROVEN**.

---

## 1. Executive finding

**Algorithm 1 does not want 3+1. It plans 2+2 and cannot get there.**

In steady r8/r10, across 341 decision cycles:

- the cluster is in **3+1 for 137 cycles**;
- in **129 of those 137 (94%)** the planner's own `placement_plan` is **2+2**,
  with `convergence_gap = 1` — exactly one model sitting somewhere the plan does
  not want it;
- what prevents the corrective move is **the migration cooldown (90 cycles)** and
  **a target-memory feasibility gate (42 cycles)**, not the objective.

And 3+1 is not a memory-versus-compute trade-off, because **3+1 is worse on
Algorithm 1's own memory metric too**: peak KVPR 0.1030 → 0.1184, and the
inter-GPU KVPR gap 0.0238 → 0.0725 (3× worse). It is worse on both sides.

The mechanism is an **execution artifact of a swap**: Algorithm 1 plans to
exchange two models between GPUs; the implementation emits **at most one
migration per cycle** and then enforces a **30 s cooldown**, so every swap is
executed as two moves at least 30 s apart, and the cluster necessarily sits in
3+1 in between. 12 of the 18 entries into 3+1 came from a cycle whose plan was
2+2 and wanted **two** models moved. Median 3+1 episode duration is **30.5 s** —
the cooldown value.

## 2. Trace field semantics (from source, not from names)

`[PAPER-ALG1-V4]` is emitted at `kvpr_global_v4.py:147`. Definitions:

| field | meaning | source |
|---|---|---|
| `weighted_token_rate_j` | `token_rate_j × cell_size_j / tpot_slo_j`, converted to GiB | `kvpr_global.py:123`, `kvpr_global_v3.py:34-36` |
| `token_rate_j` | `input_rate + decode_token_tput`; `input_rate` = prompt tokens arriving in the last `rate_window` (30 s) ÷ 30 | `kvpr_global.py:103-114` |
| `shared_kv_i` | `gpu_mem − Σ model_size` of models resident on GPU *i* | `kvpr_global.py:127-133` |
| `kvpr` / `KVPR_i` | `Σ_j w_rate_j / shared_kv_i` over models on *i* | `kvpr_global.py:135-149` |
| `peak_kvpr` | `max_i KVPR_i` | `kvpr_global.py:152` |
| `current_r` | ratio `w_rate/shared_kv` at the model's **current** GPU during the sequential greedy pass (partial accumulation) | `kvpr_global_v3.py:67-71` |
| `best_r` | minimum ratio over feasible candidate GPUs at that point in the pass | `kvpr_global_v3.py:66` |
| `absolute_delta` | `current_r − best_r` | `kvpr_global_v3.py:72` |
| line-8 rule | `chosen = best if delta > τ else current` | `kvpr_global_v3.py:74` |
| `placement_plan` | the greedy's target GPU for **every** model | `kvpr_global_v3.py:88` |
| `current_placement` | model → GPU actually resident now | `kvpr_global_v4.py:78-84` |
| `convergence_gap` | count of models not where the plan wants them | `kvpr_global_v4.py:126` |
| `blocked` | per-model reason the plan's move was not emitted | `kvpr_global_v4.py:98-121` |

Note `current_r`/`best_r` are **partial-pass** ratios, not the finished KVPR;
they are not interchangeable with the `kvpr` field.

## 3. Every Algorithm 1 decision reconstructed

777 cycles parsed (`alg1_cycles.csv/json`).

| condition | cycles | current 2+2 | current 3+1 | plan 2+2 | plan 3+1 | MIGRATE |
|---|---:|---:|---:|---:|---:|---:|
| steady 8 s1 | 85 | 26 | **59** | **82** | 3 | 9 |
| steady 8 s2 | 86 | 56 | 30 | 80 | 6 | 10 |
| steady 10 s1 | 84 | 54 | 30 | 83 | 1 | 10 |
| steady 10 s2 | 86 | 68 | 18 | 75 | 11 | 6 |
| steady 2 s1 | 85 | 30 | 55 | 76 | 9 | 9 |
| steady 4 s1 | 87 | 46 | 41 | 84 | 3 | 13 |
| steady 6 s1 | 86 | 44 | 42 | 85 | 1 | 14 |
| bursty 6 s1 | 92 | 22 | 25 | **12** | **35** | 8 |
| bursty 8 s1 | 86 | 27 | 31 | **23** | **35** | 11 |

**Steady and bursty differ in kind.** In steady the plan is 2+2 in 88–99% of
cycles. In bursty the plan is genuinely 3+1 more often than 2+2 — because idle
models have near-zero weighted token rate, so concentrating them costs the
objective nothing. Bursty imbalance is largely *intended*; steady imbalance is
not.

## 4. The 2+2 → 3+1 transitions

18 transitions in steady r8/r10 (`placement_transitions.csv`). In the cycle that
emitted the move:

| plan shape | count | models the plan wanted moved |
|---|---:|---|
| **2+2** | **12** | **2 (a swap)** |
| 1+3 | 6 | 1 |

Every one was a `MIGRATE`. Immediately after landing in 3+1, the plan was 2+2 in
15 of 18 and `convergence_gap = 1` in 15 of 18.

**Q1 — does 3+1 lower KVPR imbalance?** **No.** See §6. It raises it.

## 5. How long does 3+1 persist, and why

18 episodes, `placement_episodes.csv`:

| statistic | value |
|---|---:|
| median duration | **30.5 s** |
| mean | 39.0 s |
| max | **172.2 s** |
| total time in 3+1 | 701 s |
| cycles in 3+1 | 137 |
| … of which the plan was 2+2 | **129** |
| … cooldown active | **90** |
| … target memory infeasible | **42** |

The median episode equals the configured cooldown (`--kvpr-migration-cooldown
30`). The single 172 s outlier (steady r8 s1, the condition with the worst
imbalance at 70.6%) was **memory-blocked in all 35 of its cycles**.

The memory blocks are marginal, not gross:

| blocked model | need GiB (= weights + 6.46 reserve) | free GiB | shortfall |
|---|---:|---:|---:|
| model_5 | 21.54 | 21.36 | **0.18** |
| model_5 | 21.54 | 16.78 | 4.76 |
| model_4 | 12.29 | 6.85 | 5.45 |

34 of the 42 blocks were `model_4`. The destination is the *one-model* GPU, so
the shortfall is not weights — it is the resident model's elastic KV pool having
grown into the space the incoming model needs. That is what converts a 30 s
cooldown gap into a 172 s lock-in.

**Q2 — transient or stable policy state?** **Neither, exactly: it is a
hysteresis artifact.** Not a sub-second migration overlap (total residency stays
≈4.0, so both copies are not resident); not a policy preference either (the plan
is 2+2 throughout). It is the interval between the two halves of a swap, held
open by the cooldown and occasionally by memory.

## 6. Memory balance vs compute balance

Algorithm 1's own metric, steady r8/r10:

| shape | cycles | peak KVPR p50 | \|KVPR₀−KVPR₁\| p50 |
|---|---:|---:|---:|
| 2+2 | 204 | **0.102986** | **0.023768** |
| 3+1 | 137 | 0.118352 | 0.072525 |

Compute side, same runs, ITL p50 by number of models sharing the request's GPU:

| model | 1 model | 2 models | 3 models |
|---|---:|---:|---:|
| Llama-3.2-3B | 0.0110 | 0.0217 | 0.0300 |
| Qwen2.5-3B | — | 0.0229 | 0.0306 |
| Llama-3.1-8B | 0.0139 | 0.0266 | 0.0437 |
| Qwen2.5-7B | 0.0128 | 0.0259 | 0.0407 |

**3+1 is worse on both axes.** There is no memory/compute trade-off to reason
about here — the hypothesised mismatch does not arise, because the placement the
cluster is stuck in is not the one the memory objective prefers either.

## 7. Qwen2.5-7B and Llama-3.1-8B forensic

`qwen7b_by_placement.csv`. Within-run split by co-residency:

**Qwen2.5-7B** (SLO TPOT = 0.034926):

| condition | models on its GPU | decode steps | ITL p50 | ITL p95 | ITL p99 | TPOT p50 |
|---|---:|---:|---:|---:|---:|---:|
| steady 8 s1 | 1 | 96 620 | 0.0126 | 0.0618 | 0.1170 | **0.0201** |
| steady 8 s1 | 2 | 68 282 | 0.0280 | 0.1115 | 0.2573 | **0.0417** |
| steady 8 s1 | 3 | 35 111 | 0.0444 | 0.1750 | 0.4045 | **0.0599** |
| steady 10 s1 | 1 | 18 175 | 0.0130 | 0.0607 | 0.1334 | 0.0209 |
| steady 10 s1 | 2 | 170 641 | 0.0292 | 0.1327 | 0.3201 | 0.0449 |
| steady 10 s1 | 3 | 55 460 | 0.0431 | 0.1972 | 0.4274 | 0.0594 |

**Llama-3.1-8B** shows the same monotone pattern (e.g. steady 8 s2: ITL p50
0.0135 → 0.0265 → 0.0472; TPOT p50 0.0205 → 0.0357 → 0.0694).

The relation is monotone in **every model and every condition measured**, with
large sample counts, inside the same run. Against Qwen2.5-7B's TPOT SLO of
0.0349: alone it passes with ~42% margin, at 2 models it is marginal, at 3
models **the median request already fails**.

Caveat: this is observational. Co-residency is not randomly assigned — Algorithm
1 moves models toward the less loaded GPU, so periods of 3-co-residency may
coincide with higher load for other reasons. The dose-response shape across four
models and four runs is strong evidence, not proof.

## 8. τ audit

Over the 137 3+1 cycles:

- `max_abs_delta` p50 = 0.0227, max = 0.0971, against **τ = 0.00035**;
- only **7 of 137** cycles had every line-8 delta at or below τ;
- the plan was already 2+2 in **129 of 137**, so τ was not what prevented the
  planner from wanting the balanced placement.

**τ is not the reason the cluster stays in 3+1.** τ governs whether the *plan*
moves a model; here the plan does move it, and the cooldown and memory gate stop
the *execution*. Verdict: **NOT_RELEVANT** to persistence (distinct from any
recommendation about τ, which this analysis does not make).

## 9. Implementation vs paper Algorithm 1

Full table in `exp/analysis/alg1/paper_semantics_audit.md`. Summary:

- **Every element of the decision rule MATCHES** the paper description recorded
  in `docs/paper_faithful/design_analysis.md` §2: the weight
  `token_rate × token_size / SLO`, `token_size` as KV bytes/token, SLO in the
  denominator, descending-rate ordering, `KVPR = Σ w / shared_kv`,
  `shared_kv = C − Σ model_size`, minimum-KVPR destination, and the `> τ` test.
- **The deviations are all in how the plan is applied**, and all are
  implementation additions: at most one migration per cycle (the v4 docstring
  states plainly that emitting the whole plan "is not obviously the paper's
  intent"), a 30 s migration cooldown, a `target_reserve_gib` feasibility gate
  added in v4, and a "last active model on source GPU" block.
- Tie-breaking is UNKNOWN — the reference is silent.

The paper's algorithm is not what produces 3+1. The plan-application layer is.

## 10. Prototype's 2+2

Prototype in steady performs **4 activations, 0 deactivations, 0 migrations**;
placement is static 2+2 for the whole run (Phase 2 §3.3, re-confirmed here).
Stated precisely, and without crediting it with a scheduler it does not have:
**Prototype provides a stable compute-balanced placement**, and never pays the
swap-interval cost because it never swaps.

Whether *any* 2+2 beats 3+1 on decode: the co-residency table in §6 shows the
penalty attaches to the number of models sharing a GPU, for every model measured
— which is a property of 2+2 as a class, not of Prototype's particular
assignment. **SUGGESTIVE**, since no counterfactual 2+2 assignment was executed.

## 11. Hypothesis verdicts

| # | hypothesis | verdict | basis |
|---|---|---|---|
| H1 | `--overlap-migration` causes 3+1 | **DISPROVEN** | overlap OFF left 3-on-1 at 70.6% → 70.6% (Phase 3 diagnostic); and the trace shows the cause is cooldown/memory, not the overlap path |
| H2 | Algorithm 1 intentionally selects 3+1 because it improves its KVPR objective | **DISPROVEN (steady)** | plan is 2+2 in 129/137 3+1 cycles; 3+1 raises peak KVPR 0.1030→0.1184. **In bursty it is partly true** — plan is 3+1 in 35 of 92 cycles |
| H3 | 3+1 improves memory-pressure balance | **DISPROVEN** | KVPR gap 0.0238 → 0.0725, peak KVPR higher |
| H4 | 3+1 worsens compute/decode balance | **STRONGLY_SUPPORTED** | monotone ITL/TPOT rise with co-residency in every model and condition, large n, within-run |
| H5 | 3+1 contributes to Qwen2.5-7B TPOT/ITL degradation | **STRONGLY_SUPPORTED** | TPOT p50 0.0201 (1 model) → 0.0417 (2) → 0.0599 (3) against an SLO of 0.0349 |
| H6 | τ keeps Prism stuck in 3+1 | **DISPROVEN** | deltas exceed τ by ~65× at the median; plan already 2+2 in 94% of stuck cycles |
| H7 | the implementation matches the paper's Algorithm 1 | **MATCH for the decision rule; DEVIATION for plan application** | §9 |
| H8 | memory-centric vs compute-centric objective mismatch | **DISPROVEN as stated** | 3+1 is worse on *both*; there is no trade-off in this regime. A weaker related claim — that KVPR is indifferent to compute contention — remains untested |

## 12. Verdict and branch

This is **CASE C**: 3+1 is produced by plan-application state (one migration per
cycle + 30 s cooldown, plus elastic-KV memory infeasibility), not by the
mathematical placement objective and not by an error in the objective's
implementation.

It is **not CASE A** — Algorithm 1's objective prefers 2+2 and 3+1 scores worse
on it. It is **not CASE B** in the strict sense — the implementation does not
choose 3+1; it chooses 2+2 and reaches it late.

**Implementation-bug evidence**: none in the objective. The plan-application
layer behaves as documented; whether "one migration per cycle + 30 s cooldown"
is *correct* for a plan that requires a swap is a design question, not a defect.
A two-move swap has no atomic form here, so some intermediate imbalance is
unavoidable; its *duration* is a tunable consequence of the cooldown.

**Policy-limitation evidence**: Algorithm 1 has no notion of swap atomicity and
no cost term for the intermediate state it passes through. It also has no
compute-contention term, so it cannot see that the intermediate state is
expensive.

**Regime-dependence evidence**: bursty plans 3+1 deliberately (idle models are
free to concentrate); steady does not. The pathology is specific to workloads
where all models stay active — exactly the 4-HET steady regime.

## 13. Recommended next step

**Offline first, no benchmark: quantify the swap-interval cost bound.** From the
existing traces, compute how much of each run's total decode time falls inside a
3+1 episode, and what that implies for aggregate TPOT if the interval were
shortened. `placement_episodes.csv` (701 s of 3+1 across four ~430 s runs) and
the co-residency ITL table already contain everything needed. This bounds the
achievable gain before anything is changed, and it costs nothing.

If that bound is material, the **minimal discriminating experiment** is a
cooldown-only change (`--kvpr-migration-cooldown`, a launch flag — no code,
no τ, no c_i, no SLO) on the same four steady conditions, to test whether the
3+1 fraction and the TPOT deficit move together as the trace predicts.
**Not run, not recommended without approval** — and it should be framed as a
mechanism test, not a tuning search.

I have stopped here and made no further changes.
