# Prism paper-faithful baseline -- handoff

    PIPELINE_STATUS   = STOPPED
    FINAL_RUNTIME_SHA = 6618671
    HANDOFF_SHA       = b8f3a72
    selected tau      = 0.00035

`FINAL_RUNTIME_SHA` is the runtime the benchmarks ran on. `HANDOFF_SHA`
is the commit that also carries these documents and manifests; its
runtime code is identical to `FINAL_RUNTIME_SHA` -- packaging changed no
runtime behaviour.

## This baseline is not complete

The chain ended in **STOPPED**. Do not cite these numbers as a
finished comparison. What exists, and where to pick it up, is in
[Resume](#resume) below and in
`exp/results/final-evaluation/PIPELINE_STATE.json`.

Stop reason: `Algorithm 2 interaction gate FAIL: algorithm2_ran in protofresh-bursty-r2-s1 (/root/Prism-final-regression-diagnosis/exp/results/final-evaluation/04b-prototype-fresh/raw/bursty/rate_2/seed_1)`

Failed stages: 04-prototype-fresh
  - `04-prototype-fresh`: a prototype run failed

## What this is

Prism -- KVPR global placement (Algorithm 1) and Moore-Hodgson local
arbitration (Algorithm 2) -- implemented faithfully on SGLang's
multi-model worker pool, compared against the released Prism prototype
on identical ShareGPT workloads. Six models across two GPUs, with weight
and KV migration between them.

## Hardware

- 2 x NVIDIA A100-SXM4-80GB, NVLink between them
- the placement config and memory pool sizes assume 80 GB per GPU
- driver on the reference machine: 570.195.03
- Blackwell (compute capability 10.0+) will not work: the stack is
  pinned to torch 2.4.0+cu121, which has no kernels for it

## Setup

```bash
git clone <this repo> prism-exp && cd prism-exp
git checkout b8f3a72
./bootstrap.sh              # pinned; see setup/pins.env + setup/requirements.lock.txt
cp .env.example /workspace/.env && chmod 600 /workspace/.env
$EDITOR /workspace/.env     # HF_TOKEN is required; the Llama models are gated
```

`bootstrap.sh` builds the virtualenv, downloads the six models and
prepares the ShareGPT data. It is idempotent and safe to re-run.

Environment comes from `exp/scripts/env.sh`, which every run sources.
One value there matters more than it looks:
`FLASHINFER_WORKSPACE_SIZE=1073741824`. At FlashInfer's own 384 MiB
default, model_6 asks for more during prefill and the server kills
itself mid-run. It is set in the repository, after `/workspace/.env` is
sourced, so a stale copy of that file cannot lower it.

Secrets are never in the repository. `.env.example` names them:
`HF_TOKEN` (required) and `PRISM_NTFY_TOPIC` (optional push
notifications).

## Models and workloads

| model | HF id | revision |
| --- | --- | --- |
| model_1 | `meta-llama/Llama-3.2-1B` | `4e20de362430` |
| model_2 | `Qwen/Qwen2.5-1.5B-Instruct` | `989aa7980e4c` |
| model_3 | `meta-llama/Llama-3.2-3B` | `13afe5124825` |
| model_4 | `Qwen/Qwen2.5-3B-Instruct` | `aa8e72537993` |
| model_5 | `meta-llama/Llama-3.1-8B` | `d04e592bb4f6` |
| model_6 | `Qwen/Qwen2.5-7B-Instruct` | `a09a35458c70` |

Workloads: bursty rates 2/4/8/14/20 and steady rates 4/8/20, seeds 1/2/3 -- 24 in all; calibration uses bursty rate 20 on held-out seeds 0 and 42.
The `.pkl` traces are in `exp/workloads/final-evaluation/` and are
version-controlled; their SHA256 are frozen in
`exp/results/final-evaluation/CANONICAL_WORKLOAD_SHA256.json` and
re-checked before either arm runs. If a hash does not match, the chain
stops rather than compare arms that may not have seen the same work.
They can be regenerated with `exp/scripts/build_paired_workload.py`,
but a regenerated file is only interchangeable if its hash matches.

`c_i` (per-model prefill speed) is a measured property of the hardware,
frozen in `exp/results/final-evaluation/01-ci-profile/`. On different
GPUs it must be re-measured; on the same GPUs, reuse it -- re-measuring
changes `c_i` and therefore changes what tau means.

## Preflight

```bash
source exp/scripts/env.sh
python exp/scripts/handoff_preflight.py
```

It checks GPU count and memory, CUDA through torch, the six model
revisions, all 24 workload hashes, the FlashInfer workspace, HF_TOKEN,
redis, the descriptor limit, stale `/dev/shm` segments from a crashed
server, a server already running, the port, and write access. It exits
non-zero on any failure and no benchmark should start until it passes.

## Running it

```bash
bash exp/scripts/final_overnight.sh
```

That starts the whole chain under tmux with a watchdog, survives an SSH
disconnect, and runs: preflight, c_i, 12 calibration runs, tau
selection, the fairness gate, 24 prototype runs, 24 final runs,
aggregation, and the handoff packaging. Progress is in
`exp/results/final-evaluation/pipeline.log`; each stage writes
`STATUS.json` and each run writes `VERIFICATION.json`.

Individual pieces, if you need them:

```bash
# one calibration point
bash exp/scripts/final_stage.sh <outdir> cal-<taulabel>-s<seed> \
     v4-paper-faithful-v6-bursty-r20-s<seed> -- env KVPR_TAU=<tau> ... \
     bash exp/scripts/run_v4_case.sh paper-faithful-v6 bursty 20 <seed> \
       exp/workloads/final-evaluation/bursty_r20_s<seed>.pkl <outdir>

# tau selection over the held-out seeds
python exp/scripts/final_select_tau.py --calibration <raw> --out <FROZEN_TAU.json> \
       --summary <csv> --git-sha <sha> --ci-file <c_i.json> --expect-runs 12

# aggregation
python exp/scripts/final_aggregate.py --out-dir exp/results/final-evaluation
```

## Resume

- next stage: **04-prototype-fresh**
- next run: **bursty r2 s2**
- calibration: 12/12 valid
- prototype arm: 1/24 valid
- final arm: 0/24 valid

```bash
git clone <this repo> prism-exp && cd prism-exp
git checkout b8f3a72
./bootstrap.sh
cp .env.example /workspace/.env && $EDITOR /workspace/.env
source exp/scripts/env.sh
python exp/scripts/handoff_preflight.py
cat exp/results/final-evaluation/STOP        # read why it stopped, first
rm exp/results/final-evaluation/STOP         # only once you have
bash exp/scripts/final_overnight.sh
```

A stage that passed under this runtime freeze is skipped. A run with
rc=0 and a passing `VERIFICATION.json` is not repeated. A stage that
passed under a *different* freeze is re-run, because its numbers came
from different code -- `c_i` is the one exception, being a property of
the hardware.

Invalidated: runs recorded under an earlier runtime freeze, and every directory whose name carries a suffix after the seed (seed_0.staged-return-defect1, seed_0.watchdog-abort1, raw.pre-freeze-*). Why: the runtime changed, so their numbers are not comparable with runs on this freeze; they are kept as evidence and the tau selector refuses to read them

## What makes a run valid

Each run must satisfy all of these, recorded in its own
`VERIFICATION.json`; any failure stops the chain rather than being
retried or averaged away:

- rc == 0
- every offered request accounted for
- stale dispatched sequences == 0
- Algorithm 2 order violations == 0
- ownership / identity mismatches == 0
- client descriptor failures == 0
- unexpected connection failures == 0
- no staged payload failed to be returned to the frontend

## One correctness fix worth knowing about

**The backend queue is scoped per GPU.** `alg2_seq`, the Algorithm 2
admission token, is per-GPU, but the backend queue was keyed by model
alone (`backend:<model>`) and therefore shared by every GPU hosting that
model. Two GPUs drew from one queue against two independent token
sequences, so a request admitted under one GPU's token could be fetched
by the other -- orphaned sequences, stalled frontiers, requests served
under the wrong model. The key is now `backend:<gpu_id>:<model>`.
Do not merge these queues back together: the admission token has no
meaning across GPUs, and a shared queue silently breaks the ordering
Algorithm 2 exists to enforce.

Related, and for the same reason: a staged-but-never-admitted request is
returned to the frontend in the backend form it arrived in, not through
the admitted-`Req` converter. It was never converted into a `Req`, and
running it through that converter raised on its dict `sampling_params`,
losing the request while its sequence retired cleanly.

## Results and evidence

- `FINAL_RESULTS_INDEX.md` -- every table and where the raw runs live
- `exp/final_baseline_manifest.json` -- what the evaluation used
- `exp/HANDOFF_LOCAL_DEPENDENCIES.md` -- what this baseline needs that
  git does not carry
- `exp/results/final-evaluation/PIPELINE_STATE.json` -- exactly what ran

Raw per-request dumps and server logs are ~105 MB per run and are not in
git. They are archived:

| archive | sha256 | size |
| --- | --- | --- |
| `/workspace/prism-backups/prism-final-02-tau-calibration-20260823T213412Z.tar.zst` | `7b28a64a09ccf944...` | 0.37 GB |
| `/workspace/prism-backups/prism-final-04b-prototype-fresh-20260823T213412Z.tar.zst` | `dbf4b82729a57754...` | 0.00 GB |

## Known limitations

- `c_i` and the SLO base are measured on 2x A100-80GB. On other
  hardware both must be re-derived before the numbers mean anything.
- tau is selected on held-out seeds 0 and 42 only. Seeds 1-3 are the
  evaluation seeds and are never read during selection.
- The prototype arm is re-run fresh rather than reusing published
  numbers: the released prototype's own traces are gone, so its workload
  provenance cannot be established without guessing.
- `exp/tests/test_client_fd_gate.py` asserts against specific historical
  run directories; those assertions fail until the corresponding runs
  exist on this machine. Its unit checks are unaffected.

## Environment

- python: `3.10.20`
- torch: `2.4.0+cu121`
- cuda_runtime: `12.1`
- sglang: `0.3.4.post2`
- flashinfer: `0.1.6+cu121torch2.4`
- vllm: `0.6.3.post1`
- transformers: `4.45.2`

Rebuild from `setup/pins.env` and `setup/requirements.lock.txt`. Do not
re-resolve the dependency set; the pins exist because re-resolving
breaks this stack.

