# GPU-scoped backend queue: design impact audit

Audit only. **Nothing implemented.** Runtime `16fa9b2`. Pipeline STOPPED.

Target invariant:

> A seq issued by GPU g may only exist in a backend queue owned by GPU g, until
> an explicit ownership transfer occurs.

Proposed key change: `{prefix}:{model}` → `{prefix}:{gpu_id}:{model}`.

## 1. One fact that makes this cheap

`Scheduler.gpu_id` is assigned once at construction (`scheduler.py:135`) and is
never reassigned. **Engines do not move between GPUs — models move between
engines.** So every site that touches the key already has an unambiguous
`self.gpu_id` in scope, and no site needs a new parameter, lookup or message
field. The same is true on the scheduler side, where `self.gpu_id` is the
scheduler's own identity.

## 2. Every producer, consumer and cleanup path

| # | path | site | current key | proposed key | actor GPU | issuing GPU | ownership invariant today | after |
|---|---|---|---|---|---|---|---|---|
| 1 | **GPU scheduler enqueue** | `gpu_scheduler.py:876` `_send_to_backend_queue` | `{p}:{model}` | `{p}:{self.gpu_id}:{model}` | g | g | producer records seq on g — correct | unchanged, now unreachable by others |
| 2 | **queue-length probe** | `gpu_scheduler.py:231` `get_queue_length` | `{p}:{model}` | `{p}:{self.gpu_id}:{model}` | g | any | **reads a global depth that includes other GPUs' entries** | reads only g's own backlog |
| 3 | **engine fetch** | `scheduler.py:648` `recv_pyobj_non_block` | `{p}:{self.model_name}` | `{p}:{self.gpu_id}:{self.model_name}` | g | g | correct today only because the engine happens to sit on g | structurally guaranteed |
| 4 | **backend drain** | `scheduler.py:2209` `_drain_backend_queue` | `{p}:{model}` | `{p}:{self.gpu_id}:{model}` | g | **any** | **VIOLATION**: drains entries issued by other GPUs, reports to `engine_to_gpu_scheduler:{self.gpu_id}` | drains only g's own; report is always to the issuing ledger |
| 5 | **deactivation `pop_all`** | `gpu_scheduler.py:795` `_handle_deactivate_result` | `{p}:{model}` | `{p}:{self.gpu_id}:{model}` | g | **any** | **VIOLATION**: pops other GPUs' entries and reports nothing at all — the path that orphaned seq 2060 | pops only g's own |
| 6 | **key definition** | `multi_model_server_args.py:337,347` | prefix only | unchanged | — | — | prefix carries `queue_id`, no GPU dimension | callers add the dimension |
| 7 | **activation / commit / rollback** | `scheduler.py:1793+`, `gpu_scheduler.py` `_handle_deactivate_result` | does not touch the key | — | g | — | no queue interaction | unchanged |
| 8 | **migration / adoption** | `_alg2_request_adoption`, `_handle_mh_adopt_resumed` | does not touch the key | — | target | target | target queues a placeholder and **issues its own new seq** via path 1 | unchanged |
| 9 | **worker reuse** | `worker_pool.assign_worker` / `release_worker` | does not touch the key | — | g | — | slots are per GPU already | unchanged |
| 10 | **frontend return** | `frontend_generate_request:{model}` | `{p}:{model}` | **unchanged** | any | — | frontend is deliberately model-scoped and GPU-agnostic | unchanged |
| 11 | **Redis key cleanup** | none exists | — | — | — | — | keys are never deleted; Redis drops empty lists | unchanged; the key space grows by a factor of `n_gpus`, which is 2 |

Paths 4 and 5 are the two violations. Paths 7–11 do not touch the key at all and
need no change.

## 3. Explicit ownership transfer: where it is needed

Exactly **one** place, and it already exists and is already correct.

When a model migrates, the target does **not** inherit the source's queue or its
sequence. `_handle_mh_adopt_resumed` queues a placeholder in the target's own
`RequestQueue`, and the target's Algorithm 2 selects it and issues a **fresh
target-local `alg2_seq`** through path 1. That is the ownership transfer, and it
is a re-issue rather than a hand-over. Evidence from the incidents: `model_4#140`
took seq 1159 on GPU 0 and, after migrating, seq 2110 on GPU 1.

So the transfer boundary is *the request*, never *the queue entry*. Under the
split, an entry sitting in `{p}:{g}:{m}` is never legitimately consumed by
another GPU, so **no new transfer mechanism is required**. What is required is
that a departure releases g's own queue — which is paths 4 and 5, already
present, and correct once scoped.

## 4. Effect on Prism paper semantics

| construct | affected? |
|---|---|
| Algorithm 1 (KVPR placement, τ line-8) | **no** — reads GPU memory and measured rates, never this queue |
| Algorithm 2 (Moore–Hodgson `select()`, `e_i = p_i/c_i`, deadlines) | **no** — operates on the GPU's own `RequestQueue`, which is already per-GPU |
| per-GPU admission ordering and the sequence frontier | **no** — already per-GPU; the split makes the queue agree with them |
| c_i, τ, workload, SLOs | **no** |
| migration / activation policy | **no** — path 8 unchanged |

**One behavioural delta, and it is not a paper construct.** Path 2 feeds
`models_to_skip` in `admission_control` / `admission_control_mh`
(`request_queue.py:186`, `request_queue_mh.py:106`), a backpressure guard with a
hard-coded `_skip_model_threshold = 10`. Today it measures the **global**
per-model backlog; after the split it measures **this GPU's**. A model is
normally active on one GPU, so the two coincide except during migration overlap,
when a model is briefly live on both. In that window today's global reading can
make GPU g skip a model because GPU h has a backlog — which is not a property
the paper asks for. The split makes each GPU throttle on its own backlog.

This is a real change and must be declared, not waved through: it is an
implementation guard, absent from the paper, and the split makes it local rather
than global. It should be recorded in the runtime manifest as such.

## 5. Change scope

**Five lines of key construction**, all in two files, none in Algorithm 1 or 2:

- `gpu_scheduler.py:231, 795, 876` — add `{self.gpu_id}:`
- `scheduler.py:648, 2209` — add `{self.gpu_id}:`

No new message type, no new parameter, no new state, no protocol change. The
prefix definition (path 6) stays as it is; the dimension is added by callers,
which is where the GPU identity lives.

Migration compatibility: none needed. Queues are created on first push and hold
only in-flight work; a run starts with them empty.

## 6. The alternative: keep the shared queue, add cross-GPU retire notification

| | GPU-scoped queue | cross-GPU retire notification |
|---|---|---|
| **mechanism** | the entry is unreachable by other GPUs | the entry stays reachable; whoever drains it must discover the issuing GPU and notify that ledger |
| **new state** | none | every queued request must carry its issuing GPU, and every drain must route a `MigratedAwayReq` to a ledger that is not its own |
| **failure surface** | the race is eliminated | the race remains and is *handled*: the notification can be lost, arrive after the frontier has moved, arrive for a sequence already retired, or race a re-dispatch of the same rid. Every one of those is a new class of the kind that has cost us four patches |
| **complexity** | 5 key strings | a new cross-GPU accounting path, plus idempotence and ordering rules for it |
| **paper fidelity** | identical, apart from the declared `models_to_skip` locality change | identical |
| **failure mode if it breaks** | a GPU cannot see another GPU's queue — structurally impossible to regress into | a dropped notification reproduces exactly the stall we have now, silently |

The notification design preserves a shared queue that nothing benefits from: no
component legitimately consumes another GPU's entry, and the migration transfer
happens by re-issuing the sequence, not by moving the queue entry. It keeps a
race in order to manage it, and the last four patches are the evidence for how
that goes.

**Recommendation: the GPU-scoped queue.** It removes the invariant violation by
construction rather than detecting it after the fact, and its entire cost is
five key strings plus one declared change to a non-paper backpressure guard.

## 7. STOP

Nothing implemented. Runtime `16fa9b2`, untouched. Calibration not re-run.
Valid inputs remain `tau_0p00035/seed_0`, `tau_0p00035/seed_42`,
`tau_0p07/seed_0`.
