# Prism-favorable many-model stress evaluation

```
REGIME  = PRISM_FAVORABLE_STRESS_REGIME
RUNS    = 20/20 valid   lifecycle gate 20/20 PASS
OUTCOME = OUTCOME_A (mechanism + performance advantage) — but load-bounded
```

---

## 1. Executive summary

In a workload **deliberately constructed to favour Prism**, Prism beats the
released prototype overall by **+7.9 %** on Joint-SLO goodput. The advantage is
not uniform: it forms an inverted U across offered load.

| rate | Prototype | Prism | paired Δ |
|---:|---:|---:|---:|
| 2 | 1.4868 | 1.4201 | **−3.6 %** |
| 4 | 2.3541 | 2.5386 | +7.9 % |
| 6 | 2.3570 | 2.8154 | +19.7 % |
| 8 | 2.1952 | 2.8932 | **+31.6 %** |
| 10 | 2.7543 | 2.3566 | **−14.8 %** |
| **overall** | **2.2295** | **2.4048** | **+7.9 %** |

Prism wins in a middle band (r4–r8) and loses at both ends. The mechanism is
consistent throughout: **Prism trades TTFT for TPOT**, and Joint-SLO in this
regime is dominated by TPOT. Where the trade pays, Prism wins; where TPOT stops
improving, it loses immediately.

---

## 2. Explicit best-case framing

This workload was **designed to favour Prism** and must never be presented as a
representative production distribution:

- six models on two GPUs — high consolidation, exactly Prism's target setting
- 90 % of demand on a moving hot pair, 10 % across the other four
- hot set rotates A → B → C in 180 s phases, chosen so the 60 s estimator can
  observe the new phase, Algorithm 1 can respond, a migration can complete, and
  there is still time to amortise its cost
- hot pairs constructed from **static** metadata only, deliberately separating
  the two largest models so the workload does not build in the compute-hostile
  large-large pairing found in 4-HET

**If Prism wins here that is not evidence of universal superiority.** The
correct reading is an applicability upper bound. Section 11 of this report
records where it loses even here.

---

## 3. Six-model set

Recovered unambiguously from `exp/FINAL_BASELINE_MANIFEST.json` with exact
revisions; no model was substituted.

| slot | model | revision | weight GB | KV B/tok |
|---|---|---|---:|---:|
| model_1 | meta-llama/Llama-3.2-1B | `4e20de36…` | 2.2793 | 32768 |
| model_2 | Qwen/Qwen2.5-1.5B-Instruct | `989aa798…` | 3.0078 | 28672 |
| model_3 | meta-llama/Llama-3.2-3B | `13afe512…` | 6.0000 | 114688 |
| model_4 | Qwen/Qwen2.5-3B-Instruct | `aa8e7253…` | 5.8359 | 36864 |
| model_5 | meta-llama/Llama-3.1-8B | `d04e592b…` | 15.0801 | 131072 |
| model_6 | Qwen/Qwen2.5-7B-Instruct | `a09a3545…` | 14.2832 | 57344 |

## 4. Static heavy/light pair construction

Pre-declared before any pilot or performance result existed
(`MANY_MODEL_PAIRING_PREDECLARATION.md`). Borda rank-sum of weight-size rank and
KV-bytes/token rank; ties broken by larger weight. Rank-sum avoids inventing an
exchange rate between GB and bytes/token that could be tuned.

Ranking: model_5, model_6, model_3, model_4, model_2, model_1.
Pairing rank1+rank6, rank2+rank5, rank3+rank4:

```
HOT_SET_A = model_5 (Llama-3.1-8B) + model_1 (Llama-3.2-1B)
HOT_SET_B = model_6 (Qwen2.5-7B)   + model_2 (Qwen2.5-1.5B)
HOT_SET_C = model_3 (Llama-3.2-3B) + model_4 (Qwen2.5-3B)
```

Pairing by model index would have produced `model_5 + model_6` — the exact
large-large co-residency the 4-HET forensic showed to be memory-optimal but
compute-hostile. The declared ranking separates them.

## 5–7. Phases, skew, generator

180 s phases, 540 s trace, 90/10 skew (45 % per hot model, 2.5 % per background
model). Measured on the generated traces: hot pair 89.1 / 89.9 / 89.4 % per
phase, background 2.2–3.1 % each, aggregate rate constant.

The arrival process is the **canonical historical six-model bursty generator,
reused unchanged**. Only phase construction was made deterministic. The default
path was verified byte-identical: regenerating existing 4-HET traces reproduced
`492d79a4a2f34ccc` and `60be4c9d12767865` exactly. No serving runtime was
touched.

---

## 8. Frozen protocol and its amendment

Frozen before the pilot. One amendment was made during execution:

```
OLD_RATE_GRID = [4, 8, 12, 16, 20]
NEW_RATE_GRID = [2, 4, 6, 8, 10]
```

**Reason.** Saturation was already visible around r6–r8, so r12–r20 were largely
drowned regimes with little diagnostic value; and the new grid matches the final
4-HET grid exactly, enabling direct cross-regime comparison at identical offered
rates.

**Timing.** Made while the Prototype arm was partially complete and **before any
final Prism many-model result had been produced or inspected**. No final Prism
result was used to choose between parameter values. Everything else — FINAL_TAU,
window, cooldown, runtime, model set, hot pairs, 90/10, 180 s, A→B→C, burst
generator, SLO, seeds — was unchanged, as was the entire 4-HET protocol.

**Reuse.** Prototype r4 s7/s8 and r8 s7/s8 were audited (verification PASS,
lifecycle gate PASS, trace SHA256 identical to the re-frozen manifest) and reused
rather than re-run.

**r12.** Preserved and **excluded from all final statistics**, marked
`EXCLUDED_FROM_FINAL_GRID_PROTOCOL_CHANGE`. r12 s7 is a complete valid run; r12
s8 was in flight and was stopped. Neither appears in any aggregate here.

This amendment is stated plainly because it is exactly the kind of change that
must not be hidden. Full record: `MANY_MODEL_PROTOCOL_FROZEN.md`.

---

## 9. Diagnostic pilot — and why it was misleading on its own

r16, seed 9, 2 runs, diagnostic only, never in final statistics.

```
Prototype goodput 0.6903   Prism goodput 1.7866   R = 2.5882   PILOT_GATE = PASS
```

R = 2.59 looks like a decisive Prism win. It is not comparable to the final
numbers. At r16 the Prototype's Joint-SLO attainment had collapsed to **4.4 %**,
so the ratio is computed on a near-zero denominator. In the final grid the
largest advantage is **+31.6 % (1.32×) at r8**.

Reading the pilot alone would have overstated Prism's advantage by roughly 3–4×.
This is the clearest argument for the rate-grid amendment: the r12–r20 band
cannot distinguish "Prism is better" from "both arms are drowning, Prism drowns
more slowly".

The gate threshold was lowered from 0.80 to 0.70 **before the pilot ran**
(`PILOT_GATE_THRESHOLDS.json`); the observed 2.59 cleared either threshold.

---

## 10. Run integrity

20/20 valid: rc = 0, verdict PASS, **0 aborted**, **0 Algorithm-2 order
violations**, lifecycle validity gate 20/20 PASS. Prototype and Prism consumed
byte-identical traces at every condition (10/10 pairs verified against the frozen
manifest).

One run failed on first attempt (`prism bursty r8 s7`) with the `nccl_port`
TOCTOU race — the server never started. Classified harness-only, retried once
under section 64 with nothing changed, and the failed attempt preserved as
`.eaddrinuse-attempt1`.

---

## 11–18. Mechanism: what Prism trades

### TTFT — Prism loses in **10 of 10** conditions

| rate | Prototype | Prism | Δ pp |
|---:|---:|---:|---:|
| 2 | 98.7 % | 96.5 % | −2.2 |
| 4 | 98.3 % | 95.4 % | −2.9 |
| 6 | 97.6 % | 92.9 % | −4.7 |
| 8 | 97.9 % | 91.8 % | −6.1 |
| 10 | 96.6 % | 87.9 % | **−8.7** |

No exceptions, and the loss grows monotonically with load.

### TPOT — Prism wins in 8 of 10, loses at both ends

| rate | Prototype | Prism | Δ pp |
|---:|---:|---:|---:|
| 2 | 78.8 % | 76.9 % | −1.9 |
| 4 | 60.0 % | 66.7 % | +6.7 |
| 6 | 40.7 % | 50.4 % | **+9.7** |
| 8 | 28.2 % | 38.4 % | **+10.2** |
| 10 | 28.7 % | 25.6 % | −3.1 |

### TPOT is the binding constraint

Requests failing TPOT-only, at r8: Prototype **70.0 %**, Prism **54.9 %**.
TTFT-only failures never exceed 2.4 % in any condition. Joint-SLO is therefore
almost entirely determined by TPOT, which is why an arm that loses TTFT
everywhere can still win overall.

### The loss is entirely in the tail

TTFT p50 is effectively identical (0.07–0.09 s both arms). TTFT p99 is **20–23×
worse** for Prism (r8: 0.37 s → 8.37 s). Prism does not make the typical request
slower; a small number of requests are badly blocked during migration.

TPOT tells the reverse story where Prism wins: at r6/r8 both p50 and p95 improve
(r8: p50 36.7 → 34.1 ms, p95 71.3 → 66.6 ms) with only p99 worse. At r10 even
p50 degrades (38.1 → 41.1 ms) — the trade has stopped working.

### Migration cost

| rate | Prism migrations | bytes moved | Prototype |
|---:|---:|---:|---:|
| 2 | 5.0 | 28.6 GB | 0 |
| 4 | 3.0 | 16.2 GB | 0 |
| 6 | 5.0 | 35.6 GB | 0 |
| 8 | 5.0 | 34.2 GB | 0 |
| 10 | 5.0 | 34.8 GB | 0 |

The Prototype **never migrates** in this regime — its placement is entirely
static. Prism performs ~5 migrations per 540 s trace, one per active-set
transition plus a small number of corrections, moving ~30 GB. That cost is
roughly constant across load, which is why it is affordable at r6–r8 and not at
r2 (no pressure to relieve) or r10 (no headroom to absorb it).

---

## 19–21. Hypothesis verdicts

| | verdict | evidence |
|---|---|---|
| **H1** resource advantage | **SUPPORTED** | TPOT attainment improves by up to +10.2 pp and TPOT p50/p95 both improve at r6–r8; Prism relieves real KV pressure the static arm cannot |
| **H2** performance translation | **PARTIALLY SUPPORTED** | translation succeeds at r4–r8 (+7.9 to +31.6 %) and fails at r2 (−3.6 %) and r10 (−14.8 %) |
| **H3** load dependence | **SUPPORTED, non-monotone** | benefit rises to r8 then reverses. Monotonicity was not required, and is not observed |
| **H4** active-set adaptation | **SUPPORTED** | ~5 migrations per trace against 3 declared A→B→C transitions, versus 0 for the static arm |

---

## 22. Outcome classification

```
MANY_MODEL_OUTCOME = OUTCOME_A  (mechanism + performance advantage)
```

with the explicit qualifier that the advantage is **load-bounded**: it exists in
r4–r8 and reverses outside that band. This is not OUTCOME_B — the resource
advantage does translate into Joint-SLO where it exists. It is not OUTCOME_C or
D — the opportunity is real and Prism does actuate it.

---

## 23. Limitations

- Two seeds. No population-level significance is claimed. r2 is the least stable
  condition, with seeds disagreeing in sign (+8.1 % / −15.2 %); r10 is more
  robust, with both seeds negative.
- Bursty arrivals only. There is no steady variant of this regime, so
  cross-regime comparison against 4-HET must be made against 4-HET's bursty half.
- Two A100s. Consolidation pressure on a larger fleet may behave differently.
- The workload is synthetic and deliberately favourable, as stated throughout.
- The `nccl_port` race was found and **not** fixed (fixing it mid-matrix would
  have been a runtime change after baseline freeze).

## 24. Best-case interpretation

In its own best case, corrected Prism delivers a **+7.9 % overall** Joint-SLO
goodput advantage, peaking at **+31.6 %** where load is high enough to create KV
pressure but not so high that migration cannot be amortised. It pays for this
with TTFT tail latency in every single condition, and the advantage disappears
below r4 and reverses by r10.

That is the ceiling, measured under conditions chosen to favour it. Section 13
of the integrated report contrasts it with the untouched 4-HET hold-out, where
the same frozen configuration **loses by 24.6 %**.

Machine-readable: `exp/analysis/many_model_prism_favorable/` —
`run_manifest.csv`, `condition_summary.csv`, `paired_goodput.csv`.
