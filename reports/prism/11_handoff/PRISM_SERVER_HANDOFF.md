# Prism server handoff

```
PRISM_HANDOFF_STATE = READY
```

Written for a researcher or agent with **no access to the originating
conversation**. Everything needed is either in this repository or named here with
instructions to obtain it.

---

## 1–3. Project, question, conclusion

**Purpose.** Reproduce *Prism: Cost-Efficient Multi-LLM Serving via GPU Memory
Ballooning* (OSDI 2026 camera-ready) faithfully enough that a comparison against
the released prototype measures the algorithm rather than an implementation
artefact — then find where the algorithm helps.

**Question.** Does Prism's memory-centric placement improve serving performance,
and under what conditions?

**Conclusion.** Prism is **not universally superior**. The same frozen
configuration gives:

| regime | Prototype | Prism | Δ | wins |
|---|---:|---:|---:|---:|
| Prism-favorable many-model (best case) | 2.2295 | 2.4048 | **+7.9 %** | 7/10 |
| Final 4-HET hold-out | 4.7686 | 3.5937 | **−24.6 %** | 0/20 |

Consistent with objective-function mismatch: KVPR optimises **memory pressure**,
not serving performance. In 4-HET, KVPR's optimum frequently co-locates the two
largest models — memory-optimal, compute-hostile. Two final seeds per condition;
no statistical significance is claimed.

## 4–7. Repository, branch, commit, tags

| | |
|---|---|
| remote | `https://github.com/meojun/Prism.git` |
| **safe branch** | `research/prism-final-baseline` |
| clean base | `3ef6adb1c7c138f6537d882c73d48a56df026b27` (tag `final-baseline-handoff`) |
| **RUN_CODE_COMMIT** | `413f9ee44aa7563afd7570f06ca74100b738dad8` |
| final tag | `prism-final-baseline-v1` |

**Never push `exp/4het-paired-evaluation`.** Its history reaches local commit
`51c5405`, which contains the ShareGPT dump. The remote has never had it.

## 8. Layout

```
reports/prism/        research conclusions        (00_overview … 11_handoff)
exp/analysis/         machine-readable CSVs
exp/manifests/        frozen protocols and manifests
exp/scripts/          harness, runners, gates, generators
exp/workloads/        generated traces        (not committed; SHA256s frozen)
exp/results/          raw run outputs         (not committed; 6.61 GB, inventoried)
exp/state/            pipeline state
repro/prism_final/    this package
prism-research/       serving runtime         (gitignored; rebuilt from patches/)
```

## 9–10. Runtime identity and patches

| | |
|---|---|
| runtime base commit | `595ec1f170e75a43897a7a2ad58ac5a9820aa2e8` |
| worktree patch sha256 | `49f47ebd7c75aecdac2d67efea1b3d121b9e599693df514cfd71bfe10724881d` |
| **RUNTIME_SOURCE_TREE_HASH** | `7fbd431c6a636df0c72bb6a40324f851d002d204` |

```bash
git -C prism-research reset --hard 595ec1f170e75a43897a7a2ad58ac5a9820aa2e8
git -C prism-research clean -fdq
git -C prism-research apply patches/lifecycle_containment/prism_research_worktree.patch
PRISM_REPO=$PWD/prism-research bash exp/scripts/snapshot_source_patch.sh /tmp/v verify
sha256sum /tmp/v/prism_research_worktree.patch     # must equal the patch sha256 above
```

Details, including which changes are paper mechanisms and which are
implementation-added correctness infrastructure:
`exp/manifests/prism_final/SOURCE_MANIFEST.md`.

## 11–20. Hardware and software

2 × NVIDIA A100-SXM4-80GB, **NVLink NV4** between GPU0 and GPU1, 64 cores,
503 GB RAM. Driver 580.173.02, CUDA 12.1 (torch runtime), Python 3.10.21,
PyTorch 2.4.0+cu121, NCCL bundled with the wheel, SGLang from the local
`prism-research` checkout. Full detail and `nvidia-smi topo -m`:
`exp/manifests/prism_final/ENVIRONMENT_MANIFEST.md`.

Disk: ~7 GB results + 0.63 GB dataset + ~47 GB model weights.

## 21–24. Models and dataset

Six models, pinned revisions, in `exp/manifests/prism_final/MODEL_MANIFEST.md`:
`Llama-3.2-1B`, `Qwen2.5-1.5B-Instruct`, `Llama-3.2-3B`, `Qwen2.5-3B-Instruct`,
`Llama-3.1-8B`, `Qwen2.5-7B-Instruct`. 4-HET uses `model_3`…`model_6`.

```bash
export HF_HOME=/workspace/.hf_home
export HUGGING_FACE_HUB_TOKEN=...        # meta-llama repos are gated
huggingface-cli download <hf_id> --revision <revision>
```

Dataset: `ShareGPT_V3_unfiltered_cleaned_split.json`, 0.627 GB,
sha256 `35f0e213ce091ed9…` (full value in `DATASET_MANIFEST.md`), from
`anon8231489123/ShareGPT_Vicuna_unfiltered`. **Never committed.**

## 25–26. Environment and secrets

Non-secret: `WORKSPACE`, `HF_HOME`, `SHAREGPT_JSON`, `CUDA_VISIBLE_DEVICES`,
`PRISM_CONTROL_REQUEST_TIMEOUT_S`, `PRISM_OUT_DIR`, `PRISM_EXPECTED_SRC_FILE`.

Secrets, **names only** — no value appears anywhere in this repository:

| name | why |
|---|---|
| `HUGGING_FACE_HUB_TOKEN` | gated meta-llama repositories |
| `PRISM_NTFY_TOPIC` | optional notifications; absence is silent |
| GitHub PAT | pushing the research branch |

Template: `repro/prism_final/example.env`. Put the filled copy **outside** the
repository (e.g. `$WORKSPACE/.env`).

## 27–33. Frozen configuration

```
FINAL_TAU    = 0.012859417696566448      (pooled positive-delta_r P50)
KVPR_WINDOW  = 60 s                      (paper Appendix A.4)
COOLDOWN     = 30 s
Alg1 policy  = kvpr-global-v4            Prototype = simple-global
```

SLO: joint per request, `TTFT <= slo_ttft AND TPOT <= slo_tpot`; per-model bases
in `exp/configs/v2/slo_base.json` (many-model) and `exp/configs/v4het/slo_base.json`
(4-HET), scaled ×5 TTFT / ×3 TPOT from solo-unloaded p95.

| matrix | workloads | rates | seeds | runs |
|---|---|---|---|---|
| τ calibration | steady + bursty | 8, 10 | 3, 4 | 48 |
| many-model (Prism-favorable) | bursty | 2, 4, 6, 8, 10 | 7, 8 | 20 |
| final 4-HET (hold-out) | steady + bursty | 2, 4, 6, 8, 10 | 5, 6 | 40 |
| pilot (diagnostic only) | bursty | 16 | 9 | 2 |

**Seed ownership — do not blur these.** 1,2 historical/diagnostic · 3,4 τ
calibration · 5,6 final 4-HET hold-out · 7,8 final many-model · 9 pilot only.

Trace SHA256s: `exp/manifests/prism_final/*TRACE_MANIFEST.json`. Traces are
regenerated deterministically by `exp/scripts/build_paired_workload.py`; the
many-model variant adds `--hot-sets`, `--phase-duration 180`, `--hot-share 0.9`.

## 34–37. Running, resuming, validity

```bash
repro/prism_final/resume.sh --status        # what would run
repro/prism_final/resume.sh --verify-only   # environment + artifacts
repro/prism_final/resume.sh --resume        # asks for confirmation first
```

A condition is **VALID** when: `rc=0`, verification verdict PASS with no failed
gates, 0 aborted, 0 Algorithm-2 order violations, 0 staged-return failures,
lifecycle validity gate PASS, and the trace SHA256 equals the frozen manifest.

Lifecycle gate: `exp/scripts/lifecycle_validity_gate.py <run_dir>` — detects
runtime WorkerPool teardown without shutdown, controller cycle gaps > 30 s,
control requests unacknowledged > 30 s, and unexpected scheduler shutdowns. Its
unacknowledged-request check applies only to runs that emit `[V5-HOP]` acks; the
prototype does not, by design.

## 38–41. Known invalid artifacts, races, STOP behaviour

Nothing is deleted. Classification:
`exp/manifests/prism_final/ARTIFACT_CLASSIFICATION.json`.

| class | count |
|---|---:|
| `AUTHORITATIVE_FINAL` | 108 |
| `DIAGNOSTIC` | 2 |
| `EXCLUDED_PROTOCOL_CHANGE` | 2 |
| `KNOWN_INVALID_PRESERVED` | 6 |
| `REGRESSION_ONLY` | 1 |
| `HISTORICAL` | 5 |

**`nccl_port` race — known, unfixed.** `PortArgs` in `srt/server_args.py` does a
check-then-use on the torch-distributed port; all engines scan from the same
`start_port + 1`, so two can bind the same port and one dies with `EADDRINUSE`
before the server starts. Seen twice in ~110 runs. Fixing it would change runtime
semantics after the baseline freeze. Full note:
`exp/results/many-model-final/KNOWN_RUNTIME_RACE_nccl_port.md`. Symptom: run
fails in ~2 min with `algorithm2_ran`, `pipeline_rc_zero`, `no_deadlock` failing
because *nothing ran*. Remedy: one identical retry, preserving the failed attempt.

**STOP behaviour.** A run failure writes `exp/results/final-evaluation/STOP`; the
runners refuse to start while it exists. Clear it only after diagnosing the
cause, and record why.

## 42–45. Outputs, analysis, reports

Raw: `exp/results/{4het-tau-final, many-model-final, 4het-final, many-model-pilot}/`.
Inventory with sizes and checksums:
`exp/manifests/prism_final/RAW_ARTIFACT_INVENTORY.csv`.

Analysis: `exp/analysis/{final_summary, final_4het, many_model_prism_favorable,
tau_calibration, correctness_fix}/`. **Every final table and figure can be built
from `exp/analysis/final_summary/` alone** — see its `README.md` for columns,
units, aggregation and exclusions.

Reports: start at `reports/prism/REPORT_INDEX.md`.

## 46–50. Smoke test, runtimes, disk, monitoring

```bash
repro/prism_final/smoke_test.sh      # offline by default; SMOKE_TEST = PASS
```
Expected: 4 unit-test suites PASS, lifecycle gate rejects the known-bad run and
accepts a known-good one, regenerated trace byte-identical
(`492d79a4a2f34ccc`). Writes only under `exp/results/smoke-test/<timestamp>/`.

Full experiment runtime on this hardware: τ ≈ 8 h, many-model ≈ 3.5 h,
4-HET ≈ 7.5 h — about **19 h** for all 110 runs, ~10 min per run.

Monitoring: everything runs under `tmux`. `prism_final_auto` (stage runner),
`prism_orchestrator` (chain), `prism_notify` (enriched notifications).

## 51–52. Recovery

**After SSH disconnect** — tmux sessions survive. `tmux ls`, then
`resume.sh --status`. GPU work continues without the originating session.

**After a failed run** — read `exp/state/orchestrator.log` and the run's
`FAILURE_AUTOPSY.json`. Classify from primary evidence (`rc`, server log, kill
audit, OOM/CUDA/NCCL counters), never from the harness label — the harness
mislabels a dead server as `INVALID_CLIENT_FD_EXHAUSTION`. Harness-only failures
get **one** identical retry with the failed attempt preserved. Runtime
correctness failures **STOP**.

## 53–55. Git hygiene

Never commit: ShareGPT, model weights, `exp/results/`, `exp/workloads/*.pkl`,
any secret value. Never `git add .`, never `git push --force`.

Before every push: dangerous-history check (`git merge-base --is-ancestor
51c5405 HEAD` must fail), secret scan, large-file scan, staged-file inventory.

## 56–59. Tag, limitations, interpretation, future work

Final tag `prism-final-baseline-v1`.

**Limitations.** Two GPUs; two seeds per condition; synthetic workloads; the
favourable regime is bursty-only; the `nccl_port` race is unfixed; the trigger
behind the lifecycle stall remains INCONCLUSIVE; differences from the authors'
environment are unknown.

**Interpretation.** Corrected Prism helps only when *all* of: enough
consolidation to reclaim memory; demand that genuinely shifts; phases long enough
to amortise ~30 GB of migration; TPOT rather than TTFT binding; and load high
enough to create pressure but low enough to leave migration headroom. Drop any
one and the advantage disappears.

**Future work.** The obvious next step is a placement objective that prices
compute interference alongside memory pressure — a research contribution and a
deviation from the paper, to be built and labelled as a **variant**, never folded
into the paper-faithful baseline. Before that, the `nccl_port` race should be
fixed and the baseline re-frozen.

## 60. NEW SERVER — FIRST 30 MINUTES

```bash
# 1. clone and check out the exact handoff point
git clone https://github.com/meojun/Prism.git prism-exp
cd prism-exp
git checkout prism-final-baseline-v1

# 2. read this first
less reports/prism/11_handoff/PRISM_SERVER_HANDOFF.md
less reports/prism/REPORT_INDEX.md

# 3. environment (no secrets in the repo)
cp repro/prism_final/example.env $HOME/.prism.env && $EDITOR $HOME/.prism.env
set -a && . $HOME/.prism.env && set +a
export HF_HOME=${HF_HOME:-/workspace/.hf_home}
export SHAREGPT_JSON=${SHAREGPT_JSON:-/workspace/datasets/sharegpt/ShareGPT_V3_unfiltered_cleaned_split.json}

# 4. prepare (launches nothing)
bash repro/prism_final/bootstrap.sh

# 5. external assets
#    models  -> exp/manifests/prism_final/MODEL_MANIFEST.md
#    dataset -> exp/manifests/prism_final/DATASET_MANIFEST.md
sha256sum "$SHAREGPT_JSON"          # compare with DATASET_MANIFEST.md

# 6. rebuild the exact serving runtime
git -C prism-research reset --hard 595ec1f170e75a43897a7a2ad58ac5a9820aa2e8
git -C prism-research apply patches/lifecycle_containment/prism_research_worktree.patch

# 7. verify, in this order
bash repro/prism_final/verify_environment.sh     # NEW_SERVER_REPRO_STATUS = READY
bash repro/prism_final/verify_artifacts.sh       # ARTIFACT_VERIFICATION = PASS
bash repro/prism_final/smoke_test.sh             # SMOKE_TEST = PASS

# 8. what would run? (should be 0 — the evaluation is closed)
bash repro/prism_final/resume.sh --status
```

If step 7 reports READY / PASS / PASS and step 8 reports
`PENDING_CONDITIONS = 0`, the server reproduces the frozen baseline and the
results in `reports/prism/` are the authoritative ones.

```
PRISM_HANDOFF_STATE = READY
```
