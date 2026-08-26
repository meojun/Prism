# Environment manifest

Captured 2026-08-26T14:10:49.737039+00:00 on the machine
that produced every authoritative result.

## Hardware

| | |
|---|---|
| GPU | 2 x NVIDIA A100-SXM4-80GB (79.2 GiB usable each, sm_80) |
| interconnect | **NVLink NV4** between GPU0 and GPU1 |
| CPU | AMD EPYC 7513 32-Core Processor |
| cores | 64 |
| RAM | 503 GB |
| disk free at freeze | 439G |
| disk needed | ~7 GB results + ~0.7 GB dataset + ~47 GB model weights |

### nvidia-smi topo -m

```
[4mGPU0	GPU1	CPU Affinity	NUMA Affinity	GPU NUMA ID[0m
GPU0	 X 	NV4	24-31	3		N/A
GPU1	NV4	 X 	40-47	5		N/A

Legend:

  X    = Self
  SYS  = Connection traversing PCIe as well as the SMP interconnect between NUMA nodes (e.g., QPI/UPI)
  NODE = Connection traversing PCIe as well as the interconnect between PCIe Host Bridges within a NUMA node
  PHB  = Connection traversing PCIe as well as a PCIe Host Bridge (typically the CPU)
  PXB  = Connection traversing multiple PCIe bridges (without traversing the PCIe Host Bridge)
  PIX  = Connection traversing at most a single PCIe bridge
  NV#  = Connection traversing a bonded set of # NVLinks
```

## Software

| | |
|---|---|
| OS | Ubuntu 24.04.4 LTS |
| kernel | 6.8.0-124-generic |
| NVIDIA driver | 580.173.02 |
| CUDA (torch runtime) | 12.1 |
| Python | Python 3.10.21 |
| PyTorch | 2.4.0+cu121 |
| transformers | 4.45.2 |
| vllm | 0.6.3.post1 |
| SGLang | local checkout `prism-research/`, see SOURCE_MANIFEST.md |
| NCCL | bundled with the PyTorch cu121 wheel |
| venv | `/workspace/prism-exp/prism-venv` |

Full dependency set: `repro/prism_final/environment/pip-freeze.txt`.

## Non-secret environment variables

| name | purpose |
|---|---|
| `WORKSPACE` | workspace root, default `/workspace` |
| `HF_HOME` | model cache root, `/workspace/.hf_home` |
| `SHAREGPT_JSON` | path to the ShareGPT dump used by the trace generator |
| `CUDA_VISIBLE_DEVICES` | GPU selection; the experiments assume both GPUs visible |
| `PRISM_CONTROL_REQUEST_TIMEOUT_S` | control-path bound, default 120 |
| `PRISM_OUT_DIR`, `PRISM_EXPECTED_SRC_FILE` | harness overrides used by the runners |

Observed in this shell at capture time: HF_HOME, WORKSPACE.

## Secret variables required - names only, never values

| name | why | where to obtain |
|---|---|---|
| `HUGGING_FACE_HUB_TOKEN` | the `meta-llama/*` repositories are gated | huggingface.co account settings, after accepting the model licences |
| `PRISM_NTFY_TOPIC` | optional phone notifications; absence is handled silently | any ntfy.sh topic the operator chooses |
| GitHub credentials | pushing the research branch | a personal access token with `repo` scope |

**No secret value is stored in this repository.** `notify.sh` reads
`PRISM_NTFY_TOPIC` from the environment or `$WORKSPACE/.env`, which is outside
the repository, and exits quietly when it is absent.
