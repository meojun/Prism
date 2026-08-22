#!/bin/bash
set -euo pipefail
cd /root/Prism-final-regression-diagnosis
env PRISM_ROOT=/workspace/prism-exp PRISM_REPO=/root/Prism-final-regression-diagnosis/prism-research PRISM_EXP=/root/Prism-final-regression-diagnosis/exp KVPR_TAU=0.07 BENCHMARK_TIMEOUT=1500 bash exp/scripts/run_v4_case.sh paper-faithful-v6 bursty 20 1 /root/Prism-final-regression-diagnosis/exp/results/baseline-readiness/raw/migration-D2/workload/bursty_r20_s1.pkl /root/Prism-final-regression-diagnosis/exp/results/final-baseline-ready/raw/interaction-D3/run6 
