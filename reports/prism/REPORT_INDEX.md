# Prism reproduction — report index

Single entry point. Everything authoritative is reachable from here.

## READ THESE FIRST

1. [`09_final_analysis/PRISM_FINAL_BASELINE_AND_REGIME_ANALYSIS.md`](09_final_analysis/PRISM_FINAL_BASELINE_AND_REGIME_ANALYSIS.md) — **integrated final analysis**
2. [`08_final_4het/FINAL_4HET_REPORT.md`](08_final_4het/FINAL_4HET_REPORT.md) — untouched hold-out result
3. [`07_many_model_prism_favorable/MANY_MODEL_PRISM_FAVORABLE_STRESS_REPORT.md`](07_many_model_prism_favorable/MANY_MODEL_PRISM_FAVORABLE_STRESS_REPORT.md) — deliberate best case
4. [`06_tau_calibration/P4HET_TAU_CALIBRATION_REPORT.md`](06_tau_calibration/P4HET_TAU_CALIBRATION_REPORT.md) — how τ was chosen
5. [`10_reproducibility/FRESH_SERVER_SELF_CHECK.md`](10_reproducibility/FRESH_SERVER_SELF_CHECK.md) — reproducibility guide
6. [`11_handoff/PRISM_SERVER_HANDOFF.md`](11_handoff/PRISM_SERVER_HANDOFF.md) — **new-server handoff**

## Headline result

| regime | Prototype | Prism | Δ | wins |
|---|---:|---:|---:|---:|
| Prism-favorable many-model (best case) | 2.2295 | 2.4048 | **+7.9 %** | 7/10 |
| Final 4-HET hold-out | 4.7686 | 3.5937 | **−24.6 %** | 0/20 |

`FINAL_TAU = 0.012859417696566448`, window 60 s, cooldown 30 s.
`PERFORMANCE_EVALUATION_CLOSED = true`.

Prism is **not** universally superior. Its memory-centric objective helps in a
high-consolidation, shifting-hot-set regime and hurts in the hold-out.

## 00_overview — project status and legacy handoff

| report | purpose | status |
|---|---|---|
| [`00_overview/FINAL_PROJECT_STATUS.md`](00_overview/FINAL_PROJECT_STATUS.md) | one-page answer to what was done, found and concluded | AUTHORITATIVE |
| [`00_overview/CURRENT_RESULTS_INDEX.md`](00_overview/CURRENT_RESULTS_INDEX.md) | legacy index from the pre-final phase | HISTORICAL |
| [`00_overview/FINAL_RESULTS_INDEX.md`](00_overview/FINAL_RESULTS_INDEX.md) | legacy index | HISTORICAL |
| [`00_overview/FINAL_BASELINE_HANDOFF.md`](00_overview/FINAL_BASELINE_HANDOFF.md) | legacy handoff, superseded by 11_handoff | HISTORICAL / SUPERSEDED |
| [`00_overview/HANDOVER.md`](00_overview/HANDOVER.md) | legacy operational notes | HISTORICAL |
| [`00_overview/RESUME_HERE.md`](00_overview/RESUME_HERE.md) | legacy resume notes | HISTORICAL |
| [`00_overview/RESUME_HERE_TP.md`](00_overview/RESUME_HERE_TP.md) | legacy TP resume notes | HISTORICAL |

## 01_environment

| report | purpose | status |
|---|---|---|
| [`../../exp/manifests/prism_final/ENVIRONMENT_MANIFEST.md`](../../exp/manifests/prism_final/ENVIRONMENT_MANIFEST.md) | hardware, OS, driver, CUDA, Python, PyTorch, topology, secrets by name | AUTHORITATIVE |

## 02_prototype_baseline — the original 4-HET paired evaluation (seeds 1/2)

| report | purpose | status |
|---|---|---|
| [`02_prototype_baseline/P4HET_REPORT.md`](02_prototype_baseline/P4HET_REPORT.md) | first Prototype vs Prism 40-run evaluation | HISTORICAL |
| [`02_prototype_baseline/P4HET_PHASE2_ANALYSIS.md`](02_prototype_baseline/P4HET_PHASE2_ANALYSIS.md) | causal analysis of that evaluation | HISTORICAL |
| [`02_prototype_baseline/P4HET_CAUSAL_ANALYSIS.md`](02_prototype_baseline/P4HET_CAUSAL_ANALYSIS.md) | first-pass cause analysis | HISTORICAL |

## 03_implementation — placement and migration behaviour

| report | purpose | status |
|---|---|---|
| [`03_implementation/P4HET_KVPR_PLACEMENT_QUALITY_ANALYSIS.md`](03_implementation/P4HET_KVPR_PLACEMENT_QUALITY_ANALYSIS.md) | **how often KVPR's optimum is compute-hostile** — the key mechanism result | AUTHORITATIVE |
| [`03_implementation/P4HET_BAD_PLACEMENT_FORENSIC.md`](03_implementation/P4HET_BAD_PLACEMENT_FORENSIC.md) | why the corrected planner stabilises a compute-hostile placement | AUTHORITATIVE |
| [`03_implementation/P4HET_ALG1_PLACEMENT_ANALYSIS.md`](03_implementation/P4HET_ALG1_PLACEMENT_ANALYSIS.md) | Algorithm 1 placement forensic | HISTORICAL |
| [`03_implementation/P4HET_PLANNER_OSCILLATION_ANALYSIS.md`](03_implementation/P4HET_PLANNER_OSCILLATION_ANALYSIS.md) | planner oscillation root cause | HISTORICAL |
| [`03_implementation/P4HET_MIGRATION_THRASH_ANALYSIS.md`](03_implementation/P4HET_MIGRATION_THRASH_ANALYSIS.md) | migration thrashing analysis | HISTORICAL |
| [`03_implementation/P4HET_SWAP_PATHOLOGY_BOUND.md`](03_implementation/P4HET_SWAP_PATHOLOGY_BOUND.md) | achievable-gain bound and cooldown mechanism test | HISTORICAL |
| [`03_implementation/P4HET_COOLDOWN_DIAGNOSTIC.md`](03_implementation/P4HET_COOLDOWN_DIAGNOSTIC.md) | cooldown ablation | HISTORICAL |
| [`03_implementation/P4HET_OVERLAP_DIAGNOSTIC.md`](03_implementation/P4HET_OVERLAP_DIAGNOSTIC.md) | overlap-migration diagnostic | HISTORICAL |
| [`03_implementation/VERSION_FEATURE_REGRESSION_MATRIX.md`](03_implementation/VERSION_FEATURE_REGRESSION_MATRIX.md) | which patch layer introduced which feature | REFERENCE |

## 04_correctness — lifecycle correctness

| report | purpose | status |
|---|---|---|
| [`04_correctness/P4HET_MIGRATION_LIFECYCLE_FAILURE_CONTAINMENT_REPORT.md`](04_correctness/P4HET_MIGRATION_LIFECYCLE_FAILURE_CONTAINMENT_REPORT.md) | **the containment fix**, verified | AUTHORITATIVE |
| [`04_correctness/P4HET_MIGRATION_LIFECYCLE_STALL_FORENSIC.md`](04_correctness/P4HET_MIGRATION_LIFECYCLE_STALL_FORENSIC.md) | the 601.8 s stall forensic | AUTHORITATIVE |
| [`04_correctness/P4HET_MIGRATION_ROLLBACK_CORRECTION_REPORT.md`](04_correctness/P4HET_MIGRATION_ROLLBACK_CORRECTION_REPORT.md) | first attribution, corrected — ROLLBACK_PATH_BUG = DISPROVEN | HISTORICAL / CORRECTED |

## 05_estimator_validation

| report | purpose | status |
|---|---|---|
| [`05_estimator_validation/P4HET_ESTIMATOR_CORRECTION_REPORT.md`](05_estimator_validation/P4HET_ESTIMATOR_CORRECTION_REPORT.md) | token-rate estimator fidelity correction, FIX VERIFIED | AUTHORITATIVE |
| [`05_estimator_validation/P4HET_WINDOW_CALIBRATION_REPORT.md`](05_estimator_validation/P4HET_WINDOW_CALIBRATION_REPORT.md) | 30 s vs 60 s window study; 60 s adopted for paper fidelity | AUTHORITATIVE |

## 06_tau_calibration

| report | purpose | status |
|---|---|---|
| [`06_tau_calibration/P4HET_TAU_CALIBRATION_REPORT.md`](06_tau_calibration/P4HET_TAU_CALIBRATION_REPORT.md) | 48 runs, pre-declared selection rule, FINAL_TAU | AUTHORITATIVE |

## 07_many_model_prism_favorable

| report | purpose | status |
|---|---|---|
| [`07_many_model_prism_favorable/MANY_MODEL_PRISM_FAVORABLE_STRESS_REPORT.md`](07_many_model_prism_favorable/MANY_MODEL_PRISM_FAVORABLE_STRESS_REPORT.md) | 20 runs, deliberate best case, +7.9 % | AUTHORITATIVE |
| [`07_many_model_prism_favorable/PILOT_PROTOTYPE_ARM_GATE_MISCLASSIFICATION.md`](07_many_model_prism_favorable/PILOT_PROTOTYPE_ARM_GATE_MISCLASSIFICATION.md) | harness label defect and its recovery | SUPPORTING |
| [`07_many_model_prism_favorable/MANY_MODEL_PROTOTYPE_PILOT_FORENSIC.md`](07_many_model_prism_favorable/MANY_MODEL_PROTOTYPE_PILOT_FORENSIC.md) | independent forensic of the same pilot incident (Korean) | SUPPORTING |

## 08_final_4het

| report | purpose | status |
|---|---|---|
| [`08_final_4het/FINAL_4HET_REPORT.md`](08_final_4het/FINAL_4HET_REPORT.md) | 40 runs on hold-out seeds 5/6, −24.6 % | AUTHORITATIVE |

## 09_final_analysis

| report | purpose | status |
|---|---|---|
| [`09_final_analysis/PRISM_FINAL_BASELINE_AND_REGIME_ANALYSIS.md`](09_final_analysis/PRISM_FINAL_BASELINE_AND_REGIME_ANALYSIS.md) | integrated analysis across both regimes | AUTHORITATIVE |
| [`09_final_analysis/FINAL_RESULT_AUDIT.md`](09_final_analysis/FINAL_RESULT_AUDIT.md) | offline audit of all 108 authoritative runs | AUTHORITATIVE |

## 10_reproducibility

| report | purpose | status |
|---|---|---|
| [`10_reproducibility/FRESH_SERVER_SELF_CHECK.md`](10_reproducibility/FRESH_SERVER_SELF_CHECK.md) | clean-shell validation of the repro package | AUTHORITATIVE |
| [`10_reproducibility/FINAL_REPRO_VERIFICATION.md`](10_reproducibility/FINAL_REPRO_VERIFICATION.md) | verification run from the committed state | AUTHORITATIVE |

## 11_handoff

| report | purpose | status |
|---|---|---|
| [`11_handoff/PRISM_SERVER_HANDOFF.md`](11_handoff/PRISM_SERVER_HANDOFF.md) | everything a new researcher needs on a new server | AUTHORITATIVE |

## Data and manifests

| path | contents |
|---|---|
| [`../../exp/analysis/final_summary/`](../../exp/analysis/final_summary/) | all final table/figure CSVs — see its `README.md` for columns, units and formulas |
| [`../../exp/analysis/final_4het/`](../../exp/analysis/final_4het/) | per-run and paired 4-HET tables |
| [`../../exp/analysis/many_model_prism_favorable/`](../../exp/analysis/many_model_prism_favorable/) | per-run and paired many-model tables |
| [`../../exp/analysis/tau_calibration/`](../../exp/analysis/tau_calibration/) | τ candidates, Δr distribution, regret, trade-off |
| [`../../exp/analysis/correctness_fix/`](../../exp/analysis/correctness_fix/) | fix verdicts, regressions, lifecycle audit |
| [`../../exp/manifests/prism_final/`](../../exp/manifests/prism_final/) | frozen baseline, protocols, trace/dataset/model/source/environment manifests, artifact classification, raw inventory |
| [`../../repro/prism_final/`](../../repro/prism_final/) | bootstrap, verification, smoke test, resume |

## Raw results

| experiment | root | runs |
|---|---|---|
| τ calibration | `exp/results/4het-tau-final/` | 48 |
| many-model final | `exp/results/many-model-final/` | 20 |
| final 4-HET | `exp/results/4het-final/` | 40 |
| many-model pilot | `exp/results/many-model-pilot/` | 2 (diagnostic) |

Raw artifacts stay on the server; `exp/manifests/prism_final/RAW_ARTIFACT_INVENTORY.csv`
lists every path, size, classification and verification status (6.61 GB total).
