# Phase 4 Stage B — cooldown-only mechanism test

One runtime variable: `kvpr-migration-cooldown` 30 s → 0 s. Nothing else was
changed — not the runtime source, Algorithm 1, τ, c_i, SLO, workloads, models,
KV migration, overlap-migration, worker pool or collector. No git push, no
history rewrite, no touching the 901 MB commit. Existing artifacts unmodified.
Raw: `exp/results/4het-cooldown-diagnostic/`.

## 1. Question

Does the implementation-added 30 s migration cooldown cause the long-lived
intermediate 3+1 state that Algorithm 1's 2+2 swap plans pass through?

**The primary endpoint is placement mechanics, not goodput.**

## 2. Flag semantics, verified before launch

| item | finding | source |
|---|---|---|
| default | `30.0` | `multi_model_server_args.py:174`, `kvpr_global.py:77` |
| enforcement in the v4 path | **exactly one site** | `kvpr_global_v4.py:92` |
| effect of that site | one `elif` branch that skips emitting a migration this cycle and increments `deferred_by_cooldown` | `kvpr_global_v4.py:134-136` |
| is 0 valid? | yes — `type=float`, no validation; `_last_migration_time` starts at `-inf`, so `(now-last) < 0` is always False | `kvpr_global.py:87` |
| other semantic change at 0? | **none found** — eligibility timing only | exhaustive grep of `migration_cooldown` / `_last_migration_time` |

`kvpr_global_v3.py:138` and `kvpr_global.py:278` also test the cooldown but are
not on the v4 code path, which overrides `_find_optimal_migrations`.

## 3. Configuration diff

`run_v4_case.sh` already reads `KVPR_COOLDOWN` from the environment, so the
**unchanged `paper-faithful-v6` arm** was used and **no harness edit was needed**.

Measured on the real `SERVER_COMMAND.txt`:

- **flag sets identical** (`diff` of all `--flags` is empty)
- only value difference: `--kvpr-migration-cooldown 30` → `0`
- plus the runtime-assigned `--port` (varies between all runs) and the output
  `--log-file` path (necessarily a different directory)

**INTENDED RUNTIME DIFFERENCE = exactly 1.**

## 4. Validity — 4/4

| condition | completed / offered | aborted | rc | migrations |
|---|---:|---:|---:|---:|
| steady r8 s1 | 3373 / 3373 | 0 | 0 | 12 |
| steady r8 s2 | 3317 / 3320 | 0 | 0 | 23 |
| steady r10 s1 | 4138 / 4139 | 0 | 0 | 28 |
| steady r10 s2 | 4181 / 4181 | 0 | 0 | 18 |

No STOP, no client errors. Controls are the existing validated cooldown=30 Prism
runs and the Prototype runs; neither was re-run.

## 5. Primary endpoint — placement mechanics

| condition | arm | 3+1 % of run | episodes | ep p50 | ep max | swap plans | completed | swap latency p50 | cooldown-blocked cycles | memory-blocked cycles |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| steady 8 s1 | cooldown30 | **69.4** | 5 | **32.0** | **172.2** | 17 | 17 | **65.7** | 45 | 39 |
| steady 8 s1 | cooldown0 | **8.3** | 6 | **5.1** | 10.0 | 17 | 16 | **5.1** | **0** | 25 |
| steady 8 s2 | cooldown30 | 35.5 | 5 | 30.3 | 31.3 | 7 | 5 | 35.2 | 46 | 0 |
| steady 8 s2 | cooldown0 | **58.1** | 11 | 10.0 | **100.7** | 7 | 5 | **66.6** | **0** | 1 |
| steady 10 s1 | cooldown30 | 37.0 | 5 | 30.4 | 34.2 | 18 | 18 | 45.2 | 50 | 3 |
| steady 10 s1 | cooldown0 | 24.1 | 14 | 5.0 | 25.2 | 15 | 15 | 23.9 | **0** | 16 |
| steady 10 s2 | cooldown30 | 21.2 | 3 | 30.4 | 30.7 | 1 | 1 | 35.7 | 30 | 0 |
| steady 10 s2 | cooldown0 | 22.3 | 9 | 10.1 | 20.1 | 4 | 4 | 10.1 | **0** | 0 |

Pooled episode duration:

| arm | episodes | p50 | p95 | max | total 3+1 |
|---|---:|---:|---:|---:|---:|
| cooldown30 | 18 | **30.5 s** | 54.9 s | 172.2 s | 701 s |
| cooldown0 | 40 | **5.4 s** | 27.0 s | 100.7 s | 485 s |

**The predicted signature is confirmed.** The ~30.5 s mode — which matched the
configured cooldown exactly — collapses to 5.4 s. `cooldown_active` cycles go
45/46/50/30 → **0/0/0/0**, so the treatment demonstrably took effect. Episodes
become more numerous (18 → 40) but far shorter, and total 3+1 time falls 31%.

**But removing the cooldown does not reliably reduce 3+1 exposure.** Migrations
rise sharply (9→12, 10→23, 10→28, 6→18), and in steady r8 s2 the churn made
things worse: 3+1 rose 35.5% → 58.1% and swap latency 35.2 s → 66.6 s. This is
the thrashing the v4 docstring anticipated — *"moving several models against one
window's estimate is what produced the thrashing"* — the rate estimate is a 30 s
sliding window, and with no cooldown the policy acts many times inside one window.

## 6. Memory-feasibility residual

Memory blocking is **not** removed by cooldown=0 and in two conditions increases
(steady r8 s1: 39 → 25 cycles; steady r10 s1: 3 → 16). The 172 s outlier at
cooldown30 (steady r8 s1, memory-blocked in all its cycles) does disappear —
its max episode falls to 10.0 s — but steady r8 s2 gains a new 100.7 s episode.
So **cooldown-induced waiting and memory-gate-induced waiting are distinct**, and
only the first is addressed here.

## 7. Decode and outcome

Means over the four conditions:

| arm | goodput | attainment | throughput | TPOT p50 | TPOT p99 |
|---|---:|---:|---:|---:|---:|
| Prototype | **6.8777** | 0.7954 | 8.74 | 0.0308 | 0.0498 |
| cooldown30 | 4.2251 | 0.4884 | 8.71 | 0.0350 | 0.1047 |
| cooldown0 | 4.1098 | 0.4700 | 8.70 | 0.0358 | **0.2972** |

Per condition, goodput: 3.335→**4.283** (+28.4%), 4.620→**2.721** (−41.1%),
4.276→4.557 (+6.6%), 4.671→4.879 (+4.5%). Throughput is unchanged everywhere.

Qwen2.5-7B pooled: TPOT p50 0.0374 → 0.0440, TPOT p99 0.1868 → **0.7180**,
ITL p99 0.2680 → **0.9765**. The tail degradation is dominated by steady r8 s2
(TPOT p50 0.0349 → 0.2032; TTFT p99 11.5 s → **292.6 s**), the thrashing run.

## 8. Dose-response

| condition | Δ 3+1 exposure | Δ goodput | migrations base→diag |
|---|---:|---:|---:|
| steady 8 s1 | **−61.1 pp** | **+28.4%** | 9 → 12 |
| steady 8 s2 | **+22.6 pp** | **−41.1%** | 10 → 23 |
| steady 10 s1 | −12.9 pp | +6.6% | 10 → 28 |
| steady 10 s2 | +1.1 pp | +4.5% | 6 → 18 |

**corr(Δ 3+1 exposure, Δ goodput) = −0.889 (n = 4).** Within this experiment,
whenever exposure to the intermediate state fell, goodput rose, and where it
rose, goodput fell — including the condition that got much worse. That is
consistent evidence for the 3+1 → decode → goodput chain, obtained without
relying on the cooldown treatment "working".

## 9. Predeclared causal chain

| arrow | verdict | evidence |
|---|---|---|
| 30 s cooldown → **long** intermediate 3+1 | **SUPPORTED** | episode p50 30.5 s → 5.4 s; the 30 s mode disappears; cooldown-blocked cycles → 0 |
| 30 s cooldown → **more total** 3+1 exposure | **NOT_SUPPORTED** | exposure fell in 2 of 4 and *rose* in 2 of 4; total 701 s → 485 s but not per-condition monotone |
| 3+1 exposure → Qwen7B / large-model decode contention | **SUPPORTED** | corr −0.889 across conditions; Phase 3's monotone ITL/TPOT-by-co-residency, 1.32–1.65× |
| decode contention → higher aggregate TPOT | **SUPPORTED** | TPOT tracks exposure per condition |
| TPOT → SLO goodput loss | **SUPPORTED** (established Phase 2 §7) | cliff amplification, unchanged |
| **cooldown=0 → better goodput** | **NOT_SUPPORTED** | mean goodput 4.2251 → 4.1098; 3 of 4 up, 1 sharply down; TPOT p99 nearly 3× worse |

## 10. Interpretation

This is **CASE A on the mechanism and CASE D on the treatment**, simultaneously:

- **CASE A evidence**: the 30 s cooldown does cause the long-lived intermediate
  state. The episode-duration mode is the cooldown value, and it vanishes when
  the cooldown does. The plan-application layer, not Algorithm 1's objective,
  is what holds the cluster in a placement the objective does not want.
- **CASE D evidence**: setting the cooldown to 0 is not a fix. It converts one
  long intermediate state into many short ones plus **placement thrashing**
  against a 30 s rate window, which in one of four conditions was far worse than
  the disease (goodput −41%, TTFT p99 292 s). **The cooldown is serving a real
  stabilisation purpose.**

Per the instruction, no other cooldown value was tried and none is proposed.

## 11. Paper fidelity

Stated precisely: **the Algorithm 1 decision rule matches the available paper
description, while our plan-application layer adds a 30 s cooldown and limits
migration emission to one per cycle, which are not specified in that
description.** This experiment isolates the effect of that implementation-added
constraint. It does **not** show that the paper requires atomic swaps, and it
does **not** make cooldown=0 "paper-faithful" — there is no documentary support
for either claim.

## 12. Recommended next step

**Not** a cooldown sweep, and **not** a full 20-condition rerun: cooldown=0 is
demonstrably not the answer, and no other value has evidence behind it.

The evidence now points at the **plan-application layer as a design problem**:
a two-move swap has no atomic form, so the implementation must either pass
through an imbalanced state or hold a rate-limit that keeps it there. The
discriminating question is whether a plan-aware application — completing the
second half of an already-started swap without waiting a full cooldown, while
still rate-limiting *new* plans — removes the long intermediate state without
the thrashing. That is a design change, so it is proposed, not performed.

Before any implementation, one further **offline** check is cheap and should
come first: whether the thrashing at cooldown=0 is driven by the 30 s rate
window interacting with migration frequency, measurable from the existing
`[PAPER-ALG1-V4]` traces of these four new runs.

I have stopped here and made no further changes.
