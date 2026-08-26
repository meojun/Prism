# Prism-favorable many-model protocol — FROZEN

**Regime name: `PRISM_FAVORABLE_STRESS_REGIME`.** This workload is *deliberately
constructed to favour Prism*. It is a best-case, applicability-upper-bound
experiment, not a representative production distribution. If Prism wins here that
is not evidence of universal superiority. If Prism loses even here, that is
reported prominently.

| | |
|---|---|
| models | six-model historical set (`model_1` … `model_6`) |
| hot sets | A = model_5+model_1, B = model_6+model_2, C = model_3+model_4 |
| phases | A → B → C, **180 s each**, trace 540 s |
| skew | **90 %** on the phase's hot pair (45 % each), 10 % across the other four (2.5 % each) |
| arrivals | canonical historical six-model bursty generator, unchanged |
| rates | 4, 8, 12, 16, 20 |
| final seeds | 7, 8 |
| arms | Prototype, final Prism |
| final runs | **20** |
| pilot | r16 seed 9, 2 runs, **diagnostic only** |

180 s is chosen so the 60 s estimator can observe the new phase, Algorithm 1 can
respond, a migration can complete, and there is still time to amortise its cost.
Frozen before the pilot so no pilot result can change it.
