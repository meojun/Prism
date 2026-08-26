# Final 4-HET evaluation — untouched hold-out

```
FINAL_BASELINE_STATUS = VERIFIED
VALID_RUNS  = 40/40    lifecycle gate 40/40 PASS
TRACE_PAIRING = 20/20 byte-identical
4HET_E2E_RESULT = PROTOTYPE_WIN
```

`FINAL_BASELINE_STATUS = VERIFIED` means a frozen baseline was cleanly
evaluated. **It does not mean Prism won.** In this regime it lost.

---

## 1. Result

Seeds 5 and 6, never used for tuning. Every parameter frozen before these runs:
FINAL_TAU 0.012859417696566448, window 60 s, cooldown 30 s, runtime tree
`7fbd431c6a636df0c72bb6a40324f851d002d204`.

### Steady

| rate | Prototype | Prism | Δ | TTFT ok P→S | TPOT ok P→S | migr |
|---:|---:|---:|---:|---|---|---:|
| 2 | 1.8656 | 1.7899 | −4.1 % | 100.0 → 99.2 % | 99.2 → 96.3 % | 0.5 |
| 4 | 3.8321 | 2.7779 | **−27.4 %** | 99.9 → 98.8 % | 98.4 → 72.1 % | 1.5 |
| 6 | 5.5972 | 5.0959 | −9.0 % | 99.8 → 99.0 % | 95.0 → 86.9 % | 1.0 |
| 8 | 6.7455 | 4.2929 | **−36.3 %** | 100.0 → 98.2 % | 86.4 → 55.9 % | 2.0 |
| 10 | 6.9595 | 5.5935 | −19.6 % | 99.7 → 97.1 % | 71.1 → 57.7 % | 2.0 |
| **mean** | **5.0000** | **3.9100** | **−19.3 %** | | | |

### Bursty

| rate | Prototype | Prism | Δ | TTFT ok P→S | TPOT ok P→S | migr |
|---:|---:|---:|---:|---|---|---:|
| 2 | 1.8678 | 1.6846 | −9.8 % | 99.9 → 98.1 % | 99.4 → 92.0 % | 5.5 |
| 4 | 3.7592 | 2.9635 | −21.2 % | 99.7 → 95.1 % | 96.6 → 79.6 % | 8.5 |
| 6 | 4.8678 | 3.6023 | −26.3 % | 99.1 → 96.1 % | 83.1 → 62.9 % | 9.0 |
| 8 | 6.3020 | 4.3724 | −29.7 % | 99.5 → 92.9 % | 80.9 → 59.5 % | 9.0 |
| 10 | 5.8898 | 3.7639 | **−36.1 %** | 98.7 → 93.2 % | 59.9 → 40.9 % | 8.5 |
| **mean** | **4.5373** | **3.2773** | **−24.6 %** | | | |

```
OVERALL   Prototype 4.7686   Prism 3.5937   −24.6 %
```

**Prism loses in all 20 conditions.** There is no rate and no workload at which
it wins.

---

## 2. Why — the trade that works in many-model does not work here

In the Prism-favorable many-model regime, Prism sacrificed TTFT and bought TPOT.
Here it sacrifices TTFT and buys **nothing**: TPOT attainment falls too, in every
condition, and by far more than TTFT does.

| | TTFT loss | TPOT loss |
|---|---:|---:|
| steady r4 | −1.1 pp | **−26.3 pp** |
| steady r8 | −1.8 pp | **−30.5 pp** |
| bursty r8 | −6.6 pp | **−21.4 pp** |
| bursty r10 | −5.5 pp | **−19.0 pp** |

TPOT is the binding constraint here as it was in many-model, so a large TPOT
regression translates directly into the Joint-SLO loss.

This is the same mechanism the earlier placement forensic identified. In the
4-model setting, KVPR's optimum frequently co-locates Llama-3.1-8B and
Qwen2.5-7B — memory-optimal, compute-hostile. The Prototype's static 2+2
placement happens to separate them. Prism gives that separation up in exchange
for lower peak KVPR, and pays for it in decode throughput. Nothing in the
corrected estimator, the 60 s window or the recalibrated τ changes that: they
made the planner accurate and stable, and a stable planner pursues the same
memory-only objective more consistently.

## 3. Migration cost

| workload | Prism migrations (mean) | bytes moved |
|---|---:|---:|
| steady | 1.4 | 12.2 GB |
| bursty | 8.1 | **80.1 GB** |

The Prototype performs zero migrations. Bursty's 80 GB of weight and KV movement
per 420 s run buys a **−24.6 %** goodput outcome — the clearest single statement
of the problem in this regime.

---

## 4. Validity

40/40: rc = 0, verdict PASS, **0 aborted**, **0 Algorithm-2 order violations**,
**0 lifecycle validity failures**, and **no retries were needed** — the entire
40-run matrix ran clean on first attempt. Prototype and Prism consumed
byte-identical traces in all 20 conditions.

## 5. Interpretation

This is an **applicability-boundary result**, not a correctness result. The
implementation is paper-faithful on every mechanism that was verifiable, its
correctness infrastructure is verified, and it executes Algorithm 1 exactly as
specified. The algorithm's objective simply does not serve this regime:

- four models, low consolidation — little for memory ballooning to reclaim
- all models continuously active — no idle memory to harvest
- two large models that must not share a GPU for compute reasons the objective
  cannot see

Retained from the earlier finding, and **not** relabelled as a bug: in 4-HET,
Algorithm 1 can and does choose a memory-optimal, compute-hostile large-large
co-residency. That is the specified algorithm operating correctly on the wrong
regime.

Machine-readable: `exp/analysis/final_4het/` — `run_manifest.csv`,
`condition_summary.csv`, `paired_goodput.csv`.
