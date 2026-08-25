# `--overlap-migration` diagnostic — 8-run controlled ablation

Treatment is exactly one flag. Controls are the existing validated 4-HET
artifacts; Prototype was not re-run and no baseline artifact was modified.
No τ, c_i, SLO, policy or runtime-source change. Raw: `exp/results/4het-overlap-diagnostic/`.

## 1. Experimental question

Does the `--overlap-migration` enabled path actually contribute to Prism's
steady TPOT degradation / placement imbalance and bursty TTFT pathology?

## 2. Exact treatment diff

Full semantics in `exp/results/4het-overlap-diagnostic/TREATMENT.md`. Verified
from source **before** any run:

- `overlap_migration: bool = False`, `action="store_true"` → **omitting = OFF**.
- OFF changes four things: single action batch instead of five serialized
  phases; control calls become fire-and-forget instead of
  `_send_req_and_wait_for_response` (the readiness barrier); action timeout
  600 s → None; the "skip source deactivation when target activation failed"
  guard is not applied.
- **Migration is not disabled.** Only its execution semantics change.

Normalized diff of the real server commands: `--overlap-migration` present in ON,
absent in OFF; `--port` differs, but it is assigned per run by
`find_free_port.py` and differs between ON runs too (42800/42950/42600/41250).
**INTENDED CONFIG DIFFERENCE = exactly 1.**

A trap was caught before running: the new harness arm would have fallen into the
`*)` default in `run_v4_case.sh` and lost `PRISM_V6_KV_MIGRATION`, making the
treatment two variables. All five system-name branches were extended so the arm
is treated identically to `paper-faithful-v6`.

## 3. Predeclared prediction (recorded before results)

> ON keeps source and target both resident across the readiness barrier, a direct
> candidate for the 3-on-1 imbalance. OFF should shorten or remove that overlap,
> possibly at the cost of brief windows where a model is resident nowhere.

## 4. Validity — 8/8

Every run: rc=0, VERIFICATION verdict PASS, trace SHA256 checked against the
frozen `WORKLOAD_MANIFEST.json` before launch, zero client errors.

| condition | completed / offered | aborted |
|---|---:|---:|
| steady r8 s1 | 3373 / 3373 | 0 |
| steady r8 s2 | 3320 / 3320 | 0 |
| steady r10 s1 | 4138 / 4139 | 0 |
| steady r10 s2 | 4181 / 4181 | 0 |
| bursty r6 s1 | 2584 / 2584 | 0 |
| bursty r6 s2 | 2522 / 2523 | 0 |
| bursty r8 s1 | 3373 / 3373 | 0 |
| bursty r8 s2 | 3320 / 3320 | 0 |

One preserved infrastructure failure: `bursty/rate_6/seed_2.stale-shm-attempt1`
— server died at startup with `KeyError: '/ipc_1_2_root'`, the documented stale
shared-memory mode. No benchmark ran, no numbers. **Not** a correctness finding
about the ablation. Cause was in the diagnostic's own pre-run cleanup (guarded
by a bare `pgrep`, so it was skipped while the previous server was still exiting);
fixed by waiting for the previous processes before clearing, then re-attempted.

## 5. Goodput / attainment / throughput — per seed

| condition | seed | Proto gp | ON gp | **OFF gp** | Δ OFF-vs-ON | Proto attain | ON | OFF |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| steady 8 | 1 | 6.7945 | 3.3349 | **3.3615** | +0.8% | 0.864 | 0.427 | 0.431 |
| steady 8 | 2 | 6.8329 | 4.6196 | **5.1455** | +11.4% | 0.883 | 0.602 | 0.668 |
| steady 10 | 1 | 6.6539 | 4.2756 | **3.7682** | **−11.9%** | 0.692 | 0.444 | 0.399 |
| steady 10 | 2 | 7.2295 | 4.6705 | **5.2615** | +12.7% | 0.743 | 0.481 | 0.541 |
| bursty 6 | 1 | 4.2433 | 4.0218 | **3.8978** | −3.1% | 0.705 | 0.713 | 0.690 |
| bursty 6 | 2 | 5.1185 | 4.9940 | **5.0009** | +0.1% | 0.871 | 0.847 | 0.850 |
| bursty 8 | 1 | 4.7221 | 5.0752 | **4.7028** | −7.3% | 0.600 | 0.646 | 0.602 |
| bursty 8 | 2 | 6.2270 | 5.9873 | **4.5272** | **−24.4%** | 0.800 | 0.770 | 0.614 |

**Direction: 3 of 8 up, 5 of 8 down.** Throughput is unchanged in every arm
(±1%). OFF does not close any part of the steady gap to Prototype: steady OFF
remains 3.77–5.26 against Prototype's 6.65–7.23.

## 6. Placement — the decisive mechanistic test

Time with ≥3 models on one GPU, and total residency:

| condition | seed | Proto t3+ | **ON t3+** | **OFF t3+** | ON migr | OFF migr | ON unavail | OFF unavail |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| steady 8 | 1 | 0.0% | **70.6%** | **70.6%** | 9 | 8 | 0 s | 4 s |
| steady 8 | 2 | 0.0% | 36.9% | 37.3% | 10 | 9 | 0 s | 5 s |
| steady 10 | 1 | 0.0% | 38.0% | 42.8% | 10 | 11 | 0 s | 5 s |
| steady 10 | 2 | 0.0% | 21.9% | 28.0% | 6 | 7 | 0 s | 4 s |
| bursty 6 | 1 | 16.8% | 27.3% | 25.9% | 8 | 7 | 259 s | 271 s |
| bursty 6 | 2 | 1.1% | 24.5% | 27.6% | 8 | 6 | 289 s | 296 s |
| bursty 8 | 1 | 26.4% | 37.2% | 33.3% | 11 | 10 | 178 s | 178 s |
| bursty 8 | 2 | 3.5% | 13.7% | 9.8% | 7 | 8 | 300 s | **419 s** |

**The predeclared prediction is DISPROVEN.** Turning the readiness barrier off
does not reduce 3-on-1 imbalance — it is unchanged (70.6% → 70.6%) or *higher*
(38.0 → 42.8, 21.9 → 28.0). Migration counts are unchanged. Total residency is
unchanged (~4.0 steady).

The one thing that moves in the predicted direction is model-unavailable time:
steady 0 s → 4–5 s, and bursty r8 s2 300 s → 419 s. That is the readiness
barrier doing what it is for, at a small scale — it is not what drives placement.

## 7. Steady TPOT / ITL — ON vs OFF

| condition | seed | Proto TPOT p50 | ON | OFF | Proto TPOT p99 | ON | OFF |
|---|---:|---:|---:|---:|---:|---:|---:|
| steady 8 | 1 | 0.0303 | 0.0369 | 0.0364 | 0.0450 | 0.1030 | **0.1495** |
| steady 8 | 2 | 0.0299 | 0.0329 | 0.0317 | 0.0480 | 0.0953 | 0.0612 |
| steady 10 | 1 | 0.0318 | 0.0357 | **0.0383** | 0.0504 | 0.1552 | 0.1710 |
| steady 10 | 2 | 0.0311 | 0.0346 | 0.0337 | 0.0558 | 0.0655 | 0.0707 |

TPOT p50 moves by ±5% with no consistent sign, and stays 5–20% above Prototype
in every condition. p99 is mixed and in half the cases worse with OFF.

## 8. Qwen2.5-7B (the model Phase 2 identified as worst hit)

| condition | seed | Proto TPOT p50 | ON | OFF | Proto ITL p99 | ON | OFF |
|---|---:|---:|---:|---:|---:|---:|---:|
| steady 8 | 1 | 0.0331 | 0.0288 | 0.0262 | 0.1674 | 0.2357 | 0.2417 |
| steady 8 | 2 | 0.0304 | 0.0349 | 0.0345 | 0.1365 | 0.2207 | 0.1924 |
| steady 10 | 1 | 0.0366 | 0.0460 | 0.0482 | 0.2040 | 0.3451 | **0.4171** |
| steady 10 | 2 | 0.0316 | 0.0365 | 0.0357 | 0.1502 | 0.2011 | 0.2070 |

Essentially unchanged; ITL p99 is worse with OFF in 3 of 4.

## 9. Bursty TTFT — is there a trade-off?

TTFT tail counts, >1 s / >5 s / >10 s:

| condition | seed | Prototype | ON | **OFF** |
|---|---|---|---|---|
| steady 8 | 1 | 0/0/0 | 288/190/98 | 377/274/148 |
| steady 10 | 1 | 0/0/0 | 406/350/243 | 546/433/331 |
| bursty 6 | 1 | 45/10/0 | 346/270/241 | 377/267/235 |
| bursty 8 | 1 | 41/1/0 | 330/223/151 | 359/247/145 |
| bursty 8 | 2 | 81/11/0 | 228/144/76 | **687/588/526** |

**Yes — and it goes the wrong way.** OFF makes the TTFT tail worse in 6 of 8
conditions, and bursty r8 s2 degrades severely: TTFT p99 11.39 s → **114.66 s**,
with model-unavailable time rising 300 s → 419 s. That is the readiness barrier's
protective effect being removed, exactly as its docstring describes.

## 10. Migration / residency differences

Migration counts, bytes and total residency are unchanged between ON and OFF.
The only systematic difference is model-unavailable time (steady 0 s → 4–5 s;
bursty up to +119 s). Placement composition is not governed by the overlap path.

## 11. Causal-chain verdict

| arrow | verdict | evidence |
|---|---|---|
| overlap path → placement trajectory | **NOT SUPPORTED** | t3+ unchanged or higher with OFF (70.6→70.6, 38.0→42.8); migrations unchanged |
| placement → 3-on-1 imbalance | **INCONCLUSIVE here** | placement did not move, so this experiment cannot test the link |
| 3-on-1 → decode contention | **INCONCLUSIVE here** | same reason |
| decode contention → ITL/TPOT | untested by this experiment | — |
| TPOT → SLO cliff → goodput | already PROVEN in Phase 2 | unchanged |
| **readiness barrier causality** | **NOT SUPPORTED** | removing it does not improve steady goodput consistently and worsens bursty tails |

**Overall classification: CASE C — no meaningful improvement**, with elements of
CASE D (OFF is clearly worse in bursty r8 s2 and in TTFT tails generally).

`--overlap-migration` is **deprioritised as the primary cause.** The prime
suspect inherited from `../00_project/HANDOVER.md` §3.1 does not survive its first direct test.

## 12. Remaining unknowns

- **What actually produces the 3-on-1 placement imbalance.** It persists with the
  overlap path off, so it comes from the placement decisions themselves
  (Algorithm 1 / KVPR), not from how migrations are executed.
- Why Prism's TPOT median sits 5–20% above Prototype even at matched placement.
- The untimestamped request path (Phase 2 §4) — still unresolvable without a
  request id in the client dump.
- Whether the guard OFF removes (skip source deactivation on failed activation)
  contributed to bursty r8 s2's degradation. No activation failure was observed
  in the logs, so this is unproven either way.

## 13. Recommended next step

**Investigate Algorithm 1 / KVPR placement itself, using the artifacts already
on disk — no new benchmark.** Every Prism run records
`[PAPER-ALG1-V4] {"cycle":…, "line8":[…], "kvpr":…, "chosen_gpu":…}` per cycle.
That trace can answer, offline: what objective value makes KVPR choose a GPU that
already holds two models, how often the chosen GPU differs from the balanced one,
and whether the imbalance is a stable attractor or oscillation. This is the
question the diagnostic just promoted from third place to first, and it costs no
GPU time.

Only after that, if the placement trace shows a specific decision rule
responsible, consider a targeted placement experiment. A full 20-condition
overlap ablation is **not** recommended — this 8-run diagnostic already shows the
effect is inconsistent and not in the hypothesised direction.
