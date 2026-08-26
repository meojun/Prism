# Phase 7 — Paper-aligned token-rate estimator correction

Offline audit, minimal estimator change, unit tests, and a 4-run Prism-only
mechanism test. No change to Algorithm 1, KVPR, τ, c_i, SLO, migration policy,
cooldown, window length, or workload. Original results untouched.

Artifacts: `exp/analysis/estimator_correction/`.

---

## 1. Paper-fidelity audit

The paper was **not in this repo**; prior phases worked from a second-hand
summary, which `docs/handoff/TP_FINAL_HANDOFF_PROMPT.md:24` records as having
already caused two corrections. The OSDI'26 camera-ready was retrieved
(sha256 `b2af6f908a07adbb…`, extracted to `paper_osdi26_extracted.txt`).

**The arXiv version (2505.04021v2) has no Appendix A.4** — its appendix ends at
A.2. That is almost certainly the source of our documentation error below.

### Verbatim, camera-ready

§4: *"By counting both input tokens from newly admitted requests and decode
tokens produced by running requests per unit time, `token_rate` captures the
full KV-cache growth rate."*

Appendix A.4, *Load Monitoring Window Size*: *"Figure 15(b) examines the impact
of **the sliding window size used to calculate the moving average of token rates
for the KVPR** … We observe that **a window size of approximately 60 seconds
provides a stable estimation of memory pressure**."*

τ: *"proceeds only if the improvement exceeds a threshold τ"* — **no numeric
value**. Our documentation is correct on this point.

### Correction to our own documentation

`design_analysis.md:154–155` record the token-rate measurement window and the
smoothing method as **"명시 없음"**. Against the camera-ready both are wrong:
A.4 specifies a sliding-window moving average and reports ~60 s. The 30 s choice
was made for a defensible reason (matching the prototype's tracker so window
length is not a confound between arms) but was believed unconstrained when it
was not.

### Audit table

Full table in `paper_fidelity_audit.md`. The load-bearing rows:

| field | paper | current code (before) | after |
|---|---|---|---|
| input token rate | input tokens of newly admitted reqs / unit time | prompt tokens within `rate_window`, ÷ window | unchanged |
| decode token rate | decode tokens of running reqs / unit time | cached scalar `decode_token_tput` — achieved throughput over a ~1 s engine interval | decode tokens reported within `rate_window`, ÷ window |
| sliding window | A.4: moving average over a sliding window | **input only** | both components, one window |
| during migration/deactivation | not stated | report gated on `_activated` → controller keeps last value | no reports → tokens age out → decays to 0 |
| stale value | not stated | persists indefinitely, never aged | impossible by construction |
| elapsed accounting | not stated | `last_tput_update_time` advanced only inside the `_activated` branch | advances every second regardless |

Everything marked "not stated" is genuinely absent from the paper and is an
implementation choice, not a fidelity claim.

---

## 2. Source diff

`estimator.patch` — **4 hunks, 36 lines added, 2 removed**, 4 files:

- `model_queue_tracker.py` — timestamped decode-token events + windowed rate
- `controller_global.py` — record the count that `UpdateModelTput` **already
  carried and the controller discarded**
- `kvpr_global.py` — the single line Algorithm 1 reads for the decode component
- `scheduler.py` — the timer gate, so elapsed accounting does not stall

Algorithm 1's formula, model ordering, destination selection, τ, cooldown,
overlap, memory reserve and one-migration-per-cycle are byte-identical.

Runtime hash: freeze `6618671` + this patch = `2b5430c1b04b21e9…`, gated before
every run.

---

## 3. Unit tests — 10/10 pass

`test_estimator.py`, covering A (steady production), B (idle expiration and
no-stale-value), C (migration pause), D (rate independent of reporting cadence),
E (input+decode sum exactly), F (window-boundary expiry). Two initial failures
were errors in the test arithmetic, not the estimator.

---

## 4. Offline replay of the new estimator — NOT POSSIBLE

The new estimator needs decode-token **counts** at report granularity. The run
logs contain **zero** occurrences of `decode_token_count`, `UpdateModelTput`, or
any per-second throughput record — only 5 s cycle traces. Phase 6 recovered the
rate *scalar* exactly but could never recover its count/elapsed split. Producing
the counts would require fabricating token timestamps, so no replay was run and
no approximation was substituted.

---

## 5. Four-run mechanism test

Prism only, steady r8/r10 × seed 1/2, canonical traces hash-verified, τ=0.00035,
window 30 s, cooldown 30 s, all other flags frozen. **4/4 PASS**, 0 aborted,
0 Algorithm-2 order violations.

### Primary — planner mechanism (pooled)

| metric | OLD | NEW | change |
|---|---:|---:|---:|
| plan changes/min (median) | 3.005 | 1.450 | **−51.7%** |
| median plan lifetime (s) | 9.21 | 18.05 | **+96.0%** |
| plan reversals | 26 | 20 | **−23.1%** |
| ping-pong migrations | 26 | 20 | **−23.1%** |
| migrations total | 35 | 29 | **−17.1%** |
| 3+1 exposure | 30.7% | 26.0% | **−4.7 pp** |

**Every primary endpoint moved in the predicted direction.** The estimator
correction does what it was designed to do: the planner is markedly more stable.

### Secondary — downstream (pooled medians)

| metric | OLD | NEW | change |
|---|---:|---:|---:|
| Qwen2.5-7B TPOT p50 (ms) | 36.5 | 44.2 | +21.0% |
| Qwen2.5-7B TPOT p95 (ms) | 81.9 | 108.5 | +32.5% |
| Qwen2.5-7B ITL p99 (ms) | 228.2 | 283.6 | +24.3% |
| aggregate TPOT mean (ms) | 36.8 | 39.7 | +7.8% |
| TTFT p99 (s) | 15.7 | 17.6 | +12.4% |
| joint-SLO goodput (req/s) | 4.448 | 4.567 | +2.7% |
| joint-SLO attainment | 0.462 | 0.497 | +7.6% |
| throughput (req/s) | 8.72 | 8.62 | −1.1% |

**The predicted downstream chain did not follow.** Latency got worse while
goodput/attainment moved slightly up. The pooled numbers hide the real structure.

---

## 6. The actual result: a clean 4/4 dose–response

| condition | migrations | goodput | large-model co-residency | verdict |
|---|---|---|---|---|
| steady r8 s2 | 10 → **6** | 4.62 → **5.50** (+19%) | 13.3% → **4.7%** | better |
| steady r10 s2 | 6 → **4** | 4.67 → **5.31** (+14%) | 3.5% → **0.0%** | better |
| steady r8 s1 | 9 → 9 | 3.34 → 3.15 (−5%) | 39.1% → **77.3%** | worse |
| steady r10 s1 | 10 → 10 | 4.28 → 3.82 (−11%) | 40.2% → **65.9%** | worse |

"Large-model co-residency" is the share of run time with Llama-3.1-8B **and**
Qwen2.5-7B on the same GPU (`large_model_coresidency.csv`).

The correlation is perfect across all four conditions and identifies the
mechanism unambiguously:

> The estimator correction makes the planner **hold whatever plan it picks**.
> It does not change **which** plan it picks. Where the settled plan separates
> the two large models, holding it is a large win. Where the settled plan puts
> them together, holding it is sustained harm.

The r8 s1 tail is **not a transient stall**: 727 requests exceeded 30 s TTFT
(OLD: 11), spread across the whole run (64–420 s) and confined to `model_5` and
`model_6` — the two large models, starving each other while co-resident. Plan
lifetime p95 rose from 46.8 s to 145.3 s in that condition: the planner sat in
the bad placement roughly three times as long.

This is exactly the residual Phase 6 measured and could not remove: with the
endogenous term held constant, **80/94 reversals persisted**, attributable to
objective flatness. Phase 7 confirms that from the other side — fixing the
measurement stabilises the planner without improving its choice.

---

## 7. Fidelity verdict

```
ALG1_OBJECTIVE_FIDELITY            = UNCHANGED (formula, ordering, selection, tau byte-identical)
TOKEN_RATE_DEFINITION_FIDELITY     = IMPROVED -> FAITHFUL on the stated semantics
                                     (both input and decode counted per unit time
                                      over one common window, per §4)
WINDOW_SEMANTICS_FIDELITY          = IMPROVED, NOT YET FAITHFUL
                                     (sliding-window moving average now applies to
                                      BOTH components, per A.4; but the window is
                                      30 s and A.4 reports ~60 s as stable.
                                      Deliberately not changed here.)
MIGRATION_MEASUREMENT_ARTIFACT_FIXED = YES
                                     (gated reporting, stalled elapsed accounting,
                                      and un-aged stale values all eliminated;
                                      verified by unit tests C and D)
```

The implementation is **not** called paper-faithful overall: the window length
still differs from the only value the paper reports.

---

## 8. Recommendation

**Do not adopt or reject on these 4 runs.** The change is correct on fidelity
grounds and does what it claims mechanically, but its downstream sign is
determined by a second, unfixed defect — the flat objective's choice of plan.
Adopting it now would ship a variance amplifier: better when the objective is
right, worse when it is wrong.

Two things must happen before the full 4-HET re-evaluation, and the order
matters:

1. **Window calibration (A.4 is the prior).** The 30 s window was chosen when we
   believed the paper was silent. It is not. Whether ~60 s changes the
   co-residency outcome is now a first-class question, not a tuning knob.
2. **τ recalibration on the corrected estimator.** The line-8 delta distribution
   is necessarily different now; τ=0.00035 has no calibrated standing against
   this estimator.

Both must use **calibration seeds distinct from the evaluation seeds**. The
current 4-HET canonical set contains only seeds 1 and 2, so new traces must be
generated — otherwise τ and the window would be selected and validated on the
same data.

Open question this phase did not answer: **why** the corrected estimator prefers
co-locating the two large models in the s1 conditions. A windowed decode term is
smoother and lags a reactivating model, which would systematically understate a
large model's demand right after it moves — but that is a hypothesis, and no
test in this phase discriminates it.

---

> **Correction (Phase 7b).** Where this document attributes the residual
> reversals to "objective flatness", that claim is **withdrawn as unproven**.
> The evidence here shows only that the estimator counterfactual is not a
> sufficient explanation. `../02_placement/P4HET_BAD_PLACEMENT_FORENSIC.md` §7 measures the
> objective directly and finds it is *not* flat in the conditions where the
> damage occurs (median margin +6.4 %), while being nearly perfectly flat in the
> conditions that behave well.
