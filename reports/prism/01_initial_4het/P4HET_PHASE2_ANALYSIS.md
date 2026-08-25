# Prism 4-HET — Phase 2 causal analysis

Analysis only. **No benchmark was run; no runtime, scheduler, policy, τ, c_i,
primary SLO threshold or raw artifact was modified.** Scripts: `exp/analysis/`.
Phase 1 (`P4HET_CAUSAL_ANALYSIS.md`) is left intact; this document supersedes
two of its conclusions and says so explicitly.

Confidence labels: **PROVEN** (follows from the artifacts alone) ·
**STRONGLY SUPPORTED** · **SUGGESTIVE** · **INCONCLUSIVE** · **DISPROVEN**.

---

## 1. Executive summary

1. **The steady −36.9% is mostly a threshold-cliff artifact, but not entirely.**
   Loosening the TPOT SLO by 20% cuts the gap from −38.9% to −24.9%; at 2.0×
   only −6.5…−11.2% remains. Scaling TTFT instead changes nothing (−38.9% →
   −38.6%). **~75% of the headline steady gap is amplification; a real residual
   deficit of roughly 7–11% survives any reasonable threshold.** *(PROVEN for the
   scaling arithmetic; the split is definitional, not causal.)*

2. **The steady TPOT deficit is caused by placement imbalance, not by migration
   events.** Prototype holds **exactly 2.00 models per GPU, 100% of the run,
   zero migrations**. Prism spends **30–56% of the run with 3 models on one GPU**
   (and 1 on the other) at identical total residency (~4.0). Three models
   contending for one A100 slows decode continuously — which is why state
   transitions explain only 14–23% of long-gap *time*. *(STRONGLY SUPPORTED)*

3. **The no-scheduler-timestamp path cannot be resolved to identity with the
   existing artifacts** — the per-request dumps carry **no request id** and the
   Algorithm 2 JSONL carries only round aggregates. Code evidence favours
   metadata loss over a real bypass. *(MIXED — leaning METADATA_LOSS; identity
   level INCONCLUSIVE)*

4. **A large share of the tail is literally unattributable.** 54–93% of steady's
   tail TTFT-seconds and 32–64% of bursty r2/r4/r10's sit in the untimestamped
   segment. Any claim about "where the tail comes from" in those conditions is
   currently unfalsifiable. *(PROVEN as a limitation)*

5. **This is not new.** `../00_project/HANDOVER.md` §3.1, written on the previous machine for
   the 6-model V4 study, already recorded the same signature — identical
   throughput, zero deferrals, a uniform 5–6% latency penalty, and "goodput 이
   SLO 임계 지표라 5% 지연이 23% goodput 손실로 증폭된다". Phase 2 rediscovered it
   independently. *(STRONGLY SUPPORTED)*

6. **The prime suspect named by that earlier analysis was never ablated and is
   active in our runs**: `--overlap-migration`. *(PROVEN that it is active;
   its effect is untested.)*

---

## 2. SLO cliff sensitivity (TASK 1)

`exp/analysis/slo_sensitivity.{csv,json}`. Primary SLO (1.0×) is unchanged and
reported alongside; these are sensitivity-only recomputations from the frozen
per-request dumps.

Goodput Δ% (Prism vs Prototype), **scaling TPOT only**:

| rate | 0.8× | **1.0× (primary)** | 1.2× | 1.5× | 2.0× |
|---:|---:|---:|---:|---:|---:|
| steady 2 | −46.9% | **−25.8%** | −11.0% | −4.9% | −4.5% |
| steady 4 | −44.0% | **−33.9%** | −18.0% | −9.1% | −7.4% |
| steady 6 | −21.2% | **−38.9%** | −24.9% | −11.9% | −6.5% |
| steady 8 | −13.0% | **−41.6%** | −31.0% | −16.6% | −11.2% |
| steady 10 | −10.1% | **−35.6%** | −22.8% | −14.1% | −8.8% |

**Scaling TTFT only** (steady): −25.8 / −33.9 / −38.9 / −41.6 / −35.6 at 1.0×
becomes −25.8 / −33.3 / −38.6 / −41.2 / −35.1 at 2.0×. **Flat.**

Bursty is flat on both axes (−2…−5% throughout, no cliff).

Two consequences:

- **The steady gap is a TPOT-threshold phenomenon.** TTFT is irrelevant to it
  *(PROVEN — doubling the TTFT allowance moves the gap by <0.6 points)*.
- **The cliff is steep for both arms.** At 0.8× the *prototype* also collapses
  (steady 6: goodput 11.22 → 4.59), which is why the gap *narrows* there. The
  operating point sits on a shared cliff, not a Prism-specific one
  *(STRONGLY SUPPORTED)*.

**Answer to "does moving the threshold a little shrink −36.9% a lot?": yes.**
+20% TPOT slack removes about a third of it; +50% removes about two-thirds.
A residual ≈7–11% deficit persists at 2.0× and is not an artifact.

---

## 3. Steady TPOT / ITL root cause (TASK 2)

`exp/analysis/tpot_itl_forensics.{csv,json}`, `gap_transition_overlap.json`,
`residency_load.json`.

Long-gap threshold is **data-derived, not chosen**: for each (workload, rate,
model) it is the *prototype arm's own ITL p99* in that condition, so the control
defines "normal decode spacing under this load".

### 3.1 Median vs tail (steady)

| rate | model | ITL p50 Proto→Prism | ratio | ITL p99 ratio | Prism long gaps | % of decode time |
|---:|---|---:|---:|---:|---:|---:|
| 6 | Qwen2.5-7B | 0.0222 → 0.0309 | **1.39** | 1.54 | 6520 | **15.2%** |
| 8 | Llama-3.1-8B | 0.0242 → 0.0290 | 1.20 | 1.43 | 6333 | 14.7% |
| 8 | Qwen2.5-7B | 0.0227 → 0.0266 | 1.17 | 1.44 | 7171 | **22.1%** |
| 10 | Qwen2.5-7B | 0.0235 → 0.0289 | 1.23 | 1.63 | 10377 | **21.7%** |
| 10 | Llama-3.2-3B | 0.0225 → 0.0229 | **1.02** | 1.19 | 5229 | 9.0% |

**Both**: a modest median shift (1.00–1.39×) *and* long gaps consuming 9–22% of
decode time. The deficit is **concentrated on Qwen2.5-7B** and mildest on
Llama-3.2-3B. *(PROVEN from the ITL distributions.)*

### 3.2 Are the long gaps migration events? Mostly not.

| cond | long gaps | on a transition | obs% | chance% | excess | share of gap **time** |
|---|---:|---:|---:|---:|---:|---:|
| steady 6 | 15796 | 179 | 1.1% | 0.3% | 3.3× | 15% |
| steady 8 | 21738 | 237 | 1.1% | 0.7% | 1.6× | 20% |
| steady 10 | 24337 | 202 | 0.8% | 0.6% | 1.4× | 16% |
| bursty 8 | 13970 | 214 | 1.5% | 0.8% | 1.9× | 17% |

Gaps that coincide with a transition are real (1.4–4.2× above chance) and are
individually long (1% of gaps carry 14–23% of gap time) — but **77–86% of
long-gap time happens between transitions**. *(STRONGLY SUPPORTED)*

### 3.3 The actual mechanism: placement imbalance

Time-weighted models resident per GPU, over the request window:

| cond | arm | GPU0 | GPU1 | **time with ≥3 on one GPU** |
|---|---|---:|---:|---:|
| steady 2 | Prototype | 2.00 | 2.00 | **0.0%** |
| steady 2 | **Prism** | 1.98 | 2.04 | **56.4%** |
| steady 8 | Prototype | 2.00 | 2.00 | **0.0%** |
| steady 8 | **Prism** | 1.84 | 2.19 | **53.7%** |
| steady 10 | Prototype | 2.00 | 2.00 | **0.0%** |
| steady 10 | **Prism** | 1.87 | 2.15 | **29.9%** |
| bursty 8 | Prototype | 1.58 | 1.97 | 15.0% |
| bursty 8 | **Prism** | 1.68 | 1.76 | 25.5% |

Prototype's steady placement is **fully static**: 4 activations (one per model),
**0 deactivations, 0 migrations**, verified from the server log. Prism migrates
8–13 times per seed and spends a third to a half of the run with a 3-1 split.

Total residency is identical (~4.0), so this is **not** a memory-capacity effect
— it is **compute contention from imbalance**. It explains what transitions
cannot: a *sustained* decode penalty across the whole run, concentrated on the
largest model sharing a GPU. *(STRONGLY SUPPORTED — mechanism is measured;
"imbalance causes the slowdown" remains observational, since no ablation
isolates it.)*

In bursty, prototype *also* runs imbalanced 9–21% of the time (it deactivates
idle models), so the arms differ far less — consistent with bursty being a tie.

---

## 4. No-scheduler-timestamp lifecycle (TASK 3)

**Verdict: MIXED — leaning METADATA_LOSS. Identity-level: INCONCLUSIVE.**

Evidence **against** a real path bypass:

- `_send_to_backend_queue` (`gpu_scheduler.py:890-916`) is the **only** writer of
  the backend queue key, and it stamps `gpu_scheduler_dispatch_time`
  unconditionally on every request. There is no unstamped route into the engine.
  *(PROVEN by exhaustive search of that key's writers.)*
- `_recv_from_frontend_queue` stamps `gpu_scheduler_queue_time` on every request
  it pops.

Evidence **for** metadata loss:

- The deactivation path (`gpu_scheduler.py:807-820`) returns both waiting and
  already-dispatched backend requests **to the frontend queue**, and the hand-off
  documents a representation change exactly there: a staged request is returned
  "in the backend form it arrived in, not through the admitted-`Req` converter".
- `scheduler.py:2194` reads these fields as
  `getattr(req, "gpu_scheduler_queue_time", None)` — a defence that only makes
  sense if some request form lacks the attribute.
- Empirically, **100% of steady's >5 s no-timestamp tails straddle a state
  transition of their own model** (§5), i.e. they are concentrated exactly where
  the representation change happens.

Why it cannot be closed: **the per-request client dumps contain no request id**
(fields are `arrival_time, ttft, itl, model, slo_*, …` — no `rid`), and the
Algorithm 2 JSONL contains only per-round aggregates. The server logs do carry
`rid`, but there is **no join key** to the client records. A per-request
lifecycle reconstruction is therefore **impossible with these artifacts**, which
is itself a finding: the instrumentation cannot answer the question it raises.

---

## 5. Bursty TTFT tail causal evidence (TASK 4)

Null-model completion (`straddle_null.py`): observed straddles vs those the
window length alone produces (λ = transitions/span, P = 1−e^(−λW)):

| cond | 1–5 s | 5–10 s | >10 s |
|---|---:|---:|---:|
| bursty 2 | **13.1×** | 4.9× | 0.7× |
| bursty 6 | 10.7× | 5.4× | 1.7× |
| bursty 8 | 8.2× | 6.3× | 3.6× |
| steady 2 | **11.2×** | 6.3× | 3.3× |
| steady 8 | 4.6× | 4.7× | 3.0× |

Non-tail requests straddle a transition **0% of the time in all 20 conditions**.
The excess is 1.4–13× in 29 of 30 buckets. *(STRONGLY SUPPORTED — association is
far beyond window-length chance; direction is not established.)*

Tail (>1 s) TTFT-seconds by segment, Prism:

| cond | intake | admission | fetch | prefill | **unattributable** | >10 s wait explained by model-unavailable |
|---|---:|---:|---:|---:|---:|---:|
| bursty 6 | **55%** | 37% | 1% | 0% | 7% | **60%** |
| bursty 8 | **37%** | 26% | 1% | 1% | 35% | **49%** |
| bursty 2 | 9% | 41% | 17% | 1% | 32% | 2% |
| bursty 10 | 11% | 27% | 2% | 1% | **60%** | 3% |
| steady 8 | 6% | 16% | 1% | 1% | **76%** | 0% |
| steady 10 | 13% | 31% | 1% | 1% | **54%** | 0% |

**Classification of bursty tail cause:**

| cause | conditions | share of tail time |
|---|---|---|
| model unavailable / scheduler intake starvation | bursty 6, bursty 8 | 92%, 63% (intake+admission), with 60%/49% of >10 s wait overlapping genuine unavailability |
| dispatch / engine-fetch waiting | bursty 2 only | 17% |
| **unknown (untimestamped)** | bursty 2/4/10, all steady | **32–93%** |

*(PROVEN for the segment split; the "unknown" row is the honest majority in most
conditions.)*

---

## 6. Version-by-version implementation history (TASK 5)

Full matrix in **`../00_project/VERSION_FEATURE_REGRESSION_MATRIX.md`**. Headlines:

- **V4's advantage was regime-specific, not architectural.** `../00_project/HANDOVER.md` §1.2:
  V3 and V4 are indistinguishable in every condition despite V4 doubling
  transfer bandwidth; paper-faithful **loses** at bursty 8 (1.37–1.46 vs 2.53)
  and steady 8 (0.53–0.65 vs 0.84) and wins only at bursty 20 (4.21–4.46 vs
  0.56), 2–4× past saturation, with seed variance 70–80% of the mean.
  *(A: DISPROVEN as architectural. B: STRONGLY SUPPORTED as regime.)*
- **Our 4-HET sweep (r2–r10) never entered the regime where paper-faithful won.**
  *(PROVEN.)*
- **`--overlap-migration` (introduced V3) is active in our Prism arm and absent
  from Prototype**, and §3.1 named its readiness barrier as the prime suspect and
  prescribed removing it as the next ablation — never done. *(PROVEN active.)*
- **Deactivation blocking**: median 0.90 s, mean 3.45 s, **max 15.38 s**, all
  engine wait (§3.2), matching today's long-gap and intake findings.
  *(STRONGLY SUPPORTED as the same mechanism.)*
- **TP overlap**: first in `patches/paper_faithful_tp`, present in the frozen
  runtime, but all four models are `tp_size=1` and anti-affinity is "satisfied by
  construction" there. *(SUGGESTIVE that it is inert here — inferred from code,
  not ablated.)*

---

## 7. Regression candidate matrix

| candidate | first layer | active in our Prism arm? | evidence it could cost latency | rank |
|---|---|---|---|---|
| `--overlap-migration` readiness barrier | V3 | **yes** | named suspect in HANDOVER §3.1; blocks controller during engine completion | **1** |
| placement imbalance from migration (3-on-1) | V4 (Alg1) | yes | measured: 30–56% of steady run imbalanced vs 0% prototype | **1** |
| deactivation engine-wait up to 15.4 s | V3-era | yes | HANDOVER §3.2; matches long gaps and intake | 2 |
| KV migration | V6 | yes | added after all V4 measurements; no isolating point exists | 3 |
| staged-return / GPU-scoped queues | final | yes | correctness fixes; implicated in untimestamped path | 3 |
| Moore-Hodgson admission | V2 | yes | **ruled out earlier**: 3482/3483 admitted, +0.001 ms/iteration | — |
| TP overlap / anti-affinity | tp | code only | all models TP=1 | — |

---

## 8. Proven

- Scaling TTFT SLO to 2.0× changes the steady gap by <0.6 points; scaling TPOT to
  2.0× removes ~75% of it.
- Prototype's steady placement is fully static (4 activations, 0 deactivations,
  0 migrations); Prism spends 30–56% of steady with 3 models on one GPU.
- Prism's added TTFT is entirely pre-engine; prefill is identical (0.035–0.052 s
  both arms).
- Non-tail requests straddle a state transition 0% of the time in all 20
  conditions.
- The per-request dumps carry no request id, so identity-level lifecycle
  reconstruction is impossible with existing artifacts.
- `--overlap-migration` is active in Prism and absent from Prototype.
- `_send_to_backend_queue` is the sole backend-queue writer and always stamps.

## 9. Disproven

- ~~"Steady is worse because migration is pure overhead there."~~ Steady migrates
  as much or more and moves more bytes, with **zero** residency gaps.
- ~~"More migrations → worse tail."~~ corr(migrations, TTFT p99) = **−0.42**.
- ~~"τ↓ → … → goodput↓."~~ Calibration shows the opposite sign
  (corr(migrations, goodput) = +0.48), and HANDOVER §2 records that raising τ on
  the previous box **collapsed** bursty-20 goodput 4.21 → 0.30.
- ~~"The deficit is a TTFT-tail problem."~~ True for bursty 6/8 only; steady's
  loss is 82–87% TPOT and TTFT scaling does not touch it.
- ~~"V4's architecture beat the prototype."~~ V3 ≡ V4 in every condition; the win
  is the r20 regime.
- **Phase 1 correction**: Phase 1 attributed the steady tail primarily to
  requeue/residency effects. §3.3 supersedes that — **placement imbalance**, not
  transition events, dominates.

## 10. Still unknown

- Whether the untimestamped requests lose metadata or take another path
  (**blocked by missing request ids**) — 54–93% of steady tail time sits there.
- Whether the 3-on-1 imbalance *causes* the decode penalty, or both follow from a
  third factor. No ablation isolates it.
- Whether `--overlap-migration` contributes measurably. Never ablated.
- Whether the V2→V3 boundary is where medium-load loss begins: no V2-only
  performance point exists in the surviving documents.
- Whether Prism recovers the crossover at r12–r20 in the 4-model mix.

---

## 11. Ranked next experiments

**1. `--overlap-migration` ablation (Prism, flag off), 20 runs, ~3.5 h.**
The highest-value experiment: it is the suspect the previous study named and
prescribed, it was never run, it is active in our arm and absent from the
control, and it is a **flag removal, not tuning** — τ, c_i, SLO and workloads all
stay frozen. It cleanly separates "Prism policy" from "the readiness barrier that
policy is implemented on top of". Pair it per condition against the existing runs.

**2. Extreme bursty replication — bursty r12 / r16 / r20, both arms, 12 runs, ~2 h.**
Framed explicitly as **reproduction of the previously observed saturation
crossover regime**, not as looking for a workload Prism wins. Justification is
documentary and specific: `../00_project/HANDOVER.md` §1.2 shows the sign flips only past
saturation, and our sweep stopped at r10. This tests whether the crossover
survives the 4-model mix and the current runtime. It must be reported whatever
the outcome, and r20's 70–80% seed variance means ≥3 seeds or it proves nothing.

**3. Instrumentation experiment — request-id join, 2 runs (steady r8), ~20 min.**
Add a request id to the per-request dump (client-side record only; **no scheduler
or policy change**) so the untimestamped path can be resolved. Without it, 54–93%
of steady tail time stays unattributable and TASK 3 cannot be closed. Small,
cheap, and it unblocks the only genuinely open mechanism question.

**Not recommended now**

- **τ local sweep — NO.** τ failed the directional test in Phase 1
  (corr(migrations, goodput) = +0.48), HANDOVER §2 records that suppressing
  migration collapsed goodput 4.21 → 0.30, and seed variance at fixed τ (8×)
  exceeds the entire across-τ spread. The 200× gap between 0.00035 and 0.07 is
  not, by itself, a reason.
- **Migration ablation via τ=∞ — deprioritised** in favour of (1). τ=∞ changes
  placement, residency, queueing and KV pressure simultaneously, so a null result
  would be uninterpretable; the flag ablation is the cleaner cut. Reconsider it
  after (1).

I have stopped here and made no further changes.
