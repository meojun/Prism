# Token-rate monitoring window calibration — 30 s vs 60 s

Controlled paired experiment. Estimator semantics, Algorithm 1, τ, cooldown,
SLO, models and hardware frozen; the only difference between paired arms is the
configured sliding-window length. No τ recalibration, no final 4-HET, no
many-model, no Prototype rerun, no push.

Artifacts: `exp/analysis/window_calibration/`.

```
ESTIMATOR_FIX_STATUS   = FIX VERIFIED
WINDOW_STATUS          = WINDOW_SEMANTICS_VERIFIED_BUT_EFFECT_MIXED
E2E_IMPROVEMENT_STATUS = NOT VERIFIED
FINAL_BASELINE_STATUS  = NOT VERIFIED
```

---

## 1. Executive summary

**The implementation is verified correct at both settings. The camera-ready's
stabilization rationale does not reproduce in this regime, and the window effect
is sharply regime-dependent with opposite signs.**

Offline Algorithm 1 replay agrees with the recorded plan in **100 % of cycles in
both arms** — the strongest replay coverage of any phase in this project. There
is no correctness or semantics pathology.

But the pre-registered primary measure of A.4's claim fails:

| primary measure | 30 s | 60 s | delta |
|---|---:|---:|---:|
| **rank-1 switches/min** (median of 8) | 0.559 | **0.698** | **+0.139** |
| median rank-1 lifetime (s) | 74.9 | 79.3 | +4.4 |
| median plan lifetime (s) | 26.5 | **13.9** | **−12.6** |

Rank-1 switching did **not** fall (paired: down 3/8, up 2/8, unchanged 3/8), and
plan lifetime roughly **halved**. A longer window did not produce a more stable
planner here.

The effect splits cleanly by regime, in both mechanism and outcome:

| | steady | bursty |
|---|---|---|
| plan changes/min | **down 4/4** | **up 3/4** |
| median plan lifetime | up 3/4 | **collapses** (30→15, 43→13, 43→10 s) |
| goodput delta (median) | **+0.260**, up 3/4 | **−0.799**, up **0/4** |
| agg TPOT p99 | improves | degrades (1106→**1668 ms** at r10 s4) |

This is the stability/responsiveness trade-off the task anticipated. At 60 s the
estimator is **stale relative to bursty active-set shifts**: it tracks phase
transitions too slowly, so the planner acts on out-of-date demand, replans more
often, and holds each plan for less time.

The pre-declared causal path — window → token-rate smoothing → rank-1 identity →
KVPR-optimal composition — **is** visible and traceable, but it pushes the
*wrong way*: 60 s raises small-model rank-1 share (median 26.6 % → 33.9 %) and
raises large-large KVPR-optimality (median 24.8 % → 31.0 %, up in 5/8 conditions
and **never down**). Longer smoothing makes the compute-hostile composition
*more* attractive, not less.

**60 s is therefore not vindicated by behaviour. It remains the only value the
camera-ready supports.** The verdict is B: semantics verified, effect mixed.

---

## 2. Paper fidelity basis

OSDI'26 camera-ready §4 defines `token_rate` as input tokens of newly admitted
requests **plus** decode tokens of running requests, per unit time. Appendix A.4
*Load Monitoring Window Size* states KVPR consumes "the sliding window size used
to calculate the moving average of token rates" and reports **~60 seconds** as
providing "a stable estimation of memory pressure ... while remaining responsive
enough to shift resources during sustained workload changes."

A.4's result is drawn from Hyperbolic and Chatbot Arena production traces. Our
bursty workload uses 30–90 s synthetic phase transitions, which are sharper than
those traces. A.4's optimum need not transfer, and this experiment shows it does
not — that is a finding about our regime, not a contradiction of the paper.

30 s is the historical implementation point, chosen when `design_analysis.md`
believed the window was unspecified (corrected in Phase 7).

---

## 3. Experimental freeze

| | |
|---|---|
| source hash | `2b5430c1b04b21e9623e1d8271c7edf806eee186a434248dd7586535fb6e3a41` — freeze `6618671` + estimator correction, verified before the matrix |
| runtime code change | **none** — `KVPR_WINDOW` was already an env var (`run_v4_case.sh:72`), and the corrected estimator drives **both** input and decode from the same `rate_window` |
| τ | 0.00035 | 
| cooldown | 30 s |
| policy | `kvpr-global-v4`, `paper-faithful-v6` |
| models / SLO / hardware | unchanged |
| windows | 30 s, 60 s only — no third value, no EMA, no adaptive |

Only non-runtime change: the trace freezer's matrix was made env-overridable
(`FREEZE_RATES`/`FREEZE_SEEDS`, defaults unchanged) so the calibration set could
be frozen with the same code and schema. It lives in `exp/scripts/`; the runtime
hash is unaffected.

---

## 4. Calibration trace manifest

8 new deterministic traces, **seeds 3 and 4**, rates 8 and 10, steady and bursty,
in a separate directory so the canonical seed 1/2 traces are untouched.
`trace_manifest.csv`.

| file | sha256 (12) | requests | span |
|---|---|---:|---:|
| steady_r8_s3 | `492d79a4a2f3` | 3406 | 419.6 s |
| steady_r8_s4 | `cbfb24e42d0b` | 3345 | 419.8 s |
| steady_r10_s3 | `c6939afccd79` | 4272 | 419.6 s |
| steady_r10_s4 | `66fe74fb51c6` | 4324 | 419.9 s |
| bursty_r8_s3 | `60be4c9d1276` | 3406 | 419.5 s |
| bursty_r8_s4 | `a000cf22307e` | 3345 | 419.9 s |
| bursty_r10_s3 | `e7afd7e09bac` | 4272 | 419.8 s |
| bursty_r10_s4 | `c913720ee52b` | 4324 | 419.9 s |

```
TRACE_PAIRING = 8/8
```
Both window arms consume the **same file**, re-hashed against the frozen manifest
immediately before each arm — pairing is evidence, not assumption.

**FINAL_HOLDOUT_SEEDS = 5, 6 were not generated and not run.**

---

## 5. Correctness / validity

```
WINDOW_UNIT_TESTS = PASS (12/12)
VALID_RUNS        = 16/16
ALG1_REPLAY       = 100.0% in BOTH arms
```

Every run: `rc=0`, verdict PASS, empty failed-gate list, **0 aborted, 0 Alg2
ordering violations, 0 staged-return failures**. 0–2 `client_errors` appear in
5 runs, inside the accepted gate suite and present in both arms.

Unit tests cover 30 s expiry, 60 s expiry, both components sharing one configured
window, the window knob leaving τ/cooldown/SLO/formula untouched, and
hand-calculated synthetic values. Four assertions failed on first run — all four
were errors in my test arithmetic, not the estimator. One of them surfaced a real
property now recorded as `TestMonotonicTimeAssumption`: **pruning is destructive**
(expired events are popped, not filtered), so the estimator is correct only under
non-decreasing query time and chronologically ordered reports. Both hold in the
runtime.

### Known harness issue (recorded, not fixed — out of scope)

Attempt 1's gate run was labelled `INVALID_CLIENT_FD_EXHAUSTION`. That label is
wrong: `fd_exhaustion 0`, `errno_24 0`, `too_many_open_files 0`, nofile limit
1 048 576. The actual cause was mine — `window_cal_run.sh` passed an inner tmux
session name (`v4-w30-…`) that `run_v4_case.sh:236` does not create
(`v4-${SYSTEM}-…`), so the stage watchdog polled a non-existent session and
killed a healthy server. Full analysis in
`exp/results/4het-window-calibration/STOP_ANALYSIS_attempt1.md`; the run is
preserved as `seed_3.inner-session-misnamed-attempt1` and **excluded from all
analysis**. Validity in this report is judged from rc, server logs, kill audit
and OOM/CUDA/NCCL evidence — not from harness labels.

---

## 6. Token-rate stability and 7. rank-1 dynamics

`rate_stability.csv`, `rank_dynamics.csv`. Medians over the 8 conditions:

| measure | 30 s | 60 s | delta | paired direction |
|---|---:|---:|---:|---|
| rank-1 switches/min | 0.559 | 0.698 | **+0.139** | down 3/8, up 2/8, flat 3/8 |
| rank-1 lifetime p50 (s) | 74.9 | 79.3 | +4.4 | up 6/8 |
| rank-1 small-model share | 26.6 % | 33.9 % | **+7.3 pp** | up 5/8, down 1/8 |

Rank-1 identity (median share of cycles):

| model | 30 s | 60 s |
|---|---:|---:|
| Llama-3.2-3B (small) | 26.6 % | **33.9 %** |
| Qwen2.5-3B (small) | 0.0 % | 0.0 % |
| Llama-3.1-8B (large) | 43.9 % | 43.6 % |
| Qwen2.5-7B (large) | 13.5 % | 11.1 % |

The pre-registered stabilization prediction is **not** met. Rank-1 lifetime rises
by 4.4 s against a 60–430 s base — within noise, and driven by two conditions
that move in opposite directions (steady r8 s4 +214 s, steady r8 s3 −60.8 s).

---

## 8. Algorithm 1 stability

`planner_stability.csv`. Pooled: migrations 64 → 58, reversals 44 → 38,
ping-pong 42 → 37. Median plan changes/min 1.256 → 1.112. Those totals favour
60 s mildly. The per-condition picture does not:

| condition | plan changes/min | median plan lifetime (s) | migrations |
|---|---|---|---|
| steady r8 s3 | 1.269 → **0.280** | 22.9 → **157.6** | 6 → **2** |
| steady r8 s4 | 0.695 → **0.279** | 8.0 → 11.2 | 5 → **2** |
| steady r10 s3 | 0.418 → 0.417 | 109.5 → 107.1 | 4 → 4 |
| steady r10 s4 | 0.833 → **0.278** | 5.3 → 5.9 | 4 → **2** |
| bursty r8 s3 | 1.975 → **2.370** | 30.1 → **15.4** | 11 → 13 |
| bursty r8 s4 | 1.260 → **1.819** | 42.7 → **12.7** | 11 → 11 |
| bursty r10 s3 | 2.525 → 2.512 | 10.1 → 15.2 | 13 → 13 |
| bursty r10 s4 | 1.252 → **1.807** | 43.0 → **10.1** | 10 → 11 |

Steady improves on every axis. Bursty churns *more* and holds plans for a third
as long. Pooling the two would hide a sign reversal, so it is not done.

---

## 9. KVPR placement composition

`placement_composition.csv`. Medians:

| measure | 30 s | 60 s | delta | direction |
|---|---:|---:|---:|---|
| KVPR-optimal large-large | 24.8 % | **31.0 %** | +6.2 pp | up 5/8, **never down** |
| recorded plan large-large | — | — | +1.2 pp | up 5/8, never down |
| actual residency large-large | 34.7 % | 34.3 % | −0.4 pp | up 3/8, down 2/8 |
| 3+1 exposure | 36.6 % | 35.2 % | −1.4 pp | down 6/8 |

The 82× rank-1 relationship from the 4-HET analysis reproduces on these fresh
calibration traces: conditions with small-model rank-1 carry essentially all the
large-large optimality (steady r8 s4: rank-1 small 98.8 %, optLL 96.5 %; steady
r8 s3 and r10 s3: rank-1 small ≈ 0 %, optLL 0.0 %).

**A longer window increases the pathology.** More smoothing → more cycles where
the small Llama-3.2-3B tops weighted demand → more cycles where co-locating the
two large models is KVPR-optimal.

---

## 10–11. Steady vs bursty, downstream

`latency_summary.csv`, `paired_window_deltas.csv`. **Secondary evidence.**

| condition | goodput 30 → 60 | attainment | Qwen7B TPOT p95 (ms) | agg TPOT p99 (ms) |
|---|---|---|---|---|
| steady r8 s3 | 5.265 → **6.091** | 0.662 → 0.766 | 62.1 → 56.0 | 70.6 → 60.8 |
| steady r8 s4 | 4.671 → **5.154** | 0.602 → 0.663 | 64.2 → 56.8 | 72.9 → 65.0 |
| steady r10 s3 | 4.778 → 4.629 | 0.481 → 0.466 | 64.7 → 69.2 | 77.2 → 78.8 |
| steady r10 s4 | 5.301 → 5.339 | 0.529 → 0.533 | 82.6 → 74.6 | 92.7 → 80.6 |
| bursty r8 s3 | 4.984 → **4.406** | 0.626 → 0.555 | 73.1 → 54.7 | 107.2 → 123.7 |
| bursty r8 s4 | 4.809 → **3.747** | 0.615 → 0.479 | 121.2 → 174.1 | 136.7 → 191.4 |
| bursty r10 s3 | 4.710 → **4.342** | 0.474 → 0.436 | 105.4 → 75.5 | 223.1 → 221.1 |
| bursty r10 s4 | 4.590 → **3.569** | 0.457 → 0.356 | 787.4 → **1212.9** | 1106.1 → **1668.3** |

Pooled goodput delta median **−0.259** (up 3/8); attainment **−0.027** (up 3/8).
Steady **+0.260** (up 3/4); bursty **−0.799** (up **0/4**), worst case −22 %.

---

## 12. Fidelity vs performance interpretation

Applying the pre-declared hierarchy:

1. **Paper fidelity** — 60 s is the only value the camera-ready supports; 30 s
   was chosen under a mistaken belief that the window was unspecified. **Favours 60 s.**
2. **Monitoring semantics** — replay 100 % in both arms, unit tests pass, both
   components share one window. No pathology in the sliding-window sense. The
   bursty degradation is *expected* behaviour of a longer moving average, not a
   defect. **Verified for 60 s.**
3. **Planner mechanism** — propagation is coherent and traceable but sign-split
   by regime, and the composition effect runs against performance.
   **Does not favour 60 s.**
4. **Downstream** — mixed; clearly negative in bursty. **Does not favour 60 s**,
   and is explicitly not the tuning objective.

The honest position: **priority 1 favours 60 s, priorities 3–4 favour 30 s, and
priority 2 is neutral.** Selecting 30 s here would mean overriding the paper's
only stated value on the strength of goodput on our own synthetic bursty
workload — precisely the goodput-driven tuning this task forbids. No third window
was tried.

---

## 13. Window verdict

```
WINDOW_STATUS = WINDOW_SEMANTICS_VERIFIED_BUT_EFFECT_MIXED
```

60 s is faithfully implemented and free of correctness problems; its
stability/responsiveness effects are mixed and regime-split.

**Recommendation (a judgement call, and reversible): adopt 60 s as the
paper-faithful configuration**, and carry the bursty responsiveness cost as a
*reported finding* of the baseline rather than as grounds to keep 30 s. Reasons:
it is the only camera-ready-supported value; the implementation is verified at
both settings; the bursty cost is textbook longer-moving-average behaviour, not a
bug; and A.4's ~60 s was measured on production traces whose phase structure is
gentler than our synthetic bursty workload.

This recommendation should be overridden if the project's goal is best measured
performance in *this* workload rather than paper fidelity — in which case 30 s
wins on bursty and the choice must be declared as a deliberate deviation from
A.4, not presented as faithful.

---

## 14. Next-step gate

```
NEXT = τ recalibration        (NOT executed)
```

τ = 0.00035 was calibrated under the old estimator semantics and a 30 s window.
It has no calibrated standing against the frozen estimator + chosen window, and
must be recalibrated only once that configuration is fixed.

**Calibration-data requirement, carried forward:** calibration seeds (3, 4) and
final hold-out seeds (5, 6) must stay separate. Seeds 1/2 are heavily analysed and
are unsuitable as final evaluation data. Seeds 5/6 remain ungenerated and
untouched. Do not tune against any seed used for final evaluation.

Sequence unchanged:
1. estimator semantics — **FIXED**
2. monitoring window — **this task, verdict B**
3. τ recalibration
4. freeze corrected Prism
5. clean hold-out 4-HET on untouched final seeds
6. FINAL BASELINE VERIFIED
7. paper-like many-model + shifting-burst

---

## 15. STOP status

No τ recalibration. No final 4-HET. No final seeds generated or run. No
Prototype rerun. No many-model. No Algorithm 1 modification, no compute-aware
placement. No git push. Raw logs and prior reports untouched; the invalid
attempt-1 run is preserved and excluded.
