# STOP: a staged request could not be returned, and the run lost it

The overnight chain stopped on its first calibration point, `cal-0p00035-s0`,
and reported itself. This is what it stopped on.

## What happened

At 17:54:48 GPU 0 dispatched `model_3#192` as Algorithm 2 sequence 854. One
second later model_3 migrated away from GPU 0 while that request was **staged
but never admitted**, so the deactivation path tried to hand it back to the
frontend. The hand-back raised:

```
[PAPER-ALG2-HANDOFF] could not return staged model_3#192 to the frontend:
    'dict' object has no attribute 'max_new_tokens'
{"event": "staged_return_failed", "gpu_id": 0, "model": "model_3",
 "rids": ["model_3#192"]}
```

Sequence 854 was retired, so the ledger stayed consistent -- every
`[PAPER-ALG2-RUNTIME]` record for the run carries `order_ok: true` -- but the
request itself was delivered to nobody. It was never re-dispatched.

From then on the client sat on it. At 17:58:12 GPU 1 logged
`under-admission: 20 consecutive rounds with eligible=1 selected=0 late=0
queue_len=1`. The last response arrived at 18:01:51 (8232 response events,
8227 arrivals). For the next 244 s nothing moved: one request outstanding,
both GPUs idle. At 18:05:55 the run monitor's no-progress timeout fired,
killed the session, and the client's pending send failed with
`Server disconnected`.

Final tally: **8225 completed, 2 aborted of 8227 offered.**

## Root cause

`Scheduler._alg2_staged_generation_reqs` holds payloads **fetched from the
backend queue** (`scheduler.py:660`), where `sampling_params` is still the
plain dict it was serialized as. The staged-return path
(`scheduler.py:2345-2364`) passes those payloads to
`_convert_req_to_frontend_reqs(req: Req)`, which is written for an *admitted*
scheduler-side `Req` and reads `req.sampling_params.max_new_tokens`
(`scheduler.py:2158`). Attribute access on a dict raises, the send never
happens, and the request is dropped.

A staged-but-never-admitted request has not been converted into a `Req` at all,
so running it through the `Req` -> frontend converter is the wrong shape for
this path.

## What this is not

- **Not client descriptor exhaustion.** `fd_exhaustion: 0`; the limits were
  correct (soft 65535, hard 524288, raised before the client started). The
  classifier labelled the run `INVALID_CLIENT_CONNECTION_FAILURES` after the
  single `Server disconnected`, which was a consequence of the watchdog kill,
  not the cause of the stall.
- **Not a monitor false positive.** 244 s with a request outstanding and zero
  progress is a stall; the monitor was right to fail the run.
- **Not caused by tonight's harness changes.** The defect is in the frozen
  runtime at `444a216` and fires when a model migrates away while a request is
  staged.

## State

- Chain stopped at stage 02 with `STOP` in force; notifications delivered
  18:06:27Z.
- No server or benchmark process is running; both GPUs are at 0 MiB.
- The failed run is preserved intact at
  `02-tau-calibration/raw/tau_0p00035/seed_0` as evidence.

No fix has been applied, and none will be without instruction.
