# Prism 4-model paired evaluation — post-hoc causal analysis

Analysis only. No benchmark was run, no runtime or configuration was changed,
τ was not altered, and no raw artifact was modified. Everything below is derived
from the 40 completed runs, their per-request dumps, their server logs, and the
committed calibration manifest. Scripts live in `exp/analysis/`.

---

## 1. Executive finding

**The Prism deficit is not one phenomenon. It is two, and the one that costs the
most is not the TTFT tail.**

| | bursty (−2.0% goodput) | steady (−36.9% goodput) |
|---|---|---|
| dominant SLO failure | TTFT rising to 29–48% of failures (Prototype 10–15%) | **TPOT — 82–87% of failures** |
| model ever unavailable? | **yes**, 421–548 s per condition | **no — 0.00 s, always resident** |
| tail explained by residency gaps | partly (bursty6 60%, bursty8 49% of >10 s wait) | **0%** |
| what actually moves goodput | TTFT tail from residency gaps | TPOT median crossing the SLO threshold |

Two independently supported statements:

1. **In bursty**, Prism's TTFT tail is tied to windows where a model is resident
   on no GPU. Requests then sit in the per-model frontend queue, which the GPU
   scheduler only drains for models in `activating`/`activated`.
2. **In steady**, no model is ever unavailable and the TTFT tail is a minority of
   failures. The loss comes from TPOT: at rate 8–10 the TPOT median of *both*
   arms sits within 1–11% of its SLO threshold, and Prism's slightly slower
   decode pushes a large fraction of requests across it.

**τ is not established as the cause of the deficit, and the available evidence
points the other way** (§8).

---

## 2. Where TTFT time is spent

TTFT is decomposed at the server's own timestamps, recorded per request by the
benchmark client (`benchmark.py:162-189`):

```
arrival → gpu_scheduler_queue → gpu_scheduler_dispatch → out_queue → prefill_finish
  intake        admission(Alg2)        fetch              prefill
```

Mean seconds per completed request, both seeds pooled (`exp/analysis/ttft_decompose.py`):

| cond | arm | TTFT | intake | admission | fetch | prefill | prequeue* | noTS |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| bursty6 | Prototype | 0.133 | 0.024 | 0.009 | 0.053 | 0.048 | 0 | 0 |
| bursty6 | **Prism** | **2.485** | **1.321** | **0.907** | 0.049 | 0.035 | 0.173 | 103 |
| bursty10 | Prototype | 0.140 | 0.023 | 0.011 | 0.056 | 0.052 | 0 | 0 |
| bursty10 | **Prism** | **0.746** | 0.071 | 0.201 | 0.045 | 0.037 | **0.391** | 284 |
| steady8 | Prototype | 0.082 | 0.001 | 0.006 | 0.029 | 0.047 | 0 | 0 |
| steady8 | **Prism** | **0.714** | 0.039 | 0.112 | 0.048 | 0.051 | **0.464** | 240 |
| steady10 | Prototype | 0.084 | 0.001 | 0.006 | 0.029 | 0.048 | 0 | 0 |
| steady10 | **Prism** | **0.770** | 0.090 | 0.225 | 0.048 | 0.049 | **0.358** | 203 |

\* `prequeue` = arrival→out_queue for requests carrying **no scheduler
timestamps at all**; those cannot be split at the scheduler boundary and are
never folded into the other columns. `noTS` counts them.

Two facts stand out:

- **Prefill is identical between arms** (0.035–0.052 s everywhere). The engine is
  not slower under Prism. The added TTFT is entirely pre-engine queueing.
- **Prototype has zero no-timestamp requests in all 20 conditions.** Prism has
  30–284 per condition (1.7–4.0% of requests), and they carry a
  disproportionate share of the tail.

Tail attribution (Prism):

| cond | >5 s tail | of which no-TS | >10 s tail | of which no-TS |
|---|---:|---:|---:|---:|
| steady2 | 28 | **100%** | 15 | **100%** |
| steady4 | 88 | **100%** | 42 | **100%** |
| steady6 | 124 | 98% | 42 | **100%** |
| steady8 | 295 | 75% | 142 | 99% |
| bursty6 | 352 | 25% | 265 | 11% |

In steady the extreme tail is **entirely** the no-scheduler-timestamp path. In
bursty it is mostly the timestamped path with a large `intake` — a different
mechanism.

---

## 3. Tail-request forensic evidence

For every completed request we counted activate/deactivate events **of that
request's own model** falling inside its pre-engine window
(`exp/analysis/transition_overlap.py`):

| cond | ≤1 s requests | straddle a transition | >5 s | straddle | >10 s | straddle |
|---|---:|---:|---:|---:|---:|---:|
| bursty6 | 4616 | **0%** | 87 | 85% | 265 | 88% |
| bursty8 | 6131 | **0%** | 140 | 99% | 227 | 90% |
| steady4 | 3137 | **0%** | 46 | **100%** | 42 | **100%** |
| steady6 | 4876 | **0%** | 82 | **100%** | 42 | **100%** |
| steady8 | 6249 | **0%** | 153 | 72% | 142 | **100%** |

**Non-tail requests straddle a transition 0% of the time in all 20 conditions.**

A longer window mechanically has more chance of containing an event, so this was
tested against an explicit null: transitions of a model arrive at rate
λ = transitions / run-span, giving P(≥1 in a window of W) = 1 − e^(−λW)
(`exp/analysis/straddle_null.py`).

| cond | 1–5 s obs/exp | 5–10 s obs/exp | >10 s obs/exp |
|---|---:|---:|---:|
| bursty2 | **13.1×** | 4.9× | 0.7× |
| bursty8 | 8.2× | 6.3× | 3.6× |
| steady2 | **11.2×** | 6.3× | 3.3× |
| steady8 | 4.6× | 4.7× | 3.0× |
| steady10 | 7.9× | 3.0× | 2.7× |

The association is **3–13× beyond what window length alone produces**, in 29 of
30 buckets. This is a temporal overlap, not a cross-run correlation — but it is
still observational, and it does not by itself establish direction.

---

## 4. Bursty vs steady — the decisive contrast

| cond | migrations | migrated GB | activations | deactivations | **model-unavailable time** |
|---|---:|---:|---:|---:|---:|
| bursty2 | 18 | 202.5 | 38 | 31 | **450.1 s** |
| bursty6 | 16 | 156.0 | 35 | 29 | **548.3 s** |
| bursty10 | 16 | 176.9 | 34 | 27 | **427.5 s** |
| steady2 | 22 | 271.7 | 30 | 22 | **0.0 s** |
| steady6 | 24 | 226.9 | 32 | 24 | **0.0 s** |
| steady10 | 16 | 166.4 | 24 | 16 | **0.0 s** |

This **falsifies the intuitive explanation** ("bursty gives migration something
useful to do; steady is pure overhead"):

- steady migrates **as often or more** than bursty, and moves **more bytes**
  (166–279 GB vs 152–203 GB).
- Yet in steady **no model is ever unavailable** — every migration completes
  activate-on-new before deactivate-on-old, so the model is continuously
  servable. Verified per model per run: zero gaps in all steady runs; real gaps
  in bursty (e.g. bursty6 seed 1: model_4 137 s, model_5 111 s, model_3 11 s).

So migration volume is *not* what separates the two workloads. What separates
them is whether migration leaves a residency gap, plus the SLO-margin effect in §7.

Correlations across the 10 Prism conditions (`exp/analysis/migration_stats.json`):

| relation | bursty | steady | all |
|---|---:|---:|---:|
| corr(migrations, TTFT p99) | −0.32 | −0.57 | **−0.42** |
| corr(migrations, goodput) | −0.48 | −0.64 | −0.56 |
| corr(unavailable time, TTFT p99) | **+0.95** | n/a (0) | +0.52 |

**Migration count does not positively track the tail** — the sign is negative.
Residency *gaps* track it strongly, and only bursty has them.

---

## 5. Migration / residency / blocking mechanism

The mechanism behind the bursty gaps is in the runtime and is explicit:

`gpu_scheduler.py:380-393` — the GPU scheduler drains the per-model frontend
queue **only for models it currently holds in `activating` or `activated`**:

```python
models_can_recv = [m for m, state in self._model_states.items()
                   if state in ("activating", "activated")]
for model_name in models_can_recv:
    reqs = self.redis_client.pop_all(key=f"...:{model_name}")
    queue_time = time.time()
    for req in reqs:
        req.gpu_scheduler_queue_time = queue_time   # stamped only here
```

`gpu_scheduler.py:807-826` — on deactivation, both queued and already-dispatched
backend requests are pushed **back to the frontend queue**, then the model is
marked `deactivated`.

A request whose model is resident nowhere is therefore neither drained nor
stamped, which is exactly the observed signature: large `intake`, and requests
that reach the engine with no scheduler timestamps at all. Direct requeue log
events are rare (1 event / 42 requests across the sampled runs), so the bulk of
the effect is the *non-draining* path rather than repeated bouncing.

Algorithm 2 is **not** starving requests at the admission point:

| cond | rounds | max queue | requeued | late-dispatched |
|---|---:|---:|---:|---:|
| steady2 | 566 | 8 | 1 | 34 |
| steady8 | 1727 | 55 | 456 | 313 |
| steady10 | 1919 | 114 | 267 | 422 |
| bursty8 | 1484 | 111 | 169 | 359 |

Queues are shallow at low rate and the `admission` segment of TTFT is small in
steady (0.010–0.225 s). Deferral is Moore-Hodgson doing its job, not a stall.
(`deferred_requests` is a per-round gauge; summing it counts request-rounds, not
distinct requests, so it is not quoted as a count here.)

---

## 6. Per-model starvation / fairness

Prism, steady rate 8, per model:

| model | n | attainment | TTFT p50 | TTFT p95 | TTFT p99 | >1 s | >5 s | >10 s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Llama-3.2-3B | 1766 | 0.573 | 0.071 | 0.221 | 0.594 | 9 | 5 | 0 |
| Qwen2.5-3B | 1038 | 0.588 | 0.087 | 3.159 | 10.752 | 77 | 35 | 13 |
| Llama-3.1-8B | 1770 | 0.448 | 0.100 | 0.451 | 7.524 | 56 | 23 | 13 |
| **Qwen2.5-7B** | 2117 | 0.484 | 0.102 | **10.578** | **27.253** | **300** | **232** | **116** |

Prototype, same condition: **every model has 0 requests over 1 s**, attainment
0.818–0.993.

The tail is **not** evenly distributed. Qwen2.5-7B carries 300 of the 442
over-1 s requests. Qwen2.5-7B is the largest thin-KV model in the mix. This is
concentrated degradation, not uniform slowdown.

---

## 7. SLO failure decomposition — why steady collapses

| cond | arm | PASS | TTFT-only | TPOT-only | both | TTFT share of failures |
|---|---|---:|---:|---:|---:|---:|
| bursty8 | Prototype | 4678 | 131 | 1802 | 82 | 11% |
| bursty8 | **Prism** | 4735 | 348 | 1324 | 282 | **32%** |
| steady8 | Prototype | 5847 | 12 | 832 | 2 | 2% |
| steady8 | **Prism** | 3440 | 248 | **2678** | 325 | **18%** |
| steady10 | Prototype | 5968 | 7 | 2342 | 3 | 0% |
| steady10 | **Prism** | 3846 | 198 | **3883** | 392 | **13%** |

**In steady, 82–87% of Prism's failures are TPOT-only — the request got its
first token in time and then decoded too slowly.** The TTFT tail, however
dramatic in the percentiles, is a minority of the lost goodput there.

Why a small TPOT difference costs so much — TPOT median against its own SLO
threshold (base × 3):

| cond | model | SLO TPOT | Prototype p50 (margin) | Prism p50 (margin) |
|---|---|---:|---:|---:|
| steady8 | Llama-3.1-8B | 0.0383 | 0.0324 (**16%**) | 0.0398 (**−4%**) |
| steady8 | Qwen2.5-7B | 0.0349 | 0.0319 (**9%**) | 0.0337 (**4%**) |
| steady10 | Llama-3.1-8B | 0.0383 | 0.0367 (**4%**) | 0.0379 (**1%**) |
| steady10 | Qwen2.5-7B | 0.0349 | 0.0342 (**2%**) | 0.0392 (**−12%**) |
| bursty10 | Llama-3.1-8B | 0.0383 | 0.0277 (28%) | 0.0279 (27%) |
| bursty10 | Qwen2.5-7B | 0.0349 | 0.0305 (13%) | 0.0360 (−3%) |

**In steady at load, the SLO threshold cuts through the middle of the TPOT
distribution for both arms.** Prototype survives with 2–16% of margin; Prism's
median lands at or past the threshold. A ~5–15% decode difference therefore
flips a very large fraction of requests, which is why goodput falls 37% while
**throughput is identical** (9.68 vs 9.68 req/s at rate 10).

In bursty the margins are 13–54%, so the same absolute difference flips far
fewer requests — which is why bursty is nearly a tie.

This is a **metric-sensitivity amplifier**, not an artifact: the SLO is what it
is, frozen before the run. But it means "steady is 37% worse" measures a small
latency difference sitting on a cliff edge, not a 37% capability gap.

---

## 8. τ causal audit

From `exp/final-handoff/calibration_manifest.json` only — no new runs.
**Caveat: calibration is 6-model, bursty rate 20, held-out seeds 0/42. It is a
different configuration from this 4-model evaluation, so it transfers weakly.**
Server logs for those runs are not on disk (evidence archive only), so
model-state blocking could **not** be recomputed for them.

| τ | migrations (2 seeds) | weight GB | goodput | attainment | TTFT p99 | TPOT p99 |
|---|---:|---:|---:|---:|---:|---:|
| 0.00035 | **17** | 131.1 | **0.0990** | 0.0075 | 274.6 | 2.073 |
| 0.07 | 16 | 70.4 | 0.0613 | 0.0043 | 198.9 | 2.016 |
| 0.10 | 12 | 79.0 | 0.0658 | 0.0051 | 275.9 | 2.189 |
| 0.13 | 10 | 67.2 | 0.0527 | 0.0039 | 225.5 | 2.230 |
| 0.171086 | **3** | 20.4 | 0.0645 | 0.0048 | 204.6 | 2.297 |

Chain verdicts:

| arrow | verdict | evidence |
|---|---|---|
| τ ↓ → migration frequency ↑ | **PASS** | corr(τ, migrations) = **−0.92** over 5 τ values; 17 migrations at τ=0.00035 vs 3 at τ=0.171086 |
| migration ↑ → residency churn ↑ | **PASS** | churn is activate/deactivate count, which moves with migrations by construction |
| churn ↑ → model-state blocking ↑ | **INCONCLUSIVE** | not computable for calibration runs (no server logs). In the 4-model data it holds for bursty (corr(unavailable, TTFT p99) = +0.95) and **fails for steady**, where churn is high and blocking is exactly 0 |
| blocking ↑ → TTFT tail ↑ | **PASS in bursty, N/A in steady** | §3–§4 |
| **net: τ ↓ → goodput ↓** | **FAIL — sign is reversed** | corr(migrations, goodput) = **+0.48**, corr(τ, goodput) = **−0.78**. In calibration, *lower* τ and *more* migration gave *higher* goodput |

Two further cautions that weaken any τ claim in either direction:

- **Seed variance dwarfs the τ effect.** At the same τ=0.00035, seed 0 scored
  0.1760 and seed 42 scored 0.0220 — an 8× spread across two seeds, larger than
  the entire spread across all six τ values.
- The concern that 0.00035 is a boundary winner rather than an optimum is
  **not resolvable** from these artifacts: the grid is 0.00035 → 0.07 (200×),
  and nothing was ever measured in between.

**Conclusion: τ is confirmed to control migration frequency, and is not
confirmed to be in the causal chain to the goodput deficit. The one directional
test available points the opposite way.**

---

## 9. Implementation vs policy

Evidence for **policy** (expected Prism behaviour, not a defect):

- Algorithm 2 defers and late-dispatches requests. That is Moore-Hodgson: it
  deliberately sheds jobs that cannot meet their deadline to maximise on-time
  completions. Queues are shallow and `admission` time is small.
- Migration itself costs GPU time and KV transfer; some TPOT cost is inherent.
- Algorithm 1 records a full decision trace per cycle
  (`[PAPER-ALG1-V4] {"cycle":…, "line8":[…], "tau":…}`) with per-model
  current/best GPU and the τ comparison, i.e. it is deciding as specified.

Evidence for **implementation** (candidate defects, stated as candidates):

1. **Non-draining frontend queue.** `_recv_from_frontend_queue` drains only
   models in `activating`/`activated`. A model resident nowhere accrues requests
   that no scheduler is polling. Nothing bounds how long that lasts, and the
   bursty data shows 421–548 s of such windows per condition.
2. **Requests reaching the engine with no scheduler timestamps.** 1.7–4.0% of
   Prism requests, **zero** in Prototype, and they carry up to 100% of the
   >10 s tail in steady. `scheduler.py:2194` reads these fields with
   `getattr(req, …, None)`, which implies a request form that lacks them. The
   handoff notes a related known case ("a staged-but-never-admitted request is
   returned to the frontend in the backend form it arrived in"). **Whether these
   requests take a different code path or merely lose their instrumentation is
   not determined by the data available** — both are consistent with what we can
   see, and they have very different implications.
3. **No aging or starvation prevention** in the Moore-Hodgson queue
   implementation (`request_queue_mh.py`, `moore_hodgson.py` — no aging, priority
   floor, or oldest-first override found). Combined with (1), a model that keeps
   losing residency has no mechanism guaranteeing its requests eventually win.
   The concentration on Qwen2.5-7B (§6) is consistent with this, but not proof.

**This distinction is not resolved.** Points 1 and 3 are visible in the source
and their effects are visible in the traces; point 2's mechanism is not.

---

## 10. Proven / disproven / still unknown

**Supported by the data**

- Prism's added TTFT is entirely pre-engine queueing; prefill is identical (§2).
- Tail requests straddle model-state transitions 3–13× more than window length
  alone explains; non-tail requests straddle 0% in all 20 conditions (§3).
- bursty has 421–548 s of model-unavailable time per condition; steady has
  exactly 0 (§4).
- In steady, 82–87% of Prism's lost goodput is TPOT failures, not TTFT (§7).
- In steady at load, both arms' TPOT medians sit within 1–16% of the SLO
  threshold, so a small decode difference flips a large request fraction (§7).
- The tail is concentrated on Qwen2.5-7B, not spread evenly (§6).
- τ controls migration frequency (corr −0.92) (§8).

**Disproven / not supported**

- ~~"steady is worse because migration is pure overhead there"~~ — steady
  migrates as much or more, moves more bytes, and has **zero** residency gaps (§4).
- ~~"more migrations → worse tail"~~ — corr(migrations, TTFT p99) is **negative**
  (−0.42) across conditions (§4).
- ~~"τ↓ → … → goodput↓"~~ — the only directional evidence available shows the
  **opposite** sign (§8).
- ~~"the deficit is a TTFT-tail problem"~~ — true for bursty, false for steady,
  which is where nearly all the loss is (§7).

**Still unknown**

- The mechanism of the no-scheduler-timestamp requests (different path vs lost
  instrumentation) — and therefore whether the steady extreme tail is a defect.
- Why Prism's TPOT median is 5–15% higher in steady. Migration cost, KV
  pressure, and worker-pool contention are all candidates; nothing here
  separates them.
- Whether τ=0.00035 is an optimum or a grid-boundary winner — nothing was
  measured between 0.00035 and 0.07.
- Whether Qwen2.5-7B's concentration is starvation or simply the largest model
  suffering most under contention.

---

## 11. Recommended next experiment

**Not a τ sweep.** τ failed the one directional test available (§8), and seed
variance at fixed τ exceeded the entire across-τ spread. Sweeping τ now would
likely measure seed noise and would risk selecting τ on the same data used to
judge it.

**Primary — migration ablation on the frozen 4-model configuration.**
Run the Prism arm with **τ = ∞** (migration disabled) over the identical 20
canonical traces, paired against the existing runs. τ=∞ is an established
configuration in this harness, already exercised in calibration; it is an
*ablation*, not tuning. It answers the question the observational data cannot:

- If τ=∞ Prism recovers most of the steady goodput → migration causes the TPOT
  degradation, and the trade-off is real Prism policy cost.
- If τ=∞ Prism still loses → the loss is in the Prism serving path itself
  (worker pool, GPU scheduler, admission), independent of migration.

Cost: 20 runs ≈ 3.5 h. It also yields the residency-gap control (τ=∞ ⇒ no
migrations ⇒ no gaps), which directly tests the bursty mechanism in §4–§5.

**Free, do first — SLO sensitivity re-analysis on existing artifacts.**
Recompute goodput/attainment for both arms across a range of TTFT/TPOT scale
factors using the per-request dumps already on disk. No runs. This quantifies
how much of the −36.9% is the cliff-edge effect of §7 versus a genuine gap, and
it should accompany any future claim about the size of the deficit.

**Then, conditional on the ablation** — a minimal reproduction for the
no-timestamp path: instrument (not fix) the request path to record which route a
request took, and run one steady rate-8 condition. Only worth doing if the
ablation does not already explain the loss.

I have stopped here and made no further changes.
