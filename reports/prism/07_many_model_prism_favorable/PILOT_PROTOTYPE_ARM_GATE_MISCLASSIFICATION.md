# Prototype arm misclassified by the Algorithm-2 interaction gate

A harness labelling defect, not a runtime failure. No serving runtime, Prism
semantics, config, workload or trace was changed.

## What happened

The many-model diagnostic pilot's **Prototype** run halted the pipeline with:

```
Algorithm 2 interaction gate FAIL: algorithm2_ran in mm-pilot-prototype-bursty-r16-s9
```

The run itself was healthy. Of the gate's 14 checks, 13 passed, including every
one that matters:

| check | result |
|---|---|
| `pipeline_rc_zero` | PASS (`rc=0`) |
| `no_deadlock` | PASS (`watchdog_state: COMPLETE`) |
| `no_request_loss` | PASS — **offered 8764, completed 8764, aborted 0** |
| `no_fatal_cuda_or_nccl` | PASS |
| `no_alg2_order_violation` | PASS |

The single failing check carried its own explanation:

```
algorithm2_ran  pass=False  {'runtime_events': 0, 'alg2_log_lines': 0, 'arm': 'prism'}
```

**`arm: 'prism'` on a Prototype run.** The gate had misidentified the arm.

## Root cause

`final_stage.sh` (lines 101 and 252) derives the arm from the run label prefix:

```bash
case "$LABEL" in protofresh-*|proto-*|p4het-proto-*) arm=prototype ;; *) arm=prism ;; esac
```

`prism_final_stage_runner.sh` emitted `mm-pilot-prototype-bursty-r16-s9`. That
matches none of the three recognised prefixes, so it fell through to the default
`arm=prism`, and `check_alg2_interaction.py` then *required* Algorithm 2 to have
run. The released prototype uses `--policy simple-global` and does not implement
Algorithm 2 at all, so it could never satisfy that requirement.

The prefix list is that harness's convention. The new stage runner invented its
own label shape and put the arm name in a position the convention does not read
— the same class of mistake as passing an inner tmux session name that
`run_v4_case.sh` derives for itself.

`check_alg2_interaction.py` already documents this exact trap in a comment:
*"requiring it failed a prototype run that served 858 of 858 requests with no
aborts and no deadlock."*

## Fix

Minimal, harness-only. `prism_final_stage_runner.sh` now builds the label prefix
per arm so it classifies correctly under the existing convention:

| arm | label prefix | classifies as |
|---|---|---|
| prism | `${STAGE}-prism` | prism |
| prototype | `proto-${STAGE}` | prototype |

A **pre-flight assertion** was added: before any run, the runner applies the same
`case` statement to a probe label and stops if the derived arm does not match the
intended arm. Harness validation only — no runtime semantics involved.

Verified for every label this pipeline will emit:

```
mm-pilot   prism      mm-pilot-prism-...        -> prism      OK
mm-pilot   prototype  proto-mm-pilot-...        -> prototype  OK
mm-final   prism      mm-final-prism-...        -> prism      OK
mm-final   prototype  proto-mm-final-...        -> prototype  OK
final4het  prism      final4het-prism-...       -> prism      OK
final4het  prototype  proto-final4het-...       -> prototype  OK
```

## Re-evaluation, not re-execution

The completed Prototype pilot was **not re-run**. Its Algorithm-2 gate was
re-evaluated with `--arm prototype`, and its verification was produced from the
existing result file:

- original wrong-arm output preserved as `ALG2_INTERACTION.wrong-arm-prism.json`
  and `alg2_interaction.wrong-arm-prism.log`
- re-evaluated gate: **14/14 PASS**, `algorithm2_absent_as_expected` PASS
- `VERIFICATION.json`: **PASS**, rc=0, no failed gates, 8764/8764, 0 aborted

## A second defect this exposed: lifecycle gate false positive

Re-checking the run surfaced an unrelated false positive in
`exp/scripts/lifecycle_validity_gate.py`. Its unacknowledged-control-request
check assumed every run emits `[V5-HOP]` acknowledgements. That path exists only
under `--overlap-migration`; the released prototype issues control requests and
**never acknowledges them by design**, emitting zero acks.

The check is now applicability-guarded: a run that produced no acks at all is not
judged by it. Prism runs always produce acks, so the check keeps full force where
it matters.

Verified after the guard:

- **sensitivity** — the preserved lifecycle stall still FAILs on all three
  findings, including the unacknowledged deactivate (that run does emit acks)
- **specificity** — **113/113 PASS** across 48 τ runs, 44 historical Prism runs,
  20 historical Prototype runs and the Prototype pilot

Before the guard, 10 historical Prototype runs failed spuriously. That gate had
only ever been validated against Prism runs, which is why the gap survived.

## Impact — corrected figures

The defect affects the **Prototype arm only**; Prism labels always classified
correctly because `prism` is the fall-through default.

| | runs |
|---|---:|
| Prototype pilot, already completed (recovered by re-evaluation, not re-run) | 1 |
| Prototype in the final many-model matrix (5 rates × 2 seeds) | 10 |
| Prototype in the final 4-HET matrix (2 workloads × 5 rates × 2 seeds) | 20 |
| **Prototype runs still to execute** | **30** |

Without the fix none of those 30 could have passed. The 48 τ-calibration runs are
all Prism and are unaffected.

## Preserved evidence

- `exp/results/many-model-pilot/raw/prototype/bursty/rate_16/seed_9/ALG2_INTERACTION.wrong-arm-prism.json`
- `exp/results/many-model-pilot/STOP_EVIDENCE_prototype_arm_misclassification.txt`
