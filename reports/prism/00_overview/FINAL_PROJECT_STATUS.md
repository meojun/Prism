# Prism reproduction — final project status

One page. Everything else is reachable from
[`../REPORT_INDEX.md`](../REPORT_INDEX.md).

## What was implemented?

A paper-faithful reproduction of Prism (OSDI 2026) on 2 × A100-80GB: Algorithm 1
(KVPR global placement), Algorithm 2 (Moore-Hodgson local arbitration), the
`token_rate` definition from §4, the sliding-window moving average from
Appendix A.4, TPOT-weighted KVPR, `shared_kv`, elastic KV via kvcached, and the
~45 s idle-eviction threshold. Compared against the released prototype
(`--policy simple-global`) on byte-identical paired traces.

## Was it paper-faithful?

On every mechanism verifiable against the camera-ready, yes. Two fidelity gaps
were found and closed; one remains, deliberately:

- `token_rate`'s decode half was not windowed — **corrected**
- the monitoring window was 30 s where A.4 specifies ~60 s — **corrected** (our
  design doc had recorded A.4 as absent, because the arXiv version has no A.4)
- τ has no numeric value in the paper — **calibrated**, methodology documented

## What was corrected?

| | status |
|---|---|
| token-rate estimator windowing | FIX VERIFIED |
| 60 s window adopted for fidelity | ADOPTED (it did *not* improve this workload) |
| migration-lifecycle failure containment | FIX VERIFIED |
| τ recalibration | FINALIZED |
| `nccl_port` check-then-use race | **FOUND, NOT FIXED** — fixing it after the baseline freeze would be a runtime change mid-experiment |

## What was validated?

108 authoritative runs, **all audited offline**: 48 τ + 20 many-model + 40 final
4-HET. Every one: `rc=0`, verdict PASS, 0 aborted, 0 Algorithm-2 violations,
lifecycle gate PASS, trace SHA256 matching the frozen manifest. All 30 paired
conditions byte-identical between arms. 16/16 targeted correctness tests.

## What was FINAL_TAU?

```
FINAL_TAU = 0.012859417696566448    (pooled positive-Δr P50)
window 60 s, cooldown 30 s
```

Chosen by a pre-declared mechanistic rule; goodput was used for neither selection
nor tie-breaking. The historical 0.00035 proved **too permissive** — worse on
both residual regret and migration cost.

## What did many-model show?

In a workload **deliberately built to favour Prism**: **+7.9 %** overall, 7/10
conditions won, peaking at **+31.6 %** at r8 — but **−3.6 %** at r2 and
**−14.8 %** at r10. Prism trades TTFT (worse in 10/10 conditions, p99 20–23×) for
TPOT, and Joint-SLO here is TPOT-bound.

## What did 4-HET show?

On the untouched hold-out (seeds 5/6): **−24.6 %** overall, **0/20 conditions
won**; steady −21.8 %, bursty −27.8 %. Here Prism loses TTFT *and* TPOT.

## Why do the regimes differ?

Objective-function mismatch. KVPR optimises **memory pressure**, not serving
performance, and has no compute-interference term. In 4-HET the KVPR optimum
co-locates the two largest models in 24.3 % of cycles (up to 50.3 %) — at every
critical cycle it was rank 1 of 14 valid placements. The Prototype's static
placement happens to separate them. Whether large-large is optimal is decided by
which model tops the weighted-demand ranking: small-model rank-1 → 57.6 %,
large-model rank-1 → 0.7 %, an 82× separation.

The estimator correction made the planner accurate and stable — and a stable
planner pursues that same memory-only objective more consistently, which in
4-HET makes things worse, not better.

## Main limitations

Two GPUs. Two seeds per condition — no significance claimed. Synthetic
workloads; the favourable regime is bursty-only. The `nccl_port` race is unfixed.
The trigger behind the lifecycle stall remains INCONCLUSIVE.

## Where are the authoritative results?

| | |
|---|---|
| reports | `reports/prism/` — start at `REPORT_INDEX.md` |
| tables/figures | `exp/analysis/final_summary/` (+ its `README.md`) |
| raw runs | `exp/results/{4het-tau-final, many-model-final, 4het-final}/` |
| frozen baseline | `exp/manifests/prism_final/FINAL_BASELINE_FROZEN.json` |

`PERFORMANCE_EVALUATION_CLOSED = true`.

## How do I reproduce them?

`repro/prism_final/` — `bootstrap.sh`, `verify_environment.sh`,
`verify_artifacts.sh`, `smoke_test.sh`, `resume.sh`. Full instructions:
`reports/prism/11_handoff/PRISM_SERVER_HANDOFF.md`, section 60.

## What should the next researcher do?

1. Fix the `nccl_port` check-then-use race and re-freeze the baseline.
2. Then, if pursuing the science: build an **interference-aware placement
   variant** that prices compute contention alongside memory pressure. Label it a
   variant — never fold it into the paper-faithful baseline.
3. Test the applicability boundary directly: sweep consolidation ratio and
   active-set shift rate to find where the +7.9 % and −24.6 % regimes meet.
