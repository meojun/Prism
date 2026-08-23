# Algorithm 2 dispatch-side sequence leak on migration

Status: **NEW correctness class. Pipeline STOPPED. No runtime fix applied.**

Run: `cal-0p00035-s0`, attempt 2, runtime `6ec7357` (the A+B patch).
Artifacts: `02-tau-calibration/raw/tau_0p00035/seed_0` (attempt 2),
`seed_0.attempt1` (the pre-fix failure, preserved).

## 1. Symptom

The benchmark stopped serving at 4,558 / 8,423 completed with 8,227 arrivals.
Both GPUs sat at 0 % utilisation. The control plane stayed alive the whole
time -- the global controller kept planning, both GPU schedulers kept looping
at ~460 iterations per 5 s window -- but every window reported `admitted: 0`.

The controller's own view during the stall:

    Model model_4 has waiting/running requests: True
    Model model_6 has waiting/running requests: True
    Time for planning inactive model activation: 0.0000s

Work was pending, models were resident, and nothing was admitted.

## 2. The blocked frontier

Per-GPU reconstruction of every `[PAPER-ALG2-RUNTIME]` event:

| GPU | dispatched | admitted | issued-but-never-admitted |
|-----|-----------|----------|---------------------------|
| 0   | 1163      | 1158     | **1159, 1160, 1161, 1162, 1163** |
| 1   | 3476      | 3476     | 0 |

GPU 1 is perfect. GPU 0's admission frontier `_mh_next_backend_admit_seq` is
pinned at **1159** and never moves again. The five stranded sequences:

| seq | rid | model | dispatched | fate |
|-----|-----|-------|-----------|------|
| 1159 | model_4#140 | model_4 | 01:22:58 | never admitted, never retired |
| 1160 | model_4#141 | model_4 | 01:22:58 | never admitted, never retired |
| 1161 | model_2#82 | model_2 | 01:22:58 | **retired** by the new staged-never-admitted path |
| 1162 | model_6#1629 | model_6 | 01:25:37 | live, waiting behind the frontier |
| 1163 | model_6#1631 | model_6 | 01:26:09 | live, waiting behind the frontier |

`_mh_drain_retired()` advances only over a *contiguous* retired prefix, by
design and by explicit instruction: a live sequence after the frontier must
never be skipped. The frontier is at 1159; the retired set holds only 1161.
1159 and 1160 are neither admitted nor retired, so the prefix cannot advance,
1161 can never be consumed, and 1162/1163 -- two genuinely live model_6
requests on a model that is still resident on GPU 0 -- are blocked forever.

The contiguous-prefix rule is not the defect. It behaved exactly as specified.
It is the thing that turned a leak into a visible deadlock instead of silently
skipping live work.

## 3. Where 1159 and 1160 went

    01:22:58  GPU 0 dispatch  seq 1159  model_4#140
    01:22:58  GPU 0 dispatch  seq 1160  model_4#141
              ... model_4 migrates off GPU 0 ...
    01:23:46  GPU 1 dispatch  seq 2110  model_4#140  -> admit -> prefill -> complete
    01:23:46  GPU 1 dispatch  seq 2111  model_4#141  -> admit -> prefill -> complete

Both requests were served correctly on GPU 1. **No request was lost.** What
leaked is the source GPU's ledger entry.

`grep model_4#140 server.log` returns nothing: the *engine* on GPU 0 never saw
this request. The sequence was issued by the GPU scheduler at dispatch, the
request was still in flight to the backend when model_4 left GPU 0, and it was
then re-dispatched to GPU 1 by the frontend.

That leaves no component able to retire seq 1159 on GPU 0:

- the engine cannot report it -- `_alg2_report_migrated_away` reports what the
  engine holds (staged list, waiting queue, running batch), and this request
  was never in any of them;
- the backend-queue drain found nothing -- every `backend_queue_drained` event
  for model_4 records `drained: 0, returned_to_frontend: 0`;
- the migration handoff record confirms the miss directly:

```json
{"event": "migrated_away", "gpu_id": 0, "model": "model_4",
 "next_backend_admit_seq": 1159, "reason": "kv-stash",
 "reported": ["model_4#60", "...", "model_4#52"],   // 31 rids
 "retired": [], "dequeued_before_dispatch": [], "drained_admit_seqs": []}
```

31 requests reported, **zero retired**, and neither `model_4#140` nor
`model_4#141` appears in the reported list at all.

The GPU scheduler issued the sequence and is the only component that still
knows it exists -- and it does not retire it when the model departs.

## 4. Causal chain

> GPU 0's scheduler issued alg2_seq 1159/1160 to model_4#140/#141 at dispatch
> and pushed them toward model_4's backend; model_4 migrated off GPU 0 before
> its engine ever fetched them, so the engine's migrated-away report could not
> name requests it had never held and the backend drain found an empty queue;
> the frontend re-dispatched both onto GPU 1 (seqs 2110/2111, served normally),
> leaving 1159/1160 on GPU 0 permanently unadmitted and unretired; because
> `_mh_drain_retired()` advances only over a contiguous retired prefix and 1159
> is neither admitted nor retired, GPU 0's `_mh_next_backend_admit_seq` froze
> at 1159, so every later sequence on GPU 0 -- including live model_6 requests
> 1162/1163 on a resident model -- was refused admission for the rest of the
> run, and GPU 0 served nothing after 01:26.

## 5. Classification

**New class.** Not a recurrence of any previously fixed defect. Every earlier
fix concerned a request the *engine* held (stale staged entries, adoption
ordering, backend-queue drain on deactivation, decode retraction). This one
concerns a sequence the engine never received, and is therefore invisible to
all of those paths.

### The same class caused the attempt-1 failure

Re-reading `seed_0.attempt1` with the same reconstruction:

| GPU | dispatched | admitted | leaked | first leak |
|-----|-----------|----------|--------|-----------|
| 0   | 488       | 488      | 0      | -- |
| 1   | 31        | 29       | **2**  | **30** |

seq 30 = `model_6#23`, seq 31 = `model_6#24`, dispatched 18:32:39, zero hits in
the engine log. Identical mechanism, identical outcome.

### What the A+B patch did and did not fix

A+B was correct and necessary, and the evidence for it stands:

- seq 1161 (`model_2#82`) *was* retired in attempt 2 via the new
  `staged-never-admitted` report -- the B path works as intended;
- GPU 1 in attempt 2 admitted 3,476 / 3,476 with no identity mismatch and no
  ordering violation, against 29 / 31 in attempt 1;
- completions went from 511 to 4,558.

But A+B addressed the engine-side leak only. This dispatch-side leak sat
underneath it and now surfaces as the binding failure. The correct reading is
not "the fix was wrong" -- it is "there were two independent leaks into the
same frontier, and only one of them was fixed."

## 6. Proposed minimal fix -- NOT APPLIED

Make the component that issues the sequence responsible for retiring it.

When a model is deactivated or migrated away from a GPU,
`_handle_mh_migrated_away` in `gpu_scheduler.py` should retire every sequence
that GPU issued for that model which has not reached `backend_admit`. The GPU
scheduler already has this information -- it issued the sequences and records
each `dispatch` -- and the retirement machinery already exists
(`_mh_retired_admit_seqs` / `_mh_retired_start_seqs` plus the contiguous-prefix
`_mh_drain_retired()`).

Why this is safe rather than a bypass:

- it adds no heuristic, no constant, no timeout, and no ordering relaxation;
- it does not skip live work: the request leaves the source GPU entirely and is
  re-admitted on the target through the target's ordinary Algorithm 2 path with
  a fresh target-local sequence (`model_4#140` -> seq 2110 on GPU 1), so the
  source sequence provably has no remaining work behind it;
- it retires through the existing contiguous-prefix drain, so the invariant the
  user mandated -- never advance past a live sequence -- is preserved;
- it is symmetric with what the engine already does for requests it holds.

Scope: one handler in `gpu_scheduler.py`. It touches nothing in tau, the frozen
c_i, Moore-Hodgson `select()`, deadline or arrival semantics, migration policy,
activation policy, the KV allocator, the workload, the SLOs, or admission
ordering.

Tests to write before applying: a replay of this exact failure (dispatch on the
source, migrate before the engine fetches, re-dispatch on the target, assert the
source frontier advances and the target's ordering is untouched); an assertion
that a sequence whose request is still live on the source is *never* retired; an
idempotence check for repeated migrated-away reports; and a check that the
contiguous-prefix rule still refuses to skip a live sequence.

## 7. Second finding: the stall watchdog is blind to this failure

`monitor_baseline_stage.py` treats progress as `count > last_count` **OR** the
watched log still growing. During this deadlock the benchmark client kept
printing `Waiting for task req_...` lines to `bench.log` every few seconds, so
the log-activity clause reset the progress timer continuously and the 240 s
no-progress watchdog never fired. Only the 1800 s hard phase timeout ended the
run -- 26 minutes of GPU time after the deadlock was already provable.

The OR clause was added deliberately (D2 run 7 was killed while legitimately
draining after its benchmark finished), so it should be narrowed, not removed:
the log-activity signal should be accepted only from a log whose growth implies
served work, or granted a bounded grace window rather than an unlimited reset.

Harness only -- it cannot affect any measured result. **Also not applied**,
pending the same approval as the runtime fix.

## 8. State

- Pipeline STOPPED at stage 02. No stage advanced past this run.
- Both attempts preserved: `seed_0` (attempt 2) and `seed_0.attempt1`.
- Stage 01 (c_i) untouched and still valid; frozen c_i unchanged.
- No runtime file modified. Runtime remains at `6ec7357`.
