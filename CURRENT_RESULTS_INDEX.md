# Current results index

Where every number and artifact is, as of this handoff. The comparison is not finished; this says exactly how far it got.

| | |
| --- | --- |
| PIPELINE_STATUS | **INCOMPLETE** |
| FINAL_RUNTIME_SHA | `6618671` |
| HANDOFF_SHA | `438f54b` |
| SELECTED_TAU | **0.00035** |
| calibration | 12/12 PASS |
| prototype arm | 1/24 PASS, 23 pending |
| final arm | 0/24 PASS, 24 pending |
| aggregation | PENDING |
| next run | `04-prototype-fresh` / `bursty_r2_s2` |

## Calibration -- how tau was chosen

Six candidate tau on held-out seeds 0 and 42; evaluation seeds 1-3 were never read. Selection: highest mean Joint-SLO goodput, ties within 3% relative broken by fewer migrated bytes, then fewer migrations, then higher tau.

| tau | mean Joint-SLO goodput | mean attainment | migrations | migrated bytes |
| --- | --- | --- | --- | --- |
| 0p00035 **<- selected** | 0.0990 | 0.0075 | 17 | 140,672,284,672 |
| 0p10 | 0.0658 | 0.0051 | 12 | 86,711,910,400 |
| 0p171086 | 0.0645 | 0.0048 | 3 | 23,024,848,896 |
| 0p07 | 0.0613 | 0.0043 | 16 | 75,507,222,528 |
| inf | 0.0590 | 0.0039 | 0 | 0 |
| 0p13 | 0.0527 | 0.0039 | 10 | 72,779,199,488 |

Tied within 3%: ['0p00035'] -- no tie-break was needed.

### Every calibration run

| tau | seed | verdict | completed/offered | goodput | attainment | migrations | TTFT p99 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0p00035 | 0 | PASS | 8225/8227 | 0.1760 | 0.0134 | 7 | 296.3 s |
| 0p00035 | 42 | PASS | 8488/8498 | 0.0220 | 0.0015 | 10 | 252.9 s |
| 0p07 | 0 | PASS | 8227/8227 | 0.0846 | 0.0058 | 7 | 203.0 s |
| 0p07 | 42 | PASS | 8497/8498 | 0.0379 | 0.0027 | 9 | 194.9 s |
| 0p10 | 0 | PASS | 8226/8227 | 0.0807 | 0.0056 | 8 | 188.0 s |
| 0p10 | 42 | PASS | 8498/8498 | 0.0510 | 0.0046 | 4 | 363.9 s |
| 0p13 | 0 | PASS | 8227/8227 | 0.0815 | 0.0061 | 7 | 208.9 s |
| 0p13 | 42 | PASS | 8498/8498 | 0.0240 | 0.0018 | 3 | 242.0 s |
| 0p171086 | 0 | PASS | 8227/8227 | 0.1004 | 0.0074 | 1 | 204.5 s |
| 0p171086 | 42 | PASS | 8498/8498 | 0.0287 | 0.0021 | 2 | 204.8 s |
| inf | 0 | PASS | 8227/8227 | 0.0788 | 0.0053 | 0 | 182.2 s |
| inf | 42 | PASS | 8498/8498 | 0.0393 | 0.0024 | 0 | 132.2 s |

Full detail, including every correctness gate per run: `exp/final-handoff/calibration_manifest.json`.

## Evaluation arms

Both arms run fresh on the same 24 canonical workloads. The prototype arm has one condition done.

| stage | PASS | PENDING | INVALID |
| --- | --- | --- | --- |
| 04-prototype-fresh | 1 | 23 | 0 |
| 05-final-c | 0 | 24 | 0 |

Completed prototype runs:

- `bursty_r2_s1` -- 858/858 completed, 0 aborted, throughput 1.99 req/s, verdict PASS

Per-condition status: `exp/final-handoff/resume_manifest.json`.

## Workloads

24 canonical traces, bursty rates 2/4/8/14/20 and steady rates 4/8/20, seeds 1/2/3. They are not distributed -- they carry raw ShareGPT prompt text, some of which contains real leaked credentials. They are rebuilt instead, and the rebuild is byte-identical:

```bash
bash exp/scripts/restore_workloads.sh    # rebuild, then verify all 24 SHA256
```

Digests and the exact recipe: `exp/final-handoff/workloads_manifest.json`. Source dataset: `anon8231489123/ShareGPT_Vicuna_unfiltered` (ungated), sha256 `35f0e213ce091ed9...`.

## Evidence archive

- `final-handoff/evidence/prism-final-evidence-20260823T223812Z.tar.zst`
- sha256 `c09a27f57b47fab132767e07c0ced8649919d915bcb531122387c54bcdcebd3c`
- 35.5 MB
- durable location: committed to this git repository and pushed to the remote; this instance has no host volume, so nothing under /workspace survives its release

Contains: calibration: 12 runs, all artifacts except per-request dumps; prototype: bursty r2 s1 (PASS) and the preserved attempts; c_i profile and its provenance; preflight, readiness and fairness stage records; per-run VERIFICATION.json and ALG2_INTERACTION.json; server, scheduler, controller and model-service logs; monitor heartbeats and kill audits; weight and KV migration traces.

**Not included:** *_output_requests.json (25 files, ~950 MB). they carry raw ShareGPT prompt text -- third-party content that includes real leaked credentials -- which is why the workload pickles are not distributed either none for resuming: every number derived from them is in exp/final-handoff/calibration_manifest.json and in each run's own metrics. They are not recoverable after this server is released.

## Artifact map

| what | where |
| --- | --- |
| baseline manifest | `exp/FINAL_BASELINE_MANIFEST.json` |
| calibration provenance | `exp/final-handoff/calibration_manifest.json` |
| resume state | `exp/final-handoff/resume_manifest.json` |
| workload digests + recipe | `exp/final-handoff/workloads_manifest.json` |
| machine-local dependencies | `exp/HANDOFF_LOCAL_DEPENDENCIES.md` |
| evidence archive + hash | `exp/final-handoff/evidence_manifest.json` |
| c_i profile | `exp/results/final-evaluation/01-ci-profile/` |
| tau selector output | `exp/results/final-evaluation/02-tau-calibration/FROZEN_TAU.json` |
| per-run verification | `<run>/VERIFICATION.json` |
| Algorithm 2 interaction | `<run>/ALG2_INTERACTION.json` |
| how to continue | `FINAL_BASELINE_HANDOFF.md` |

## Correctness findings this run produced

- **staged-return type mismatch** (fixed in `6618671`): a request staged but never admitted was lost when its model migrated away, because the return path ran a backend payload through the admitted-`Req` converter. Evidence: `exp/results/final-evaluation/STAGED_RETURN_TYPE_DEFECT.md` and the preserved run `02-tau-calibration/raw/tau_0p00035/seed_0.staged-return-defect1`.
- **GPU-scoped backend queue**: `alg2_seq` is per-GPU while the backend queue was per-model and shared. See `FINAL_BASELINE_HANDOFF.md`; do not merge those queues back together.

