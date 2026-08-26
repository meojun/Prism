# Final result audit

Complete offline audit of every authoritative run. No experiment was re-executed.

```
AUDIT = PASS
tau           48/48 PASS
many_model    20/20 PASS
final_4het    40/40 PASS
trace pairing 30/30 byte-identical
problems      0
```

Machine-readable: `exp/analysis/final_summary/FINAL_RESULT_AUDIT.csv` (one row per
run, 26 columns) and `audit_pairing.json`.

## What was checked, per run

| check | source of truth |
|---|---|
| `rc = 0` | `VERIFICATION.json` |
| verdict PASS, no failed gates | `VERIFICATION.json` |
| aborted requests = 0 | run numbers |
| Algorithm-2 order violations = 0 | run numbers |
| staged-return failures = 0 | run numbers |
| lifecycle validity | `lifecycle_validity_gate.py` re-run over the run's own logs |
| no fatal CUDA / NCCL | regex over `server.log` |
| trace SHA256 = frozen manifest | file re-hashed at audit time |
| trace actually used by the run | trace filename present in the run's own `SERVER_COMMAND.txt` / `STAGE_CMD.sh` |
| τ, window, cooldown | parsed from the run's own command line |
| policy matches arm | `kvpr-global-v4` for Prism, `simple-global` for Prototype |
| seed, rate, arm, workload | directory identity cross-checked against the command line |

## Trace pairing

All 30 paired conditions (10 many-model + 20 final 4-HET) were verified
**byte-for-byte**: the file consumed by the Prototype arm and by the Prism arm is
the same file, re-hashed at audit time, and equal to the frozen manifest entry.

## One audit defect found and corrected

The first audit pass reported 30 failures — every Prototype run — with the single
problem `window=None; cooldown=None`. That was a **defect in the audit, not in the
results**: `--kvpr-rate-window` and `--kvpr-migration-cooldown` are Prism-only
flags, and the released prototype runs `--policy simple-global` and legitimately
carries neither. The check is now applied to the Prism arm only, and the
Prototype arm is instead asserted to carry *no* KVPR flags.

This is the same class of mistake as the earlier pilot-gate misclassification:
applying Prism-arm expectations to an arm that does not implement those
mechanisms. Both are recorded rather than quietly fixed, because an audit that
fails correct runs is as dangerous as one that passes broken ones.

## Runs deliberately excluded from the authoritative set

Nothing was deleted. Full classification in
`exp/manifests/prism_final/ARTIFACT_CLASSIFICATION.json`.

| class | count | examples |
|---|---:|---|
| `AUTHORITATIVE_FINAL` | 108 | the 48 + 20 + 40 runs audited above |
| `DIAGNOSTIC` | 2 | r16 seed 9 many-model pilot, both arms |
| `EXCLUDED_PROTOCOL_CHANGE` | 2 | many-model r12 s7 (valid), r12 s8 (incomplete) |
| `KNOWN_INVALID_PRESERVED` | 6 | lifecycle stall, two `nccl_port` races, one harness failure, one misnamed-session kill, one reproducibility rerun |
| `REGRESSION_ONLY` | 1 | lifecycle containment E2E regressions |
| `HISTORICAL` | 5 | 4-HET paired seeds 1/2, window calibration, estimator test, cooldown ablation, halted Stage-B |

Total retained on disk: **6.61 GB**.

## Retry history

Four retries were performed across ~110 runs, all under the harness-only rule,
all with the trace, seed, rate, τ, window, cooldown and runtime unchanged, and all
with the failed attempt preserved:

| condition | class | outcome |
|---|---|---|
| τ T0 bursty r10 s3 | `nccl_port` TOCTOU race, server never started | retry PASS |
| τ T4 steady r8 s3 | harness failure, no OOM/NCCL/Alg2 | retry PASS |
| many-model prism bursty r8 s7 | `nccl_port` TOCTOU race | retry PASS |
| many-model pilot prototype | label misclassification — **not a run failure** | recovered by re-evaluating the gate with the correct arm; the run was never re-executed |

The final 4-HET matrix required **no** retries: 40/40 clean on first attempt.
