# `cal-0p07-s0`: a staged request is retired from the ledger and then dropped

Classification: **A — ownership/lifecycle bug.** In code I wrote. **STOP. No fix
applied.** Runtime unchanged at `47564b8`.

## 1. The client fd repair worked

| run | fd exhaustion | connection failures | verdict |
|-----|--------------|--------------------|---------|
| tau_0p00035 / seed_0 | **0** | **0** | valid |
| tau_0p00035 / seed_42 | **0** | **0** | valid |
| tau_0p07 / seed_0 | **0** | 1 | invalid |

Against 4,815 / 15,846 / 8,367 / 8,013 / 5,913 descriptor failures in the five
runs before it. The re-run of seed_0 also went from 6,622 completed with 1,605
aborted to **8,227 completed, 0 aborted**, throughput 12.2 → 15.5 — so those
aborts were the client's descriptor limit, not tau.

## 2. What actually stopped `cal-0p07-s0`

Not the client. The single `Server disconnected` was logged at bench.log line
9605, *after* the waiting began at line 8232: it is the watchdog's teardown
closing a connection that had been waiting, not the cause of the wait.

The run served **8,226 of 8,227** requests. It stalled on exactly one:

```
07:39:08.783  GPU0 dispatch seq 1014  model_2#58
07:39:08.849  GPU0 [PAPER-ALG2-HANDOFF] migrated_away_reported
                    reason="staged-never-admitted"  rids=["model_2#58"]
07:39:08.850  GPU0 migrated_away  drained_admit_seqs=[1014] drained_start_seqs=[1014]
```

Sequence 1014 was retired and both frontiers drained over it — correctly. The
Algorithm 2 ledger is clean on both GPUs:

| GPU | dispatched | admitted | retired | **stale** |
|-----|-----------|----------|---------|-----------|
| 0 | 3,802 | 3,800 | 2 | **0** |
| 1 | 4,615 | 4,615 | 0 | **0** |

And `model_2#58` never appears again. It was never re-dispatched, never
completed, never returned. The client waited for it until the watchdog stopped
the run.

## 3. The missing transition

`_evict_all_waiting_requests` releases three holdings, and only two of them give
the request back:

| holding | returned to the frontend? | then reported as |
|---------|--------------------------|------------------|
| waiting queue | **yes** — `_convert_req_to_frontend_reqs` → frontend | `evicted-to-frontend` |
| backend queue | **yes** — `_drain_backend_queue` returns each | `backend-queue-drain` |
| **staged list** | **no** | `staged-never-admitted` |

```python
staged_rids = [getattr(req, "rid", None)
               for req in self._alg2_staged_generation_reqs
               if getattr(req, "rid", None)]
self._alg2_staged_generation_reqs = []                      # the requests are dropped here
if staged_rids:
    self._alg2_report_migrated_away(staged_rids, "staged-never-admitted")
```

`_alg2_report_migrated_away` sends a `MigratedAwayReq` to the GPU scheduler and
nothing else. It retires the sequence; it does not deliver the request
anywhere. So the staged request objects are discarded while the ledger is left
perfectly consistent — which is exactly why every ordering, frontier and
stale-sequence check passes while a request is silently lost.

Causal chain:

> `model_2#58` was dispatched as sequence 1014 and fetched into model_2's staged
> list; model_2 was then deactivated on GPU 0, and the staged list was emptied
> and reported as `staged-never-admitted`, which retired sequence 1014 and
> advanced both frontiers but delivered the request to no one; nothing ever
> re-dispatched it, so the client waited for a response that could not come, the
> run sat at 8,226 of 8,227 with both GPUs idle, and the no-progress watchdog
> stopped it.

## 4. This is a defect in the A+B patch

The B fix (commit `6ec7357`) moved the staged-list release ahead of the early
return, which was correct and necessary: before it, staged requests survived a
deactivation in a slot that was then reassigned, and were admitted under the
wrong model. But it released them **only into the ledger**. The two sibling
paths beside it both return the request first and report second; this one
reports without returning.

It was invisible until now because the client was losing thousands of requests
to descriptor exhaustion in every run — one more unanswered request changed
nothing observable. With the client repaired, a single dropped request is enough
to stall a run, which is how it surfaced.

Frequency: 1 request in `tau_0p07/seed_0` and 1 in `tau_0p00035/seed_42.invalid1`;
0 in the two runs that passed. Rare, and fatal to the run when it happens.

## 5. Proposed minimal fix — NOT APPLIED

Give the staged requests back before reporting them, exactly as the waiting
queue does one branch below:

```python
for req in self._alg2_staged_generation_reqs:
    self.redis_client.send_pyobj(
        key=f"{...frontend_generate_request_key_prefix}:{self.model_name}",
        obj=self._convert_req_to_frontend_reqs(req),
    )
```

then clear the list and report. The report stays as it is — retiring the
sequence is right, because the request will take a new one wherever it lands
next.

Scope: one loop in `_evict_all_waiting_requests`. No new heuristic, no timeout,
no retry, no ordering change; it makes the staged path do what its two siblings
already do. It touches nothing in tau, the frozen c_i, Moore–Hodgson, deadline
or arrival semantics, migration or activation policy, the KV allocator, the
workload, the SLOs, or admission ordering.

Tests to write first: a staged request present at deactivation is delivered to
the frontend exactly once and its sequence retired once; a run with staged,
waiting and backend-queued requests together returns each exactly once and never
twice; the D3-run-2 and identity regressions the B fix exists for still hold;
and an assertion that no rid is ever reported migrated-away without having been
delivered somewhere.

## 6. State

- Pipeline STOPPED at stage 02. Runtime `47564b8`. Artifacts preserved.
- `tau_0p00035/seed_0` and `seed_42` are **valid** and stand as calibration input.
- `tau_0p07/seed_0` is marked invalid; it is also genuinely incomplete (rc=143).
- The five descriptor-exhausted runs remain excluded.
