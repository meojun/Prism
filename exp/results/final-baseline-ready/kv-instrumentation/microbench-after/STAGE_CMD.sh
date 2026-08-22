#!/bin/bash
set -euo pipefail
cd /root/Prism-final-regression-diagnosis
env PRISM_ROOT=/workspace/prism-exp PRISM_REPO=/root/Prism-final-regression-diagnosis/prism-research bash -c source\ /root/Prism-final-regression-diagnosis/exp/scripts/env.sh\ \&\&\ python3\ /root/Prism-final-regression-diagnosis/exp/scripts/microbench_kv_migration.py\ --profiles\ model_3\ model_4\ model_6\ --reps\ 2\ --out\ /root/Prism-final-regression-diagnosis/exp/results/final-baseline-ready/kv-instrumentation/microbench_after.json 
