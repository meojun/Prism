# Fresh-server self-check

The reproduction package was validated **in a clean shell** (`env -i`, no
inherited environment, no shell history, no tmux, from an unrelated working
directory) to catch dependencies that only work because this session happens to
have them set.

```
FRESH_SERVER_SELF_CHECK = PASS
```

## Method

```bash
env -i HOME=/root PATH=/usr/bin:/bin:/usr/local/bin bash -lc '
  cd /tmp
  bash /workspace/prism-exp/repro/prism_final/verify_environment.sh
  bash /workspace/prism-exp/repro/prism_final/verify_artifacts.sh
  bash /workspace/prism-exp/repro/prism_final/smoke_test.sh
  bash /workspace/prism-exp/repro/prism_final/resume.sh --status'
```

7 environment variables total, none of them `PRISM_*`. Invoked from `/tmp`, so
any reliance on the caller's working directory would surface.

## Results

| check | result |
|---|---|
| all five scripts parse in a clean shell | PASS |
| repository root resolved from an unrelated cwd | PASS |
| `verify_environment.sh` | `NEW_SERVER_REPRO_STATUS = READY` (warnings only, for unset optional secrets) |
| `verify_artifacts.sh` | `ARTIFACT_VERIFICATION = PASS` — 48 trace hashes, frozen config, 108/108 runs |
| `smoke_test.sh` | `SMOKE_TEST = PASS` |
| `resume.sh --status` | `PENDING_CONDITIONS = 0` |
| no dependence on shell history / tmux / Claude session | PASS |
| no absolute temporary-path dependence | PASS |
| smoke output isolated to `exp/results/smoke-test/<timestamp>/` | PASS |

## One real defect found by this check

The first clean-shell run **failed**, and the failure was genuine rather than an
artefact of the test:

```
FAIL  regenerated trace differs:  vs 492d79a4a2f34ccc
regen.log: Cannot access gated repo ... meta-llama/Llama-3.2-3B ... 401
```

`build_paired_workload.py` loads each model's tokenizer from the local Hugging
Face cache. With `HF_HOME` inherited it resolves locally; in a clean shell it
fell back to `~/.cache/huggingface`, missed, and tried the network — which 401s
because the `meta-llama` repositories are gated. The script had a hidden
dependency on an environment variable that happened to be set.

Two corrections were made to `smoke_test.sh`:

1. `export HF_HOME=${HF_HOME:-/workspace/.hf_home}` — a clean shell now behaves
   like a configured one, matching what `verify_environment.sh` already did.
2. The gated-repo case is now reported as **SKIP with an explanation**, not as a
   trace mismatch. A missing tokenizer cache and a genuinely different trace are
   very different problems and must not share an error message.

`HF_HOME` is documented as required in
`exp/manifests/prism_final/ENVIRONMENT_MANIFEST.md` and in `example.env`.

After the fix, the clean-shell run reports
`PASS  regenerated trace is byte-identical (492d79a4a2f34ccc)`.

## Limits of this check

A truly fresh container was not provisioned: doing so would have required
re-downloading ~47 GB of model weights and a 0.63 GB dataset, and this machine
holds the only copy of the 6.61 GB of raw results. The clean-shell simulation
covers environment leakage, path resolution and hidden variables. It does **not**
prove that `pip install -r environment/pip-freeze.txt` resolves on a different
CUDA driver — that lock contains cu121 wheels and is only claimed valid for the
recorded driver.
