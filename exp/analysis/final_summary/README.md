# Final summary CSVs

Every final table and figure can be built from these files alone. No raw log
needs to be reparsed. Numbers must be loaded from here, never hard-coded into a
plotting script.

Pairing rule everywhere: a *paired* delta compares Prototype and Prism on the
**same trace file** (byte-identical, SHA-verified), so deltas are within-condition.
Aggregations are unweighted means over the conditions named in each file.

---

## `final_headline_metrics.csv`
One row per regime. `regime` ∈ {prism_favorable_many_model, final_4het_overall,
final_4het_steady, final_4het_bursty}.

| column | meaning | units |
|---|---|---|
| `prototype_goodput_mean`, `prism_goodput_mean` | mean Joint-SLO goodput over all conditions in the regime | req/s |
| `delta_pct_of_means` | `100·(prism_mean − proto_mean)/proto_mean` | % |
| `mean_paired_delta_pct` | mean of the per-condition paired deltas — differs slightly from the line above because it weights conditions equally rather than by magnitude | % |
| `conditions` | number of paired conditions | count |
| `prism_wins` | conditions with a positive paired delta | count |

## `prism_favorable_by_rate.csv`, `final_4het_by_rate.csv`
One row per (workload, rate), averaged over the two seeds.
`delta_pct_mean` is the mean of the two paired deltas.
`*_ttft_ok`, `*_tpot_ok` are the percentage of successful requests meeting that
SLO individually. `prism_migrations_mean` is migrations executed per run.

## `latency_headlines.csv`
Raw latency, which is primary evidence — SLO thresholds are experiment-defined.
One row per (regime, workload, arm).
`ttft_p50_s`/`p95`/`p99` in **seconds**; `tpot_p50_ms`/`p95`/`p99` in
**milliseconds**; `joint_attainment` is a fraction in [0,1].
Percentiles are computed per run over successful requests, then averaged across
runs (not pooled across runs).

## `migration_headlines.csv`
One row per (regime, workload, arm). `migrations_mean` = migrations executed per
run; `migration_gb_mean` = (weight bytes + KV bytes) / 2^30 per run. The
Prototype is static and always 0.

## `mechanism_evidence.csv`
Evidence for the load-band and objective-mismatch hypotheses.
Many-model rows carry `gb_moved`, `tpot_p50_*_ms`, `ttft_p99_*_s` per rate.
Final-4HET rows carry `large_large_residency_pct` — the share of controller
cycles in which Llama-3.1-8B and Qwen2.5-7B were resident on the same GPU,
measured from each run's own `[PAPER-ALG1-V4]` cycle traces.

## `resource_headlines.csv`
τ candidates. `regret_tw_mean` = time-weighted mean residual KVPR regret
(`max(0, current_peak_KVPR − best_peak_KVPR)`, dimensionless);
`migration_gb_per_min`; `tradeoff_score = sqrt(A_norm² + B_norm²)` over finite
candidates only. T5 (migration disabled) is excluded from selection by rule.

## `regime_comparison.csv`
Side-by-side of the two evaluation regimes, one row per dimension.

## `hypothesis_verdicts.csv`
`scope` ∈ {many_model, tau}; verdicts are SUPPORTED / PARTIALLY_SUPPORTED /
SUPPORTED_NON_MONOTONE / DESCRIPTIVE_ONLY.

## `reproducibility_manifest.csv`
Key/value identity of the frozen baseline: commits, tree hash, τ, window,
cooldown, hardware, driver, Python, PyTorch.

## `FINAL_RESULT_AUDIT.csv`
One row per authoritative run with every gate outcome. `status` is PASS/FAIL and
`problems` is empty on PASS.

---

## Exclusions applied to every aggregate here

- many-model **r12/r16/r20** — removed by the rate-grid amendment, preserved on
  disk, never aggregated
- the **r16 seed 9 pilot** — diagnostic only
- all `KNOWN_INVALID_PRESERVED` attempts
- historical seeds 1/2 (4-HET paired), which are a different evaluation

## Caution

Two seeds per condition. No population-level statistical significance is claimed
anywhere, and none should be computed from these files without saying so.
