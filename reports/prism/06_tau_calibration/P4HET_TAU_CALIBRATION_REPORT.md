# τ calibration on the corrected paper-faithful baseline

48 runs on calibration seeds 3/4, corrected estimator, 60 s window, cooldown 30 s.
τ selected by the pre-declared mechanistic rule alone; goodput was used for
neither selection nor tie-breaking.

```
TAU_STATUS = FINALIZED
FINAL_TAU  = 0.012859417696566448      (candidate T2 = pooled positive-Δr P50)
VALID_RUNS = 48/48    lifecycle gate 48/48 PASS
```

---

## 1. Why τ had to be recalibrated

τ = 0.00035 was chosen under the **old** estimator semantics and a different
workload regime. The estimator correction changed what `weighted_token_rate`
means, so the distribution of the line-8 quantity τ gates — `Δr = current_peak_KVPR
− best_peak_KVPR` — is not the same distribution τ was calibrated against. The
historical value had no calibrated standing and was carried forward only as a
reference candidate.

---

## 2. Stage A — the Δr scale, measured offline first

Measured on the frozen configuration before any candidate existed, so the
candidate set could not be chosen to suit a result.

| stratum | cycles | frac Δr>0 | p25 | p50 | p75 | p90 |
|---|---:|---:|---:|---:|---:|---:|
| steady aggregate | 344 | 0.073 | 0.00091 | 0.003569 | 0.007712 | 0.010298 |
| bursty aggregate | 343 | **0.528** | 0.00823 | 0.015214 | 0.027358 | 0.040051 |
| **POOLED** | 687 | 0.300 | 0.00574 | **0.012859** | **0.025950** | **0.039163** |

The historical τ = 0.00035 sits **below even the pooled p25**. 93 % of all
positive-Δr cycles exceed it: under the corrected estimator it was very nearly a
no-op gate.

Bursty produces positive Δr in 52.8 % of cycles versus 7.3 % in steady, so τ
gates an order of magnitude more decisions per unit time in bursty.

---

## 3. Frozen candidate set

Written to `TAU_CANDIDATES_FROZEN.json` **before any τ run started**, at full
precision, with no rounding:

| id | τ | derivation |
|---|---|---|
| T0 | 0.0 | maximum sensitivity |
| T1 | 0.00035 | historical reference only |
| **T2** | **0.012859417696566448** | pooled positive-Δr **P50** |
| T3 | 0.02595037368602512 | pooled positive-Δr P75 |
| T4 | 0.03916286292302117 | pooled positive-Δr P90 |
| T5 | 1e9 | migration-disabled control, **not selectable** |

---

## 4. Results

`A` = aggregate time-weighted mean residual KVPR regret (lower better).
`B` = migration GB/min (lower better). Byte accounting was complete, so the
migrations/min fallback was not used.

| id | τ | A (regret) | B (GB/min) | A_norm | B_norm | **SCORE** | goodput | large-large |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| T0 | 0 | 0.006348 | 9.376 | 0.143 | 1.000 | 1.0101 | 4.4284 | 41.9 % |
| T1 | 0.00035 | 0.005779 | 8.617 | 0.065 | 0.792 | 0.7946 | 4.6316 | 42.9 % |
| **T2** | **0.012859** | **0.005301** | 6.955 | **0.000** | 0.336 | **0.3364** | 5.1332 | 37.5 % |
| T3 | 0.025950 | 0.007444 | 6.671 | 0.292 | 0.258 | 0.3898 | 4.7739 | 38.6 % |
| T4 | 0.039163 | 0.012648 | 5.728 | 1.000 | 0.000 | 1.0000 | 4.5195 | 37.5 % |
| T5 | ∞ | 0.013937 | 0.000 | — | — | excluded | 5.4179 | 0.0 % |

`TRADEOFF_SCORE = sqrt(A_norm² + B_norm²)` over finite candidates; T5 excluded
from selection by rule. T2 wins at 0.3364 against T3 at 0.3898 — a 15.9 %
relative gap, so the 5 % tie rule was **not** triggered and no tie-break was
needed.

### The trade-off is not monotone, and that is the substantive finding

The expected picture — raise τ, migrate less, accumulate more residual regret —
holds only from T2 upward. Below T2 it inverts:

```
T0 → T1 → T2 :  regret 0.006348 → 0.005779 → 0.005301   (falling)
                cost   9.376    → 8.617    → 6.955      (falling)
T2 → T3 → T4 :  regret 0.005301 → 0.007444 → 0.012648   (rising)
                cost   6.955    → 6.671    → 5.728      (falling)
```

Between T0 and T2, migrating **less** produced **less** residual KVPR regret.
The extra migrations at low τ were not buying placement quality; they were
churn. T2 is not a compromise point — it is simultaneously the regret minimum
and cheaper than everything below it.

T5 confirms the other end: with migration disabled the residual regret is the
worst of all six candidates (0.013937), so static placement is not the answer
either.

---

## 5. Was the historical τ = 0.00035 wrong?

**TOO_PERMISSIVE.** It is beaten by T2 on *both* objectives — higher regret
(0.005779 vs 0.005301) *and* higher migration cost (8.617 vs 6.955 GB/min). It
is not a defensible operating point under the corrected estimator, and it was
kept only because it was the historical value.

---

## 6. Hypothesis verdicts

| | verdict | evidence |
|---|---|---|
| **H1** τ controls migration cost | **SUPPORTED** | GB/min falls monotonically across all six candidates: 9.376 → 8.617 → 6.955 → 6.671 → 5.728 → 0.000 |
| **H2** τ creates a regret/cost trade-off | **PARTIALLY_SUPPORTED** | the trade-off exists only above T2; below it, lower cost and lower regret coincide |
| **H3** low τ causes excess churn | **SUPPORTED** | T0 has the highest cost and higher regret than T2 — the extra migrations buy nothing |
| **H4** high τ collapses adaptation | **SUPPORTED** | T5 has the worst regret of all candidates; T4 already trends that way |
| **H5** τ effect is regime-dependent | **SUPPORTED** | positive Δr in 52.8 % of bursty cycles vs 7.3 % of steady |
| **H6** goodput optimum matches mechanistic τ | **DESCRIPTIVE ONLY** | T2 also happens to have the highest goodput among finite candidates (5.1332), but goodput was used for neither selection nor tie-breaking. The agreement is reported, not relied upon. |

Note on H6: T5 (migration disabled) shows the highest goodput of all (5.4179).
That is exactly why goodput is not a selection criterion — optimising for it
here would have selected "Prism with migration turned off", which is not a
dynamic Prism configuration at all.

---

## 7. Validity

48/48 runs: rc = 0, verdict PASS, 0 aborted, 0 Algorithm-2 order violations,
lifecycle validity gate 48/48 PASS.

Two runs failed on first attempt and were retried once each under section 64, as
conclusively diagnosed harness-only failures. Both failed attempts are preserved
and excluded:

- `prism-T0/steady/rate_8/seed_3.eaddrinuse-attempt1` — `nccl_port` TOCTOU race,
  server never started (see `exp/results/many-model-final/KNOWN_RUNTIME_RACE_nccl_port.md`)
- `prism-T4/steady/rate_8/seed_3.failed-attempt-20260825T184350Z` — same class

Neither retry changed the trace, seed, rate, τ, window, cooldown or runtime.

---

## 8. Frozen outcome

```
FINAL_TAU = 0.012859417696566448
KVPR_WINDOW = 60
COOLDOWN = 30
FINAL_BASELINE_FROZEN = YES
```

From this point no baseline parameter changed for any reason, and no final
evaluation result was used to revisit it.

Machine-readable: `exp/analysis/tau_calibration/` —
`run_manifest.csv`, `candidate_summary.csv`, `delta_distribution.csv`,
`kvpr_regret.csv`, `migration_cost.csv`, `migration_quality.csv`,
`planner_stability.csv`, `placement_composition.csv`, `latency_summary.csv`,
`tau_tradeoff.csv`, `hypothesis_verdicts.csv`, `TAU_CANDIDATES_FROZEN.json`,
`FINAL_TAU.json`.
