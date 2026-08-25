# Phase 6 — Algorithm 1 planner oscillation root-cause analysis

**Offline only.** No benchmark, no runtime/Algorithm 1/τ/cooldown/rate-window/
smoothing/workload change, no hysteresis, no atomic swap, no many-model run, no
git push. Existing artifacts read, never modified.
Data: `exp/analysis/planner_oscillation/`.

---

## 1. Executive finding

**The planner oscillates because Algorithm 1's demand estimate contains an
achieved-service term that the placement itself controls.**

`weighted_token_rate = (input_rate + decode_token_tput) × cell_size / tpot_slo`

- `input_rate` — prompt tokens **arrived** in the last 30 s. Exogenous.
- `decode_token_tput` — tokens the engine **actually decoded**, per second.
  **Endogenous**: it falls when models are co-located, and it is not reported at
  all while a model is deactivated.

Migrating a model silences its own decode reporting, collapsing its measured
demand; when it resumes, the value spikes. The planner reacts to both edges. The
loop closes through the scheduler.

Measured: **30–56% of the demand term is the endogenous component**, and across
94 reversals the flip is attributable to it **38 times versus 16 for genuine
load change — 2.4 : 1**.

The oscillation is not new. `docs/paper_faithful/design_analysis.md` §5a recorded
it at project start, on the same model, and mitigated it by raising τ to 0.35.
**The evaluation now runs τ = 0.00035** — the median line-8 delta at each
migration is **46× τ**, so that gate no longer filters anything.

## 2. Input pipeline, from source

| stage | variable | formula | window | smoothing | placement-dependent |
|---|---|---|---|---|---|
| arrivals | `received_reqs` | never pruned (`model_queue_tracker.py:108`) | — | — | no |
| input rate | `input_rate` | Σ`prompt_len` for `now − arrival ≤ 30` ÷ 30 (`kvpr_global.py:103-112`) | 30 s sliding | none | **no** |
| decode rate | `decode_token_tput` | `decode_token_count / elapsed_time` (`scheduler.py:447-451`) | ~1 s, counters reset each report | none | **yes** |
| — gating | — | reported only `if elapsed_time > 0 and self._activated` (`scheduler.py:436`) | — | — | **yes** |
| — staleness | — | `last_tput_update_time` advanced only inside that gate (`:470`); controller never ages the value (`controller_global.py:354`) | — | — | **yes** |
| demand | `weighted_token_rate` | `(input_rate + decode_token_tput) × cell_size / tpot_slo`, ÷2³⁰ | — | none | **yes** |
| pressure | `KVPR_i` | Σ demand on *i* ÷ `shared_kv_i`, `shared_kv_i = gpu_mem − Σ model_size` | — | none | yes |
| plan | `placement_plan` | descending-demand greedy, `chosen = best if (current_r − best_r) > τ else current` | — | none | — |
| cadence | `SCHEDULE_INTERVAL` | 5 s (`controller_global.py:395`) | — | — | — |

Answers to the four questions in §1 of the brief:
1. Is `decode_token_tput` measured from actual engine throughput? **Yes.**
2. Does co-locating three models reduce it? **Yes** — Phase 3 measured a
   1.32–1.65× decode slowdown at 3-model co-residency.
3. Does `weighted_token_rate` use that value directly? **Yes, unsmoothed.**
4. Can a migration alter the input used by the next cycle? **Yes** — through
   deactivation gating, the stale hold, and the inflated `elapsed_time` on the
   first post-migration report.

## 3. Reconstruction feasibility

**WEIGHTED_RATE_RECONSTRUCTION = EXACT.**

The greedy exposes, per line-8 row, `w_rate[current_gpu]` and `w_rate[best_gpu]`
as `ratio × shared_kv`, with `shared_kv` fully determined by the preceding rows'
`chosen_gpu` and the fixed `model_size` table. The recorded `kvpr` field adds one
equation per GPU over the actual placement. Solved as a linear system
(`solve_rates.py`):

| | cycles | solved | residual < 1e-6 | worst relative residual |
|---|---:|---:|---:|---:|
| all 8 runs | 677 | 677 | **677 (100%)** | 5.6 × 10⁻⁷ |

`input_rate` is recomputed independently from the per-request dumps (the same
30 s arrival-window sum the policy performs), and `decode_token_tput` follows by
subtraction. Sanity: the subtraction is materially negative in only 51 of 2708
rows (1.9%), which bounds the accuracy of the split.

Independent validation: an offline re-implementation of the greedy, fed the
reconstructed rates, reproduces the trace's own `placement_plan` in **602 of 677
cycles (88.9%)**. The residual 11% is attributable to the destination
availability filter, which the trace does not record and which the offline
version relaxes.

## 4. Endogenous share of the demand term

Share of |token_rate| carried by `decode_token_tput`:

| condition | arm | model_3 | model_4 | model_5 | model_6 |
|---|---|---:|---:|---:|---:|
| steady 8 s1 | cooldown30 | 36.5% | 40.0% | 34.5% | 34.3% |
| steady 8 s2 | cooldown30 | 32.9% | **49.6%** | 33.2% | 30.5% |
| steady 8 s2 | cooldown0 | 32.6% | **55.7%** | 34.0% | 35.1% |
| steady 10 s1 | cooldown30 | 36.1% | 45.8% | 33.8% | 33.3% |
| steady 10 s2 | cooldown0 | 35.1% | 34.1% | 32.7% | 34.9% |

A third to a half of what Algorithm 1 treats as demand is a quantity the
placement decision itself moves. `model_4` — the model that oscillates most — has
the highest endogenous share.

## 5. Reversal attribution

For each ping-pong pair the plan at the reversal cycle is recomputed three ways:
observed, exogenous-only (`input_rate@T2`, `decode@T1`), endogenous-only
(`input_rate@T1`, `decode@T2`). Whichever counterfactual still reverses the model
carries the flip.

| attribution | cooldown30 | cooldown0 | total | share |
|---|---:|---:|---:|---:|
| **ENDOGENOUS_SERVICE_FEEDBACK** | 10 | 28 | **38** | **40.4%** |
| EXOGENOUS_LOAD_CHANGE | 6 | 10 | 16 | 17.0% |
| MIXED (both alone suffice) | 2 | 11 | 13 | 13.8% |
| NEITHER_ALONE (needed both to move) | 1 | 17 | 18 | 19.1% |
| UNKNOWN (a cycle did not validate) | 7 | 2 | 9 | 9.6% |
| **total** | 26 | 68 | **94** | |

**Endogenous outnumbers exogenous 2.4 : 1**, and the ratio holds in both arms.
A further 19% required both terms to move together, i.e. the endogenous term was
necessary though not sufficient.

## 6. steady r8 s2, cooldown = 0 — all five `model_4` reversals

| t (s) | model_4 GPU | `input_rate` | **`decode_token_tput`** | `w_rate` | KVPR₀ | KVPR₁ | migration |
|---:|---:|---:|---:|---:|---:|---:|---|
| 78 | 1 | 403 | **0** | 0.372 | 0.0489 | 0.0817 | model_4 1→0 |
| 83 | 0 | 412 | **168** | 0.535 | 0.0597 | 0.0641 | |
| 88 | 0 | 378 | **1584** | **1.811** | **0.0870** | 0.0689 | **model_4 0→1** |
| 93 | 1 | 281 | **0** | 0.259 | 0.0621 | 0.0783 | **model_4 1→0** |
| 98 | 0 | 272 | **450** | 0.667 | 0.0653 | 0.0610 | |
| 103 | 0 | 246 | **1240** | **1.371** | **0.0856** | 0.0575 | **model_4 0→1** |
| 108 | 1 | 301 | **0** | 0.278 | 0.0653 | 0.0689 | |
| 113 | 1 | 299 | **−26** | 0.252 | 0.0535 | 0.0730 | **model_4 1→0** |
| 118 | 0 | 263 | **162** | 0.392 | 0.0673 | 0.0657 | |
| 123 | 0 | 374 | **1269** | **1.517** | **0.1069** | 0.0616 | **model_4 0→1** |
| 128 | 1 | 415 | **0** | 0.383 | 0.0923 | 0.0825 | |

The cycle is identical every time:

1. `model_4` lands on a GPU → deactivated during the move → **`decode_token_tput`
   reads 0** → its demand collapses to ~0.26–0.38.
2. It reactivates and resumes decoding → the value climbs 0 → ~170 → **~1300–1600**
   within two cycles → its demand rises **4–5×** to ~1.4–1.8.
3. The GPU holding it now shows the higher KVPR (0.0489 → 0.0870; 0.0575 → 0.1069)
   → the planner moves `model_4` away.
4. Step 1 again.

`input_rate` over the same window is 403, 412, 378, 281, 272, 246, 301, 299, 263,
374, 415 — it wanders ±40% with **no relationship to the migration direction**.
The −26 at t = 113 is the subtraction going slightly negative where decode ≈ 0,
which corroborates that reading rather than contradicting it.

**R8S2_MODEL4_REVERSALS_EXPLAINED = 5/5, by the same mechanism.**

## 7. Prior record and the τ connection

`design_analysis.md` §5a, written at project start:

> 90초 스모크 워크로드에서 `τ = 0.10` 으로 … 8회 마이그레이션이 발생했고, **그것들은
> 진동했다**: `model_4` 1→0, `model_1` 0→1, `model_5` 1→0, `model_4` 0→1, …
> 목적함수가 평평하므로 argmin 은 **추정 잡음**이 결정한다.
> 개선폭: 평균 +0.002 표준편차 0.175 … τ = 0.10 → 33 %, τ = 0.35 → 4 %

Same oscillation, same model, and a structural diagnosis: with similar rates the
objective is flat, so estimation noise decides the argmin. τ = 0.35 was the
mitigation.

The current evaluation runs **τ = 0.00035**, chosen later by a calibration on a
different workload (6-model, bursty r20). Measured now, the line-8 delta at each
emitted migration is a median **46× τ**. The damper is effectively off.

**This is reported as a finding, not a recommendation.** Phase 2 §8 found that in
that calibration lower τ produced *higher* goodput (corr(migrations, goodput) =
+0.48), and τ changes are out of scope here. The two observations are in tension
and neither settles the other.

## 8. Demand semantics

Full audit: `exp/analysis/planner_oscillation/demand_semantics_audit.md`.

`design_analysis.md` §5 records the paper's `token_rate` definition as
**"부분적" (partial)** — "newly admitted input tokens + decode tokens in flight".
It names the components but **does not state whether the decode component is
offered or achieved**.

**PAPER_DEMAND_SEMANTICS = UNKNOWN.** No inference is made.

One discrepancy is recorded as an observation: the design note describes our
decode term as an output rate over *the same 30 s window* as the input term; the
code instead uses `decode_token_tput`, an engine-reported ~1 s achieved rate
gated on activation. Intent is window-symmetric; implementation is not.

Using achieved throughput as demand is exactly the construction that admits a
feedback loop — contention slows service → measured demand falls → placement
moves → service changes → demand changes again. **Our implementation does have
that property**, and §6 shows it operating.

## 9. Hypothesis verdicts

| # | hypothesis | verdict | evidence |
|---|---|---|---|
| H1 | plan oscillation is real and independent of cooldown | **PROVEN** | plan changes/min 2.0–4.1 and lifetime p50 5.1–12.6 s are the same in both arms (Phase 5); reversals occur in both |
| H2 | oscillation is driven primarily by genuine workload demand | **DISPROVEN** | exogenous carries 16 of 94 reversals (17%); in §6 `input_rate` shows no relation to migration direction |
| H3 | oscillation is driven primarily by migration-induced service feedback | **STRONGLY_SUPPORTED** | 38 of 94 (2.4:1 over exogenous), plus 18 more needing it; §6 explains 5/5 reversals cycle by cycle |
| H4 | `decode_token_tput` is an endogenous placement-dependent input | **PROVEN** | source: achieved counter, activation-gated reporting, un-advanced `last_tput_update_time`, no ageing in the controller; measured 0↔1584 swings synchronised to migrations |
| H5 | the estimator semantics can create a feedback loop | **PROVEN** | the loop is closed and observed in §6; 30–56% of the demand term is endogenous |
| H6 | ping-pong is a symptom of planner instability, not an execution bug | **STRONGLY_SUPPORTED** | only 4 of 35 baseline migrations were swap completions (Phase 5); the planner reverses its own target every 5–13 s regardless of how it is actuated |
| H7 | the oscillation is consistent with the paper's intended Algorithm 1 semantics | **INCONCLUSIVE** | the reference does not specify offered vs achieved demand; the decision rule matches, the estimator's endogeneity is unspecified |

## 10. Assessment

**PRIMARY_OSCILLATION_CAUSE**: Algorithm 1's demand estimate is partly a
measurement of the service that the placement decision itself determines. A
migration silences and then spikes the migrated model's `decode_token_tput`,
which moves its weighted demand 4–5× within two decision cycles, which reverses
the placement. The objective is flat enough between similar models
(design_analysis §5a) that this term decides the argmin, and τ = 0.00035 is three
orders of magnitude too small to filter it.

**IMPLEMENTATION_BUG_EVIDENCE**: no defect in the decision rule. Two
implementation properties are load-bearing and were not deliberate design
choices for this purpose: (a) `last_tput_update_time` is advanced only while
activated, so the first post-migration report divides by an inflated interval;
(b) the controller never ages a stale `decode_token_tput`. Both make the
endogenous swing larger than the underlying throughput change. Neither was
changed here.

**POLICY_INSTABILITY_EVIDENCE**: the objective is flat across similar-rate models
(documented §5a, consistent with today's median delta of 0.016), so the argmin is
noise-decided; the plan lifetime (5–13 s) is shorter than the time to apply it.

**MISSING_TELEMETRY**: none for this question — the series was reconstructed
exactly. What is *not* recoverable is the split of `decode_token_tput` into its
count and elapsed-time components, which would show directly how much of the
post-migration dip is the inflated `elapsed_time` versus genuinely reduced
service.

## 11. Recommended next step

**LOGGING_ONLY_RUN_NEEDED = NO.** The reconstruction is exact, so the
instrumentation run contemplated in the brief is not required and is not
proposed.

The evidence now points at the estimator, not at the actuator. Before any change,
one further **offline** step is available at zero cost: replay Algorithm 1's
greedy over the reconstructed series with the endogenous term **held at its
pre-migration value** for the two cycles following each migration, and count how
many reversals disappear. That bounds the achievable stabilisation from
addressing the endogeneity alone, using only data already on disk, and it
discriminates between "the estimator is the problem" and "the objective is flat
regardless".

Changes that this analysis makes plausible but does **not** justify without that
bound — and none of which is proposed here — are: excluding a migrating model's
decode term while it is not activated; window-matching the decode term to the
input term as the design note describes; or restoring a τ that filters the noise.
Each is a policy change requiring separate approval.

I have stopped here and made no further changes.

---

## Addendum — Offline endogeneity replay (Phase 6b, zero GPU)

Pre-registered variant set (no parameter search). The migrated model's
`decode_token_tput` is held at its pre-migration value; everything else
(`input_rate`, objective, τ, greedy) is unchanged. Each of the 94 observed
reversals is re-decided. Script: `exp/analysis/planner_oscillation/replay_freeze.py`;
results `replay_freeze.csv`, `replay_suppressed_detail.csv`.

| variant | hold | suppressed | EXO 16 | ENDO 38 | MIXED 13 | NEITHER 18 | UNK 9 |
|---|---|---|---|---|---|---|---|
| primary | 2 cyc | **14/94 (14.9%)** | 0 (0%) | 7 (18.4%) | 4 (30.8%) | 2 (11.1%) | 1 |
| persistence | 3 cyc | 16/94 (17.0%) | 0 (0%) | 9 (23.7%) | 4 (30.8%) | 2 (11.1%) | 1 |
| drop_only | 2 cyc | 4/94 (4.3%) | 0 (0%) | 3 (7.9%) | 0 | 1 | 0 |

A fourth scope (freeze *all* models) was computed and **discarded as degenerate**:
100% of reversal cycles fall within K cycles of a migration, so it freezes every
model at every cycle and is a constant, not a counterfactual (identical 53/94 at
K=1..4).

**SPECIFICITY = CLEAN.** 0/16 exogenous reversals are suppressed. The
counterfactual is well-targeted, not over-aggressive; suppression is confined to
the classes attributed to the endogenous term.

**MAGNITUDE = MINORITY.** 80/94 reversals (85%) persist with the endogenous term
held constant. Extending the hold to 3 cycles adds only 2. Measurement
endogeneity is a **real but secondary** contributor, not the dominant cause.

**DROP vs SPIKE — the load-bearing finding.** Removing only the artificial
post-migration *drop* (`max(observed, pre)`) suppresses 4/94. Removing the whole
excursion suppresses 14/94. Therefore ~10 of the 14 come from the post-migration
**spike**, not the drop. The two defects named in §6 —
`last_tput_update_time` inflating elapsed, and un-aged stale values — both act on
the **drop** side. Fixing them alone predicts ≈4/94 (4%) fewer reversals.
Leverage lies in the **time-semantics mismatch** (≈1 s achieved throughput vs a
30 s input window), which governs the spike.

**VERDICT: evidence NOT sufficient to attribute the oscillation primarily to
measurement endogeneity.** It is confirmed as a genuine, specific, minority
mechanism. The residual 85% remains attributable to objective flatness and
plan-application dynamics (§5a, §7).

---

> **Correction (Phase 7b).** Where this document attributes the residual
> reversals to "objective flatness", that claim is **withdrawn as unproven**.
> The evidence here shows only that the estimator counterfactual is not a
> sufficient explanation. `../02_placement/P4HET_BAD_PLACEMENT_FORENSIC.md` §7 measures the
> objective directly and finds it is *not* flat in the conditions where the
> damage occurs (median margin +6.4 %), while being nearly perfectly flat in the
> conditions that behave well.
