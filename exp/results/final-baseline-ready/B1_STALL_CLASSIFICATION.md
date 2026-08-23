# D3 B1 (global backlog): classification

Classification only. **No patch. Runtime `fcaaeff`, untouched.** B2/B3 not run.

## 1. Verdict

**Not the orphan-seq / token-mismatch class.** Every marker of that class is
absent, and the two markers that define it are provably clean.

| evidence | B1 | the orphan class (seq 2060, 3113-3115) |
|---|---|---|
| stale dispatched seq | **0 on both GPUs** | 3 on the issuing GPU |
| shared admission token | **GPU0 3748, GPU1 4713** — each exactly `last_dispatch + 1` | pinned at the orphaned seq (2060) while later seqs sat staged |
| backend queue residue | **no keys in Redis at all** | the entry had been taken by another GPU |
| staged seq blocking | none | 2061 and 2062 held by two engines, unpromotable |
| last dispatch = last admit = last complete | **yes, seq 3747 / 4712** | dispatch ran ahead of admit |
| issuing GPU vs actor GPU | never differ — the queue is GPU-scoped | differed; that was the fault |

Both frontiers advanced to the end. Nothing was orphaned.

## 2. Nor is it a new class

It is the shape already recorded in `FRONTEND_REDISPATCH_TRACE.md` on the
`seed_42` run and left unattributed there:

| | seed_42 (earlier) | B1 |
|---|---|---|
| stale seq | 0 | 0 |
| engines at the end | idle, nothing waiting/running/staged | silent from 16:31:06 |
| final placement | one model | `{"model_4": 0}` |
| controller | `no measured load` | `no measured load` |
| unanswered requests | 1,204 | **1,986** |
| spread | model_3/4/6 | all six: 62 / 299 / 488 / 99 / 540 / 498 |
| client failures | 0 fd, 1 conn (teardown) | 0 fd, 1 conn (teardown) |

Same class, previously documented, still unattributed. So there is nothing new
to patch and nothing old to re-diagnose here.

## 3. What this does NOT show

**It does not show that restoring the global backlog caused the stall.** One
sample. The same workload under condition A (`run10`, GPU-local backlog)
completed 8,423 of 8,423 with none aborted, but this class is intermittent —
D3 run 9 and run 10 both passed while `seed_42` and B1 did not, across several
different configurations. Attributing an intermittent stall to a one-run
configuration change would repeat the mistake this investigation has already
made twice.

**A limitation the experiment design created:** the attribution required the
observers off, so B1 has no `PAPER-ALG2-OBS` records — no heartbeats, no staged
lengths, no fetch/token pairs. Those are exactly the records that separated b1
from b2 on the last occurrence. This run therefore cannot be taken further than
the classification above.

## 4. State

- B1 failed by its own watchdog (`rc=143`, `benchmark: no actual progress
  event`) and its artifacts are preserved in full.
- B2 had started 11 seconds before the driver was stopped; it never recorded a
  server or produced a measurement, and its directory was removed rather than
  left as a partial result. B3 never started.
- No STOP marker was cleared to skip past a failure.
- Performance attribution is suspended, as instructed, until correctness is
  re-established.
- Servers stopped, GPUs free, runtime unchanged.

## 5. What is now known, and what is not

Known: the GPU-scoped queue holds. Across D3 run 9, run 10 and B1 there is not a
single stale dispatched sequence, and no GPU has touched another's queue.

Not known: what causes this second, older stall class — the one where the
ledger is clean, the tokens are clean, the engines are idle, and a large number
of requests simply never receive a response. It has now been seen under the old
queue and the new one, with observers on and off, at two different backlog
scopes. **STOP.**
