# Phase 5 — offline migration-thrashing root-cause analysis

**Offline only.** No benchmark, no runtime/scheduler/policy change, no τ change,
no cooldown change, no many-model workload, no git push. Existing artifacts read,
never modified. Data: `exp/analysis/migration_thrashing/`.

Sources: the four cooldown=30 baseline runs and the four cooldown=0 diagnostic
runs, steady r8 s1/s2 and r10 s1/s2.

---

## 1. Executive finding

**The leading hypothesis is not supported.** The churn is *not* driven by the
~30 s rate-estimation window, and the cooldown does *not* suppress plan churn.

Three measurements settle it:

1. **Plan churn is essentially identical in both arms.** Placement plans change
   2.0–4.1 times per minute with a median lifetime of **5.1–12.6 s** at
   cooldown=30 *and* at cooldown=0. The cooldown never touched the planner; it
   only rate-limited acting on it.
2. **Reversals do not occur on the 30 s window's timescale.** At cooldown=30 the
   ping-pong interval is p50 31.9 s with a **minimum of 30.1 s** — clamped by the
   cooldown itself. Remove the cooldown and the interval falls to p50 15.1 s with
   a **minimum of 5.0 s, exactly one controller cycle**. If the 30 s estimation
   window were the driver, reversals would still cluster near 30 s. They do not.
3. **The decisions are not marginal.** The line-8 delta at each emitted migration
   is a median **45–47× τ**. These are large, confident reversals, not a
   threshold flickering.

What the traces actually show is a **planner that is unstable on the controller's
own 5 s cadence**, producing a target placement that reverses faster than any
migration can be completed. The 30 s cooldown is the only thing that was holding
that instability to one action per 30 s.

And the "second leg" of a swap is usually **not a completion — it is a reversal
of the first leg**. Only 4 of 35 baseline migrations are swap-completion legs.

## 2. Rate estimator, from source

| property | value | source |
|---|---|---|
| window | **30 s** | `kvpr_global.py:76,109,112` (`--kvpr-rate-window 30`) |
| kind | **sliding**, recomputed fresh at every decision | `now - at > self.rate_window` filter |
| decision cadence | **5 s** (`SCHEDULE_INTERVAL`) | `controller_global.py:395` |
| samples per window | ~6, heavily overlapping | 30 s / 5 s |
| terms | `input_rate` (30 s sliding prompt tokens ÷ 30) **+** `decode_token_tput` | `kvpr_global.py:112-114` |
| `decode_token_tput` | `decode_token_count / elapsed_time`, engine-reported | `scheduler.py:447-451`, delivered at `controller_global.py:354` |
| smoothing | **none anywhere in the policy** | the raw estimate feeds `argmin` directly |

The overlapping 30 s window makes `input_rate` *smooth*; a plan that flips every
5–12 s cannot be explained by it. The unsmoothed, engine-reported
`decode_token_tput` is the volatile term, and a migration disturbs it directly —
a migrating model stops decoding on the source and restarts on the target — so
**migration perturbs the very measurement that drives the next migration.**
Observed: `peak_kvpr` at the cycle after a migration is p50 0.88–0.97× its value
at the migration, but ranges from 0.51× to 20.9×.

## 3. Migration classification (plan identity, not direction)

In-flight target := the `placement_plan` in force when the last migration was
emitted. A later migration under the *same* plan is a completion leg; under a
*different* plan it is a new-plan migration.

| arm | total | **swap-completion legs** | **new-plan migrations** | ping-pong |
|---|---:|---:|---:|---:|
| cooldown30 | 35 | **4** | **31** | 26 |
| cooldown0 | 81 | 18 | 63 | 68 |

**Even at the baseline, 26 of 35 migrations are ping-pong and only 4 are genuine
swap completions.** Removing the cooldown multiplies migrations ×2.3 and
ping-pong ×2.6, but the *composition* barely changes: new-plan migrations
dominate in both arms.

## 4. Plan stability

| condition | arm | unique plans | changes | changes/min | plan lifetime p50 | migrations/min | ping-pong |
|---|---|---:|---:|---:|---:|---:|---:|
| steady 8 s1 | cooldown30 | 8 | 22 | 3.07 | 5.2 s | 1.26 | 6 |
| steady 8 s1 | cooldown0 | 8 | 29 | 4.05 | 5.1 s | 1.68 | 8 |
| steady 8 s2 | cooldown30 | 3 | 26 | 3.61 | 8.3 s | 1.39 | 8 |
| steady 8 s2 | cooldown0 | 6 | 23 | 3.21 | 10.0 s | **3.21** | **20** |
| steady 10 s1 | cooldown30 | 5 | 21 | 2.94 | 10.1 s | 1.40 | 7 |
| steady 10 s1 | cooldown0 | 5 | 22 | 3.07 | 7.5 s | **3.91** | **24** |
| steady 10 s2 | cooldown30 | 3 | 14 | 1.95 | 10.3 s | 0.84 | 5 |
| steady 10 s2 | cooldown0 | 3 | 18 | 2.52 | 12.6 s | 2.52 | 16 |

Plan-change rate and plan lifetime are **the same in both arms**. Only the
migration rate and ping-pong count move. This is the decisive evidence that the
cooldown is a *actuation* limiter, not a *planning* stabiliser.

Consecutive-plan similarity is dominated by one-model and two-model differences —
the planner oscillates among a small set of placements (3–8 unique plans per run).

## 5. Ping-pong reversal intervals

| arm | n | p50 | min | max |
|---|---:|---:|---:|---:|
| cooldown30 | 26 | 31.9 s | **30.1 s** | 259.0 s |
| cooldown0 | 68 | 15.1 s | **5.0 s** | 234.3 s |

The baseline minimum equals the cooldown; the diagnostic minimum equals one
controller cycle. The cooldown is a floor on reversal speed, nothing more.

## 6. Why 3+1 episodes ended

| arm | episodes | swap completed | superseded by a new plan | memory-blocked at some point |
|---|---:|---:|---:|---:|
| cooldown30 | 17 | 8 (47%) | 9 (53%) | 2 |
| cooldown0 | 40 | 25 (63%) | 15 (37%) | 2 |

More than half of baseline 3+1 episodes never complete their swap — a new plan
supersedes it. Memory blocking terminates only 2 episodes per arm, so it is a
**minor and independent** contributor here (though it produced the single 172 s
outlier in Phase 3).

## 7. steady r8 s2 forensic — the −41% run

**cooldown=30** — 10 migrations, inter-migration gap p50 31.7 s, **min 30.2 s**:

```
 t=  5  model_6 1->0   from 2+2 (gap 2)      t= 37  model_6 0->1   from 1+3  <- reversal
 t=118  model_4 0->1   from 2+2 (gap 2)      t=148  model_4 1->0   from 1+3  <- reversal
 t=194  model_4 0->1   from 2+2 (gap 1)      t=224  model_4 1->0   from 1+3  <- reversal
 t=296  model_6 1->0   from 2+2 (gap 2)      t=326  model_6 0->1   from 1+3  <- reversal
```

Every pair is the *same model* moving out and back, spaced by exactly the
cooldown. The swap is never completed.

**cooldown=0** — 23 migrations, gap p50 10.0 s, **min 5.0 s**:

```
 t= 88 model_4 0->1 | t= 93 model_4 1->0 | t=103 model_4 0->1
 t=113 model_4 1->0 | t=123 model_4 0->1
```

`model_4` oscillates **five times in 35 seconds**. The shape alternates
2+2 → 1+3 → 2+2 → 1+3, so roughly half the time is spent in the intermediate
state — matching the measured 58.1% exposure.

**R8S2 primary failure mechanism: unbounded reversal of a single model
(`model_4`, with `model_6` and `model_3` joining later) at the controller's 5 s
cadence.** Not memory blocking (1 blocked cycle), not prolonged single episodes,
not τ. The performance collapse (goodput −41%, Qwen7B TPOT p50 0.0349 → 0.2032,
TTFT p99 11.5 s → 292.6 s) coincides with this oscillation window.

## 8. Plan-aware counterfactual (descriptive; no measured improvement claimed)

Proposed rule (evaluated, **not implemented**): rate-limit *new* plans as today,
but let an already-started multi-move plan finish its remaining legs without
waiting the global cooldown.

From the baseline traces:

| quantity | value |
|---|---:|
| cooldown-blocked cycles where the plan **was** the in-flight target (would be bypassed) | **26** (~130 s) |
| cooldown-blocked cycles under a **new** plan (would still be held) | **95** (~475 s) |
| total 3+1 time at baseline | 529 s |
| share of 3+1 time attributable to same-plan cooldown waiting | **24.6%** |
| cooldown=0 migrations that were new-plan (would still be suppressed) | **63 of 81** |
| ping-pong migrations at cooldown=0 that were new-plan reversals (would remain suppressed) | most of 68 |

So a plan-aware rule would bypass only **21%** of cooldown waits (26 of 121) and
could remove at most **~25%** of the intermediate-state time, while still
withholding the 63 new-plan migrations that drove the cooldown=0 thrashing.

**That is a real but modest gain, and it does not address the root problem.**
Only 4 of 35 baseline migrations were swap completions; the plan reverses more
often than it completes. Making completion faster does not stop the reversals.

## 9. Paper fidelity

**Paper-specified** (matches, per `docs/paper_faithful/design_analysis.md` §2):
the placement objective, the weighting `token_rate × token_size / SLO`,
descending-rate ordering, `KVPR = Σw / shared_kv`, minimum-KVPR destination, the
`> τ` test.

**Implementation-added**: one migration per cycle, the global 30 s cooldown, the
target-memory reserve gate, the source-GPU block. The available description says
nothing about how often the plan may be applied, and **nothing that requires
atomic swaps** — no such claim is made here.

> **Would plan-aware completion preserve Algorithm 1's selected placement more
> faithfully than the current globally cooldown-gated application?**
>
> **INCONCLUSIVE, leaning NO as a remedy.** It would preserve *in-flight* plans
> better (26 waits bypassed, ~25% of intermediate time). But "the placement
> selected by Algorithm 1" is not a stable object here — it changes every
> 5–12 s, and 53% of baseline episodes are superseded before completion. Applying
> a plan more faithfully is only meaningful if the plan itself is meaningful for
> longer than it takes to apply it, and these traces show it is not.

## 10. Hypothesis verdicts

| # | hypothesis | verdict | evidence |
|---|---|---|---|
| H1 | cooldown=0 migration explosion is primarily NEW-PLAN churn | **STRONGLY_SUPPORTED** | 63 of 81 are new-plan; ping-pong 26→68 |
| H2 | the churn is driven by the ~30 s rate-estimation dynamics | **DISPROVEN** | plan lifetime p50 5.1–12.6 s in *both* arms; reversal minimum is 5.0 s (one cycle), not ~30 s; deltas are 45–47× τ, not marginal |
| H3 | the 30 s cooldown effectively suppresses new-plan churn | **PARTIALLY — DISPROVEN as stated** | it does not suppress *churn* (plan changes/min unchanged); it suppresses *migrations* arising from it (35 vs 81) |
| H4 | the same cooldown unnecessarily delays completion of an already-started swap | **SUPPORTED but SMALL** | 26 blocked cycles (~130 s, 24.6% of 3+1 time); only 4 of 35 migrations were completion legs |
| H5 | separating new-plan rate limiting from in-flight completion could reduce both 3+1 exposure and thrashing | **SUGGESTIVE for exposure, NOT_SUPPORTED for thrashing** | ~25% of 3+1 time addressable; thrashing is new-plan reversal, which the rule deliberately keeps suppressing |
| H6 | memory feasibility remains an independent blocker after removing cooldown | **SUPPORTED** | memory-blocked cycles 39/0/3/0 → 25/1/16/0; ends only 2 episodes per arm, but produced the 172 s baseline outlier and a new 100.7 s outlier at cooldown=0 |

## 11. What this reframes

Phase 4 concluded the cooldown causes the *long* intermediate state — still true.
Phase 5 shows the deeper problem is upstream: **the placement plan itself is
unstable at the decision cadence**, and both the 3+1 exposure and the cooldown=0
thrashing are symptoms of that instability. The cooldown is a crude damper on an
oscillating planner, and removing or refining it treats the symptom.

The oscillation is plausibly self-sustaining — a migration disturbs
`decode_token_tput`, which feeds the next plan — but that loop is **not proven
here**: the measured `peak_kvpr` perturbation around migrations is real but not
systematically inverted (p50 0.88–0.97×, range 0.51–20.9×), and the traces do not
record per-model rate components per cycle, so the loop cannot be closed from
these artifacts.

## 12. Recommended next step

**Offline first, no benchmark: test the self-sustaining-oscillation loop
directly.** It needs one thing the current trace does not carry — the per-model
`weighted_token_rate` at each cycle. `[PAPER-ALG1-V4]` records `kvpr`, `line8`
and the plan, but not the per-model rate terms, so the loop cannot be closed
today. Whether that series can be reconstructed from `line8`'s sequential
partial ratios plus the fixed model sizes is itself an offline question worth an
hour; if it can, the loop is testable with zero GPU time.

If it cannot be reconstructed, the minimal next step is **instrumentation, not a
policy change**: log `weighted_token_rate` per model per cycle (a logging-only
change to an existing log line) and re-run a *single* steady condition. That
would show directly whether migration perturbs its own driving signal.

Either way, a plan-aware cooldown is **not** the recommended next change: this
analysis bounds its benefit at ~25% of intermediate-state time while leaving the
dominant new-plan reversal untouched.

I have stopped here and made no further changes.
