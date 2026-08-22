# Algorithm 2 x migration: the handoff accounting invariant

Written before the fix, because "give the resumed request a new sequence on the
target" is not a specification -- it says nothing about when the source lets go,
and a handoff that is not defined at both ends produces either a double count or
a gap.

## What Algorithm 2's runtime ledger is

Per GPU scheduler, `_mh_outstanding_prefills` maps `rid` to a record carrying
that GPU's monotonic `alg2_seq` and the request's predicted prefill cost. A
request enters it when that GPU **dispatches** the request and leaves it when
its prefill **completes** on that GPU. Three counters run alongside it:

```text
_mh_dispatch_seq             last sequence issued by this GPU
_mh_next_backend_admit_seq   the sequence this GPU expects to be admitted next
_mh_next_prefill_start_seq   the sequence this GPU expects to start prefill next
```

and one shared token in Redis, `_alg2_admission_seq_key`, which decides which
staged request an independent model engine on that GPU may move into its waiting
queue. Every one of these is **GPU-local runtime order**. None of them means
anything on another GPU.

The ledger is also the outstanding-work term: `sum(predicted_exec_s)` over its
entries is what the next feasibility calculation charges for work already in
flight. An entry that never leaves inflates that term forever; a request that
runs without an entry is invisible work.

## Which requests cross a GPU boundary, and in what state

Deactivating a model on the source does three things in order, and they produce
two different classes of departing request:

```text
retract_running_batch   running (decode) requests are captured as KV capsules
                        and appended to the waiting queue
_v6_stash_captured      the captured rids are removed from the waiting queue and
                        handed to the model service for the target to fetch
_evict_all_waiting_requests
                        everything still in the waiting queue is sent back to
                        the *frontend* queue, to be scheduled again from scratch
```

**Class B -- migrated with their KV.** Decode-phase requests. Their prefill
completed on the source long ago, so they hold **no** source ledger entry. They
arrive on the target, are rebuilt by `build_resumed_request`, and re-enter the
target's prefill path to extend from the migrated KV. Today they are appended
straight to the target's `waiting_queue` with `alg2_seq = None`, so the target's
completion gate finds no record and fails closed. This is the D3 failure.

**Class A -- evicted back to the frontend.** Requests the source had already
dispatched -- sitting in its Redis backend queue, staged in an engine, or
admitted and waiting -- but whose prefill had not completed. These **do** hold a
source ledger entry and a source sequence. They are returned to the frontend and
will later be dispatched afresh, with a new sequence, by whichever GPU picks them
up. Their source ledger entry is never retired today, and the source's expected
admit/start counters and the shared token still wait for a sequence that will
never arrive. D3 crashed on class B before this could bite, but it is the same
defect at the other end.

## The invariant

For every request R and every migration of R's model from GPU S to GPU T, at
every instant exactly one of the following holds:

```text
(1) R is outstanding on S, and on no other GPU
(2) R is outstanding on T, and on no other GPU
(3) R is outstanding nowhere, and is not in any GPU's waiting or prefill path
```

State (3) is legitimate only while R is in transit: after S has retired it and
before T has adopted it, R must be held somewhere that no scheduler will run --
the KV stash for class B, the frontend queue for class A. It must never be
runnable while unaccounted.

Three consequences, which the implementation has to satisfy:

**H1 -- the source retires before it lets go.** When R leaves S -- captured into
the stash, or evicted to the frontend -- S removes R's ledger entry *and*
repairs its counters, so that a sequence R will never use cannot stall the GPU:

```text
delete _mh_outstanding_prefills[R]
if R was never backend-admitted: _mh_next_backend_admit_seq skips R's seq
if R never started prefill:      _mh_next_prefill_start_seq skips R's seq
if the shared admission token still names R's seq: advance it past R
```

Retiring must be idempotent: a request can be reported gone by more than one
path, and a second retire is a no-op, never a counter skip.

**H2 -- the target adopts before the request is runnable.** A resumed request is
registered with T's Algorithm 2 and issued a **new, T-local** sequence *before*
it is placed anywhere a batch can pick it up. It never carries S's sequence: a
sequence is GPU-local runtime order and reusing it across GPUs is meaningless at
best. Concretely, `build_resumed_request` may not append to `waiting_queue`
directly; the request is held until T's scheduler has dispatched it.

**H3 -- one gate for everyone.** An adopted request then passes the *same*
backend-admission and prefill-start gates as any other request on T, in the
order T's Algorithm 2 chose. No bypass flag, no exemption branch, no "resumed"
special case in `_handle_mh_backend_admit`, `_handle_mh_prefill_start` or
`_handle_mh_prefill_complete`. If the ordering gate would reject it, that is a
real violation and must still fail closed.

## What the handoff must not change

**The request's identity in time.** Migration is a placement decision, not a new
arrival. `arrival_time`, the SLO and the deadline that Moore--Hodgson computes
from them are carried across unchanged. A resumed request that has already spent
2 s of its TTFT budget still has spent it; resetting the clock would quietly
hand migrated requests an advantage and corrupt every SLO number downstream.
What is *new* on the target is only the runtime ordering token, which is not
part of the request's semantics.

**Its position in Moore--Hodgson's own decision.** Adoption assigns a runtime
sequence; it does not re-open `select()`, change `e_i = p_i / c_i`, change the
deadline rule, or give the resumed request priority over anything. It enters T's
schedule the way T's schedule admits requests.

**The paths that do not migrate.** With migration off (the D1 configuration)
nothing in this changes: there are no capsules, no evictions to the frontend
from a deactivation, and no adoptions. With Algorithm 2 off (the D2
configuration) the ledger does not exist and the retire/adopt steps are no-ops.
Both must remain byte-for-byte in behaviour.

## How this is checked

The gate for the rerun, beyond D3's existing list:

```text
every resumed request appears in the target's ledger before it is batched
no resumed request carries a source sequence
source ledger holds no entry for a request that has left
sum over GPUs of ledger entries for any rid is <= 1 at all times
both GPUs end with an empty ledger
a mixed sequence A (target-local), R (resumed), B (target-local) is admitted
  and started in exactly the order the target's Algorithm 2 chose
```
