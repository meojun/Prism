# Migration residency lifecycle fix (Stage 1)

## Defect

`MIGRATION_DIAGNOSIS.md` (branch `exp/final-regression-diagnosis`, commit
`24eee4a`) isolated a residency-accounting defect in the model service. This
report records the minimal fix and its correctness gate. Moore--Hodgson
`select()`, deadlines, `e_i = p_i/c_i`, Algorithm 1, the migration policy, tau,
the workload, and the SLO definitions are unchanged.

`ModelService.v4_resident` maps `model_key` to the copy of that model which is
live on a GPU right now; a migration reads its weights from there over NVLink
instead of cold-loading them from host memory. Migration is target-first: the
target engine loads and commits itself as the new owner while the source is
still serving, and only then does the source engine drain and send
`__release__`.

The release handler was

```python
held = self.v4_resident.pop(released_key, None)
```

which drops the record whatever GPU it names. By the time the source releases,
that record is the *target's*, so a completed migration erased its own
residency. The D2 log shows it directly: after model4 moved GPU0 to GPU1, the
GPU0 release logged `held=1` -- it popped the GPU1 entry it had just written.

The consequence is not a lost log line. The reverse migration of model4
(GPU1 to GPU0) then found no source, reported `src=None`, and cold-loaded
6.794 GB from host memory onto a GPU that already held about 74,040 MiB. GPU0
reached about 81,152 MiB, kvcached's `cuMemCreate` failed with CUDA
out-of-memory, the server was killed, and the benchmark stopped at 3,914
responses.

## Fix

`prism-research/python/sglang/multi_model/model_sevice.py`

Residency mutation now goes through three named operations, and release is an
owner-aware compare-and-delete instead of an unconditional pop:

- `_residency_commit(model_key, gpu_id, state_dict, engine_id)` records the
  copy that is now live and returns the one it replaced.
- `_residency_source(model_key, target_gpu_id)` returns the live copy usable as
  a P2P source for this target (`None` when there is none, when it is already
  on the target GPU, or when no peer link exists).
- `_residency_release(model_key, released_gpu, released_engine)` drops the
  record **only while it still refers to the copy being released**, and reports
  whether it dropped.

The record carries the owning engine id as well as the GPU. GPU identity alone
is not sufficient: the same D2 run re-activated model6 on GPU0 in worker slot
`0_1` while slot `0_3` still held it, so a same-GPU release could delete a
newer same-GPU activation by exactly the same mechanism.

`prism-research/python/sglang/srt/model_executor/model_runner.py`

`delete_gpu_model()` now names the releasing engine in the `__release__`
message (the fourth slot was `None`), so the model service can make that
comparison. The message shape and the model-keyed router are unchanged.

The release path still runs `gc.collect()` / `torch.cuda.ipc_collect()` /
`torch.cuda.empty_cache()` unconditionally, so the IPC-mapping reclamation that
an earlier milestone added is untouched.

## Invariants

```text
after target prepare, before source release : residency names the target
after the old source releases               : residency still names the target
reverse migration while GPU-resident        : source is the owning GPU, never None
deactivation by the current owner           : residency is dropped
```

The last one matters as much as the others: a record that outlives its GPU copy
would hand a later migration a state dict pointing at freed memory.

## Test

`exp/tests/test_migration_residency.py` drives the real `ModelService.run()`
message loop -- load, release, and the migration source lookup -- with the v4
loader and CUDA stubbed, so it needs no GPU. It replays the D2 sequence
(GPU0 to GPU1 to GPU0, target-first each time), the same-GPU re-activation, an
owner deactivation, source selection without a peer link, and a source-level
guard against reinstating the unconditional pop.

```text
20 passed, 0 failed   ALL MIGRATION RESIDENCY TESTS PASSED
```

Against the pre-fix source the same test reports 10 failures, including
`reverse migration finds src=1, not None` and
`late source release keeps the committed target residency` -- the exact D2
failure.

No regression in the existing suites:

```text
exp/tests/test_alg2_runtime_order.py   PASS
exp/tests/test_moore_hodgson.py        PASS (incl. 300 randomized instances)
exp/tests/test_kv_migration.py         25 passed, 0 failed
exp/tests/test_v4_loading.py           41 passed, 0 failed
```

## Deliberately not included

`MIGRATION_DIAGNOSIS.md` also proposed failing target preparation closed
against actual target memory headroom. That is a second, independent change: it
can refuse a migration Algorithm 1 decided on, so it alters migration behaviour
rather than repairing accounting. The residency defect is the root cause of the
D2 OOM, and Stage 1 is specified as the minimal fix. The headroom guard stays
on the table and is revisited only if the D2 rerun still shows memory pressure
that residency accounting does not explain.
