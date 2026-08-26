# Prism reproduction package

Read in this order:

1. [`../../reports/prism/11_handoff/PRISM_SERVER_HANDOFF.md`](../../reports/prism/11_handoff/PRISM_SERVER_HANDOFF.md) — everything about the project and the server
2. [`bootstrap.sh`](bootstrap.sh) — prepare a fresh machine (launches nothing)
3. [`verify_environment.sh`](verify_environment.sh) — is this machine compatible?
4. [`verify_artifacts.sh`](verify_artifacts.sh) — do the frozen results still validate?
5. [`smoke_test.sh`](smoke_test.sh) — can the stack start? (offline by default)
6. [`resume.sh`](resume.sh) — resume the pipeline (asks before spending GPU time)
7. [`../../reports/prism/REPORT_INDEX.md`](../../reports/prism/REPORT_INDEX.md) — all findings

## Directory meanings

| directory | contains | committed? |
|---|---|---|
| `reports/` | human-readable research conclusions | yes |
| `exp/analysis/` | machine-readable analysis — the CSVs behind every table | yes |
| `exp/manifests/` | frozen protocols, baseline, trace/model/dataset/source manifests | yes |
| `exp/scripts/` | harness, runners, gates, generators | yes |
| `exp/workloads/` | generated trace files | **no** (regenerate; SHA256s are frozen) |
| `exp/results/` | raw run outputs | **no** (6.61 GB; inventoried, kept on the server) |
| `exp/state/` | pipeline state | yes |
| `repro/prism_final/` | this package | yes |
| `prism-research/` | the serving runtime checkout | **no** (gitignored; rebuilt from `patches/lifecycle_containment/`) |

## The one thing to know first

```
PERFORMANCE_EVALUATION_CLOSED = true
```

The baseline evaluation is finished and frozen. `resume.sh` will only fill
genuinely missing conditions and will refuse to overwrite a valid result or a
preserved failure. Do not re-run experiments to change numbers.

## Files never committed

Raw ShareGPT, model weights, raw experiment logs, trace `.pkl` files, and any
secret value. See `exp/manifests/prism_final/DATASET_MANIFEST.md` and
`MODEL_MANIFEST.md` to restore them.
