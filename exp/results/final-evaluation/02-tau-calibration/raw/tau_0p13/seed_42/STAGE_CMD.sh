#!/bin/bash
set -euo pipefail
cd /root/Prism-final-regression-diagnosis
env PRISM_ROOT=/workspace/prism-exp PRISM_REPO=/root/Prism-final-regression-diagnosis/prism-research PRISM_EXP=/root/Prism-final-regression-diagnosis/exp KVPR_TAU=0.13 PREFILL_SPEED_FILE=/root/Prism-final-regression-diagnosis/exp/results/final-evaluation/01-ci-profile/prefill_speed_final_a100.json BENCHMARK_TIMEOUT=1500 bash /root/Prism-final-regression-diagnosis/exp/scripts/run_v4_case.sh paper-faithful-v6 bursty 20 42 /root/Prism-final-regression-diagnosis/exp/workloads/final-evaluation/bursty_r20_s42.pkl /root/Prism-final-regression-diagnosis/exp/results/final-evaluation/02-tau-calibration/raw/tau_0p13/seed_42 
