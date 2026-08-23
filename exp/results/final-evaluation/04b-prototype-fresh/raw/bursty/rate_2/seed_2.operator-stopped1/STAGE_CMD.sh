#!/bin/bash
set -euo pipefail
cd /root/Prism-final-regression-diagnosis
env PRISM_ROOT=/workspace/prism-exp PRISM_REPO=/root/Prism-final-regression-diagnosis/prism-research PRISM_EXP=/root/Prism-final-regression-diagnosis/exp BENCHMARK_TIMEOUT=1500 bash /root/Prism-final-regression-diagnosis/exp/scripts/run_v4_case.sh released-prototype bursty 2 2 /root/Prism-final-regression-diagnosis/exp/workloads/final-evaluation/bursty_r2_s2.pkl /root/Prism-final-regression-diagnosis/exp/results/final-evaluation/04b-prototype-fresh/raw/bursty/rate_2/seed_2 
