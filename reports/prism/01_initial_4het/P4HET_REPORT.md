# Prism vs Released Prototype -- 4-model paired evaluation

Generated 2026-08-25T01:23:34.167229+00:00

| | |
| --- | --- |
| design | paired per condition on byte-identical traces, Prototype then Prism |
| hardware | 2 x NVIDIA A100-SXM4-80GB, **one server, both arms** |
| runtime | `6618671`, built source verified byte-identical to the freeze |
| tau | **0.00035**, frozen before this evaluation |
| models | Llama-3.2-3B, Qwen2.5-3B-Instruct, Llama-3.1-8B, Qwen2.5-7B-Instruct |
| paired conditions | 20/20 |
| seeds per condition | 2 |

Both arms consumed the same trace **file**, not merely the same seed: each run's own client log records the trace it loaded, that file is hashed at aggregation time, and a condition is only paired when both hashes agree with each other and with the frozen workload manifest.

> **n = 2 seeds per condition.** Per-seed values are kept beside every mean below and in `aggregate/by_seed.csv`. No statistical significance is claimed and none should be read into these differences.

## Bursty

| rate | Proto goodput | Prism goodput | Δ% | Proto attain | Prism attain | Proto TTFT p99 | Prism TTFT p99 | Proto TPOT p99 | Prism TPOT p99 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 1.9146 | 1.8599 | -2.9% | 0.9429 | 0.9229 | 2.74 | 11.26 | 0.0379 | 0.0422 |
| 4 | 3.2340 | 3.2864 | +1.6% | 0.8346 | 0.8572 | 1.95 | 6.21 | 0.0528 | 0.0550 |
| 6 | 4.6809 | 4.5079 | -3.7% | 0.7878 | 0.7801 | 2.54 | 32.47 | 0.0619 | 0.1050 |
| 8 | 5.4745 | 5.5313 | +1.0% | 0.6997 | 0.7079 | 2.14 | 22.19 | 0.0707 | 0.2574 |
| 10 | 6.5452 | 6.2358 | -4.7% | 0.6751 | 0.6440 | 2.30 | 14.26 | 0.1035 | 0.2306 |

| rate | Proto thr | Prism thr | Δ% | Proto compl | Prism compl | Proto abort | Prism abort | Prism migrations |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 2.03 | 2.02 | -0.8% | 858 | 858 | 0 | 0 | 9.0 |
| 4 | 3.88 | 3.84 | -0.9% | 1656 | 1656 | 0 | 0 | 8.5 |
| 6 | 5.95 | 5.77 | -3.1% | 2554 | 2553 | 0 | 0 | 8.0 |
| 8 | 7.83 | 7.81 | -0.2% | 3346 | 3344 | 0 | 0 | 9.0 |
| 10 | 9.69 | 9.68 | -0.2% | 4160 | 4160 | 0 | 0 | 8.0 |

## Steady

| rate | Proto goodput | Prism goodput | Δ% | Proto attain | Prism attain | Proto TTFT p99 | Prism TTFT p99 | Proto TPOT p99 | Prism TPOT p99 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 1.9977 | 1.4826 | -25.8% | 0.9908 | 0.7398 | 0.23 | 7.96 | 0.0335 | 0.0489 |
| 4 | 3.7860 | 2.5057 | -33.8% | 0.9806 | 0.6552 | 0.24 | 10.63 | 0.0368 | 0.0646 |
| 6 | 5.6037 | 3.4275 | -38.8% | 0.9406 | 0.5782 | 0.28 | 8.81 | 0.0434 | 0.0664 |
| 8 | 6.8137 | 3.9772 | -41.6% | 0.8737 | 0.5147 | 0.30 | 15.66 | 0.0465 | 0.0991 |
| 10 | 6.9417 | 4.4731 | -35.6% | 0.7172 | 0.4622 | 0.29 | 12.72 | 0.0531 | 0.1104 |

| rate | Proto thr | Prism thr | Δ% | Proto compl | Prism compl | Proto abort | Prism abort | Prism migrations |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 2.02 | 2.01 | -0.2% | 858 | 858 | 0 | 0 | 11.0 |
| 4 | 3.86 | 3.84 | -0.6% | 1656 | 1656 | 0 | 0 | 13.5 |
| 6 | 5.96 | 5.94 | -0.3% | 2554 | 2553 | 0 | 0 | 12.0 |
| 8 | 7.80 | 7.74 | -0.8% | 3346 | 3346 | 0 | 0 | 9.5 |
| 10 | 9.68 | 9.68 | -0.0% | 4160 | 4160 | 0 | 0 | 8.0 |

## Aggregates

| scope | paired runs | Proto goodput | Prism goodput | Δ% | Proto TTFT p99 | Prism TTFT p99 | Δ% | Proto TPOT p99 | Prism TPOT p99 | Δ% |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| bursty | 10 | 4.3698 | 4.2843 | -2.0% | 2.33 | 17.28 | -640.3% | 0.0654 | 0.1380 | -111.1% |
| steady | 10 | 5.0285 | 3.1732 | -36.9% | 0.27 | 11.16 | -4056.9% | 0.0426 | 0.0779 | -82.6% |
| overall | 20 | 4.6992 | 3.7287 | -20.7% | 1.30 | 14.22 | -992.7% | 0.0540 | 0.1080 | -99.9% |

## Per-seed values

| workload | rate | seed | Proto goodput | Prism goodput | Proto TTFT p99 | Prism TTFT p99 | paired |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| bursty | 2 | 1 | 1.9453 | 1.8943 | 2.06 | 17.12 | True |
| bursty | 2 | 2 | 1.8840 | 1.8256 | 3.42 | 5.41 | True |
| bursty | 4 | 1 | 3.3516 | 3.2918 | 1.12 | 7.49 | True |
| bursty | 4 | 2 | 3.1164 | 3.2811 | 2.77 | 4.93 | True |
| bursty | 6 | 1 | 4.2433 | 4.0218 | 2.26 | 55.18 | True |
| bursty | 6 | 2 | 5.1185 | 4.9940 | 2.83 | 9.75 | True |
| bursty | 8 | 1 | 4.7221 | 5.0752 | 1.22 | 33.00 | True |
| bursty | 8 | 2 | 6.2270 | 5.9873 | 3.06 | 11.39 | True |
| bursty | 10 | 1 | 6.1806 | 5.8287 | 1.29 | 13.28 | True |
| bursty | 10 | 2 | 6.9097 | 6.6429 | 3.31 | 15.25 | True |
| steady | 2 | 1 | 2.0757 | 1.3253 | 0.22 | 8.23 | True |
| steady | 2 | 2 | 1.9197 | 1.6400 | 0.23 | 7.69 | True |
| steady | 4 | 1 | 3.9712 | 2.4272 | 0.23 | 11.94 | True |
| steady | 4 | 2 | 3.6007 | 2.5841 | 0.26 | 9.31 | True |
| steady | 6 | 1 | 5.7068 | 2.8661 | 0.26 | 9.50 | True |
| steady | 6 | 2 | 5.5006 | 3.9888 | 0.30 | 8.12 | True |
| steady | 8 | 1 | 6.7945 | 3.3349 | 0.28 | 19.82 | True |
| steady | 8 | 2 | 6.8329 | 4.6196 | 0.32 | 11.51 | True |
| steady | 10 | 1 | 6.6539 | 4.2756 | 0.27 | 23.36 | True |
| steady | 10 | 2 | 7.2295 | 4.6705 | 0.31 | 2.08 | True |

## What the numbers say

- **bursty**: Prism leads on Joint-SLO goodput at rates [4, 8]; it does not at [2, 6, 10].
- **steady**: Prism leads on Joint-SLO goodput at rates none; it does not at [2, 4, 6, 8, 10].
- First offered rate at which achieved throughput falls below 90% of offered (a saturation marker): {'bursty_prototype': None, 'bursty_prism': None, 'steady_prototype': None, 'steady_prism': None}.

## Workloads

| trace | requests | span (s) | mean rate | peak 10 s rate | sha256 |
| --- | ---: | ---: | ---: | ---: | --- |
| bursty_r10_s1.pkl | 4139 | 419.8 | 9.86 | 12.70 | `ffdacd9fbbeae9cb` |
| bursty_r10_s2.pkl | 4181 | 419.8 | 9.96 | 12.50 | `d71011d83d8a736a` |
| bursty_r2_s1.pkl | 889 | 418.7 | 2.12 | 3.50 | `02a876be7f74b4b0` |
| bursty_r2_s2.pkl | 828 | 419.4 | 1.97 | 3.10 | `91f68c75e78a675d` |
| bursty_r4_s1.pkl | 1742 | 419.5 | 4.15 | 6.00 | `e05431bbcaffaada` |
| bursty_r4_s2.pkl | 1569 | 419.6 | 3.74 | 5.10 | `945dd350103c18df` |
| bursty_r6_s1.pkl | 2584 | 418.8 | 6.17 | 8.00 | `6e557c39ebb65587` |
| bursty_r6_s2.pkl | 2523 | 419.9 | 6.01 | 8.00 | `d217f3c1a6f73ab8` |
| bursty_r8_s1.pkl | 3373 | 419.6 | 8.04 | 10.30 | `2a91c492d59c4ca8` |
| bursty_r8_s2.pkl | 3320 | 419.8 | 7.91 | 10.20 | `8b4427954a8fdba0` |
| steady_r10_s1.pkl | 4139 | 419.8 | 9.86 | 12.00 | `2e6bd7550c3bb130` |
| steady_r10_s2.pkl | 4181 | 419.7 | 9.96 | 12.50 | `d0d9b92e6deecf3c` |
| steady_r2_s1.pkl | 889 | 419.6 | 2.12 | 3.60 | `4ed6573a6483ce3f` |
| steady_r2_s2.pkl | 828 | 418.8 | 1.98 | 3.20 | `e4931cf6b85ea78a` |
| steady_r4_s1.pkl | 1742 | 419.8 | 4.15 | 6.20 | `2328c12a9667c954` |
| steady_r4_s2.pkl | 1569 | 418.9 | 3.75 | 5.80 | `163722b483d17662` |
| steady_r6_s1.pkl | 2584 | 419.8 | 6.15 | 8.80 | `bc958b7c75716902` |
| steady_r6_s2.pkl | 2523 | 418.9 | 6.02 | 8.30 | `aec93da2a2461927` |
| steady_r8_s1.pkl | 3373 | 419.8 | 8.03 | 10.60 | `bdebbdd1ad5fc8e6` |
| steady_r8_s2.pkl | 3320 | 419.5 | 7.91 | 10.10 | `ced4e84d8d40a4c7` |

## Limitations

- **n = 2 seeds per condition.** Enough to show a direction, not enough for a significance claim. Per-seed values are published above.
- tau was selected on an independent prior heterogeneous calibration setup and frozen before this evaluation. It was not re-calibrated for the 4-model mix, and it was not adjusted after seeing any result here.
- `c_i` is reused from the A100 profile of the same model revisions at the same precision on the same execution path. It was not re-measured.
- A separate 6-model heterogeneous run exists as an exploratory / stress pilot. It is not part of this baseline and its numbers are not mixed in.

