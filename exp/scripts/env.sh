#!/bin/bash
# Common environment for all Prism experiments.
# Usage:  source /workspace/prism-exp/exp/scripts/env.sh
export PRISM_ROOT=${PRISM_ROOT:-/workspace/prism-exp}
export PRISM_REPO=${PRISM_REPO:-$PRISM_ROOT/prism-research}
export PRISM_EXP=${PRISM_EXP:-$PRISM_ROOT/exp}
# The shared venv contains an editable sglang install from the bootstrap tree.
# Worktree experiments must import the source that belongs to their own arm,
# otherwise A and C silently execute the same implementation.
export PYTHONPATH="$PRISM_REPO/python${PYTHONPATH:+:$PYTHONPATH}"
export HF_HOME=/workspace/.hf_home
export PYTHONUNBUFFERED=1
# Datasets. real_trace.pkl (harness default) has synthetic "Hello "*n prompts;
# these two carry real ShareGPT text -- see exp/scripts/build_sharegpt_trace.py.
export DATASETS=${DATASETS:-/workspace/datasets}
export SHAREGPT_JSON=$DATASETS/sharegpt/ShareGPT_V3_unfiltered_cleaned_split.json
export SHAREGPT_CONTENT=$DATASETS/sharegpt/sharegpt_content.pkl
export SHAREGPT_FULL=$DATASETS/sharegpt/sharegpt_full.pkl
# Prism's kvcached-v0 talks to the engines through /dev/shm; keep it consistent.
export TOKENIZERS_PARALLELISM=false
source "$PRISM_ROOT/prism-venv/bin/activate"

# HF token (gated Llama/Mistral models). Put HF_TOKEN=... in /workspace/.env
if [ -f /workspace/.env ]; then
    set -a; . /workspace/.env; set +a
fi

# flashinfer's prefill scratch buffer is a FIXED allocation carved by its own
# AlignedAllocator -- not GPU memory. Upstream defaults to 384 MiB; model_6
# (Qwen2.5-7B, GQA 28 query heads to 4 KV heads) asks for 420-455 MiB at the
# higher rates, and the scheduler treats the failure as fatal: the worker's
# event loop calls kill_parent_process() and the whole server goes down
# mid-run. HANDOVER.md 4.4 and
# exp/results/paper-faithful-v4/provenance/ENVIRONMENT.md record 1 GiB as the
# setting for every run and every arm; it lived only in /workspace/.env and was
# lost when this instance was rebuilt, which killed three D2 runs before the
# cause was found. It belongs in the repository, not in a file outside it.
# Set after /workspace/.env so a stale copy of that file cannot lower it.
export FLASHINFER_WORKSPACE_SIZE=${FLASHINFER_WORKSPACE_SIZE_OVERRIDE:-1073741824}
