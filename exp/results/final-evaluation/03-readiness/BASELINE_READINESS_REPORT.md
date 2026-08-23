# Baseline readiness

```text
VERDICT: READY
Known implementation blockers remaining: 0 / 10
```

| check | result |
|---|---|
| environment recorded | PASS |
| runtime unchanged since the freeze | PASS |
| experiment repository clean at preflight | PASS |
| c_i frozen for all six models | PASS |
| tau frozen | PASS |
| tau calibrated on held-out seeds only | PASS |
| tau was selected against the frozen c_i | PASS |
| D3 interaction gate PASS | PASS |
| no failing D3 check | PASS |
| workload hashes recorded | PASS |

## Frozen inputs

```text
runtime commit      6618671
source patch sha    None
tau                 0.00035  (0p00035)
c_i file            /root/Prism-final-regression-diagnosis/exp/results/final-evaluation/01-ci-profile/prefill_speed_final_a100.json
```

## c_i (tokens/s, this A100 pair, frozen code)

```json
{
  "model_1": 44810.32909113844,
  "model_2": 24346.47999693055,
  "model_3": 10527.08978973345,
  "model_4": 16205.076392951552,
  "model_5": 9843.616539166123,
  "model_6": 11011.21695116448
}
```

Recorded 2026-08-23T21:22:37.432943+00:00.
