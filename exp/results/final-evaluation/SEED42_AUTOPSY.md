# `cal-0p00035-s42` autopsy

Classification: **C — harness failure.** No ownership/lifecycle invariant is
violated, and no patch is proposed. Runtime unchanged at `47564b8`.

## 1. The Algorithm 2 ledger is clean

Reconstructed from every `[PAPER-ALG2-RUNTIME]` and `[PAPER-ALG2-HANDOFF]`
event:

| GPU | dispatched | admitted | retired | **stale** |
|-----|-----------|----------|---------|-----------|
| 0   | 3,544 | 3,542 | 2 | **0** |
| 1   | 3,162 | 3,162 | 0 | **0** |

Every sequence issued was either acknowledged or retired. No frontier is
blocked, on either GPU. The last sequence on each side ran to completion —
GPU 0 seq 3544 (`model_5#2539`) dispatch → admit → prefill_start →
prefill_complete at 06:25:01, GPU 1 seq 3162 (`model_3#1280`) likewise at
06:25:19.

Both final deactivations found their engines **already empty**:

```
06:25:24 GPU0 model_5  waiting queue 0, running batch 0, drained 0, stash 0 requests
06:25:29 GPU1 model_3  waiting queue 0, running batch 0, drained 0, stash 0 requests
```

The engines drained their batches down themselves — model_5's last decode at
06:25:20, model_3's at 06:25:27 with `#running-req: 2` — and were deactivated
*because* they had gone idle, not while holding work.

## 2. Why the GPUs went to 0 %

Because there was nothing left to serve.

```
06:24:41  GPU0 model_4 deactivated      06:25:08  GPU0 model_3 deactivated -> {model_5: 2}
06:24:57  GPU1 model_1 deactivated      06:25:13  GPU1 model_3 activated
06:25:04  GPU1 model_2 deactivated      06:25:24  GPU0 model_5 deactivated -> {}
06:25:04  GPU0 model_3 deactivate       06:25:29  GPU1 model_3 deactivated -> {model_6: 2}
```

GPU 0 ends with **no models**, GPU 1 with **model_6 only**, and the controller's
final cycle agrees: `current_placement = {"model_6": 1}`,
`placement_plan = {"model_6": 1}`, `migration_reason = "no measured load"`.

That is the correct response to an empty system. The models were idle, so they
were released.

## 3. What the client was still waiting for

98 distinct rids: **47 model_3, 50 model_5, 1 model_6**. And the reason none of
them could ever arrive is client-side:

```
Error in sending generate request model_6#231 model model_6, error:
  File ".../aiohttp/connector.py", line 1301, in _wrap_create_connection
  File ".../socket.py", line 232, in __init__
OSError: [Errno 24] Too many open files
```

**1,971 of 1,972 send failures are `Errno 24`.** The benchmark client ran out of
file descriptors and could not open connections at all — starting at
`model_6#231`, early in the run. Those requests never reached the server. For
the remaining 97 the connection existed but the response never came back; the
server had served and released them, which is why the engines drained to zero
while the client still waited.

`model_6` staying active with `has waiting/running requests: True` while its
engine did nothing is the same artifact: the controller counted the *arrival* of
`model_6#526`, whose HTTP send then failed, so it never finishes and the model
is never considered idle.

Causal chain:

> The benchmark client exhausted its file-descriptor limit and failed to deliver
> 1,971 requests and to collect the responses for 97 more; the server served
> everything it actually received, its engines drained to empty, and the
> controller released the idle models until GPU 0 held none and GPU 1 held only
> model_6; with no work left both GPUs sat at 0 % while the client waited for 98
> responses that could never arrive, and the no-progress watchdog stopped the
> run after 240 s.

## 4. This is not confined to this run

| run | `Too many open files` |
|-----|----------------------|
| seed_0 (**accepted as PASS**) | **3,210** |
| seed_0.attempt1 | 10,564 |
| seed_0.attempt2 | 5,578 |
| seed_0.attempt3 | 5,342 |
| seed_42 | 3,942 |
| historical `final-overlap-pipeline` runs | **0** |

Every run in this pipeline is affected; none of the historical runs on the
previous pipeline are. So this is an environment/launch regression in the
current harness, not a property of the workload or of τ.

The consequence has to be stated plainly: **`cal-0p00035-s0`, which I accepted
as PASS earlier today, is contaminated too.** Its 1,605 aborts and its
completion count were shaped by 3,210 undeliverable requests, so its
throughput and latency are not a measurement of τ = 0.00035. The comparison I
drew against the historical run — "38 % more completions, 53 % fewer aborts" —
compared a run with 3,210 client failures against one with none, and does not
support the conclusion I drew from it. τ calibration on these numbers would be
calibrating the client's fd limit.

## 5. Where the limit is lost (stated, not fixed)

`run_v4_case.sh:233` raises `ulimit -n 65535` inside the tmux command that
launches the **server**. The benchmark is started separately at
`run_v4_case.sh:269`, in the outer shell, which inherits whatever limit the
stage/tmux chain gave it. `benchmark.py` also calls `set_ulimit(65535)` itself,
which cannot raise a soft limit above the hard limit it inherits. Errors
beginning at `model_6#231` indicate a limit far below 65535 in the process that
actually opens the sockets.

No fix is proposed here, per instruction. Establishing which of those two
mechanisms is responsible needs the limit read from the live benchmark process
during a run.

## 6. Verdict

- **A — ownership/lifecycle bug: no.** Stale sequences 0 on both GPUs, every
  sequence accounted, both engines empty at deactivation, all previously
  established invariants hold.
- **B — aggressive-τ liveness: no.** τ = 0.00035 did drive heavy churn (8
  migrations, 55 τ-suppressions, 45 memory rejections, 49 cooldown deferrals
  over 139 cycles), but the deactivations followed genuine idleness, and
  releasing idle models is correct behaviour.
- **C — harness failure: yes.** Client-side file-descriptor exhaustion.

STOP. No runtime change, no patch proposed.
