# Final reproducibility verification

Run **from the committed state** after the checkpoint pushes, in two
configurations: this provisioned server, and a fresh checkout of the pushed
branch that has never been provisioned.

```
FINAL_REPRO_VERIFICATION = PASS
```

## A. Provisioned server

| script | result |
|---|---|
| `verify_environment.sh` | `NEW_SERVER_REPRO_STATUS = READY` |
| `verify_artifacts.sh` | `ARTIFACT_VERIFICATION = PASS` |
| `smoke_test.sh` | `SMOKE_TEST = PASS` |
| `resume.sh --status` | `PENDING_CONDITIONS = 0` |

`verify_artifacts.sh` re-hashed 48 trace files against the frozen manifests,
re-checked the frozen configuration hash, and re-ran the full audit: tau 48/48,
many-model 20/20, final 4-HET 40/40, trace pairing 30/30 byte-identical,
0 problems. No authoritative result was touched; the smoke test wrote only to
`exp/results/smoke-test/<timestamp>/`.

## B. Fresh checkout of the pushed branch

A checkout of `research/prism-final-baseline` with no `prism-research/`, no
traces and no raw results — the genuine state of a new server after `git clone`.

This surfaced **three real defects**, all now fixed:

### 1. Unprovisioned was reported as corrupted

`verify_artifacts.sh` returned a bare `FAIL` when traces were merely absent. A
missing trace and a trace whose hash disagrees are very different problems and
must not share an exit status. It now distinguishes them:

```
ABSENT tau: 0/8 traces present -- not provisioned yet, regenerate
ARTIFACT_VERIFICATION = NOT_PROVISIONED
```

`smoke_test.sh` likewise now SKIPs when the reference trace is absent instead of
reporting a trace mismatch against an empty hash.

### 2. Hard-coded repository root

`exp/analysis/final_summary/audit.py` contained
`R = Path("/workspace/prism-exp")`, so it audited **this** server no matter where
it was invoked from — a clone at any other path would silently audit the wrong
tree, or fail. The root is now derived from `__file__`, and the Python
interpreter from `PRISM_PYTHON`/`sys.executable` rather than an absolute venv
path.

### 3. A required manifest lived in an uncommitted directory

The τ-calibration trace manifest was read from
`exp/results/4het-window-calibration/`, inside the uncommitted results tree, so a
fresh clone crashed with `FileNotFoundError`. It is now committed as
`exp/manifests/prism_final/TAU_CALIBRATION_TRACE_MANIFEST.json`.

### After the fixes

| configuration | result |
|---|---|
| provisioned server | `ARTIFACT_VERIFICATION = PASS` |
| fresh checkout | `ARTIFACT_VERIFICATION = NOT_PROVISIONED`, naming the exact remedy |

The handoff's first-30-minutes sequence now includes cloning the separate
`prism-research` runtime repository and regenerating all 48 canonical traces,
each of which must match its frozen SHA256.

## What this does and does not prove

**Does.** The committed package runs correctly from an arbitrary path in a clean
shell; the frozen configuration, trace hashes and all 108 authoritative runs
re-validate; an unprovisioned clone is diagnosed rather than mislabelled.

**Does not.** A truly fresh container was not provisioned — that would require
re-downloading ~47 GB of weights and 0.63 GB of dataset, and this machine holds
the only copy of the 6.61 GB of raw results. `pip install -r
environment/pip-freeze.txt` is only claimed valid for the recorded driver, since
the lock pins cu121 wheels.
