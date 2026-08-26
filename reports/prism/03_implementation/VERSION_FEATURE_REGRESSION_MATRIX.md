# Version → feature → regression matrix

Built from the patch layers in `patches/`, the frozen runtime worktree, and
`HANDOVER.md` (the V4/V5/V5_2 hand-off written on the previous machine). No
benchmark was run and nothing was modified.

Feature→layer mapping is by literal search of each patch layer for the flag or
symbol that implements it, so "first layer containing it" is evidence, not
recollection. Layers apply in order: `paper_faithful` (V2) → `_v3` → `_v4` →
`_v5_2` → `_v6` → `_tp` → `baseline_readiness` → `final_baseline_ready`.

## Feature matrix

| feature | first layer | purpose | changes scheduler/lifecycle path? | in frozen runtime |
|---|---|---|---|---|
| Algorithm 2 Moore-Hodgson | **paper_faithful (V2)** | performance (deadline arbitration) | yes — admission ordering | yes |
| `--overlap-migration` | **paper_faithful_v3** | performance (hide transfer) | **yes — control action blocks on engine readiness** | **yes (active)** |
| `--parallel-model-loading` | **paper_faithful_v3** | performance (load throughput) | yes — activation path | yes |
| Algorithm 1 KVPR `kvpr-global-v4` | **paper_faithful_v4** | performance (placement) | yes — placement decisions | yes (active) |
| profiled `c_i` | paper_faithful_v4 | correctness of Alg1 input | no | yes |
| NVLink P2P migration | paper_faithful_v4 | performance | no (transport only) | yes |
| page-lock / loading optimisation | paper_faithful_v4 | performance | no | yes |
| KV migration `--enable-kv-migration` | **paper_faithful_v6** | paper fidelity | yes — migration lifecycle | **yes (active)** |
| TP support / slot plan | paper_faithful_tp | paper fidelity | yes — placement | yes (code present) |
| anti-affinity | paper_faithful_tp | paper fidelity | yes — placement constraint | yes (code present) |
| **GPU-scoped backend queues** | **baseline_readiness / final_baseline_ready** | **correctness** | **yes — queue keying** | yes |
| staged request return | final_baseline_ready | **correctness** | **yes — request representation** | yes |
| ownership / ledger / frontier | baseline_readiness | correctness | yes | yes |
| deactivation rollback | baseline_readiness | correctness | yes | yes |
| worker pool | pre-existing (upstream) | — | yes | yes |

Not found as literal symbols in any layer: `source-active` overlap, `tp_overlap`
(these are described in prose but implemented under other names; see caveats).

## Historical performance points (from `HANDOVER.md` §1.2)

6-model era, Joint-SLO goodput, **prototype vs V3 vs V4**:

| condition | prototype | V3 | V4 |
|---|---:|---:|---:|
| bursty 8 | **2.53 ± 2.27** | 1.46 ± 0.07 | 1.37 ± 0.20 |
| bursty 20 | 0.56 ± 0.35 | **4.46 ± 3.25** | **4.21 ± 3.40** |
| steady 8 | **0.84 ± 0.58** | 0.65 ± 0.11 | 0.53 ± 0.07 |

## Answers to the specific questions

**A. Was V4's win really the V4 architecture?**
**No — DISPROVEN as an architectural claim.** `HANDOVER.md` §1.2 states V3 and
V4 are *indistinguishable in every condition* ("V3 와 V4 는 어느 조건에서도
구분되지 않는다"), even though V4 doubled transfer bandwidth (16.8 vs 8.3 GB/s
end-to-end). The win is not attributable to the V4 layer.

**B. Or was it the r20 overload regime?**
**STRONGLY SUPPORTED.** The sign flips with load: paper-faithful loses at
bursty 8 and steady 8 and wins only at bursty 20, which the same document places
at 2–4× the configuration's saturation point (5–10 req/s). Seed variance is
70–80% of the mean there, and the document explicitly warns against quoting the
mean ratio alone. **Our 4-HET sweep stopped at r10 — entirely inside the regime
where V3/V4 also lost.**

**C. Which post-V4 features could increase request waiting?**
Named by the previous analysis itself, and all active in our runs:
1. **`--overlap-migration` readiness barrier** (V3) — "제어 액션이 엔진 완료까지
   블로킹되고 그동안 컨트롤러가 다른 결정을 못 한다", and §3.1 explicitly says
   *the next ablation should be an arm with this flag removed*. **That ablation
   was never run, and the flag is active in our Prism arm.**
2. **Deactivation blocking** (§3.2) — median 0.90 s but mean 3.45 s and max
   **15.38 s**, all engine wait, not control path.
3. **KV migration** (V6) and **GPU-scoped queues / staged return**
   (baseline_readiness → final) — correctness features added *after* the V4
   measurements, so no V4-era performance point isolates them.

**D. Same mechanism as today's degradation?**
**STRONGLY SUPPORTED.** §3.1 describes: identical throughput across arms, zero
deferrals in steady, a uniform **5–6% per-request latency penalty** (TTFT p50
106.5 → 112.7 → 117.3 ms), and "goodput 이 SLO 임계 지표라 5% 지연이 23%
goodput 손실로 증폭된다". Phase 2 rediscovered exactly this shape independently
in the 4-model data: identical throughput (9.68 vs 9.68), ITL p50 ratios
1.00–1.39, and a 37% goodput loss driven by the SLO threshold. Same mechanism,
different workload and model count.

**E. TP overlap — which version, and does it matter here?**
TP support and anti-affinity first appear in **`patches/paper_faithful_tp`** and
are present in the frozen runtime (`PAPER-FAITHFUL-TP` markers in
`gpu_scheduler.py`, `tp_slots.py`, 250 lines). The slot plan is constructed at
startup (`gpu_scheduler.py:190`). **All four models in this evaluation are
`tp_size=1`**, and `tp_slots.py:159` notes the anti-affinity constraint is
"satisfied by construction" in that case. **Assessment: present but not exercised
— no evidence it affects the 4-HET result. Confidence: SUGGESTIVE**, because
inertness at TP=1 was inferred from the code and the config, not measured by an
ablation.

## Caveats

- The V3/V4 numbers are 6-model, a different mix and rate grid; they are context,
  not a paired control for the 4-model evaluation.
- No V2-only (Algorithm 2 without V3's flags) performance point is available in
  `HANDOVER.md` §1.2, so **V2→V3 cannot be separated from V3→V4 by that table**.
  The claim "the medium-load loss first appears at V3" is therefore
  **INCONCLUSIVE**, not proven: V3 is the earliest layer with a measurement, not
  necessarily the earliest layer with the regression.
