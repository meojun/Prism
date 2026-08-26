# Final Prism baseline — FROZEN

`FINAL_BASELINE_FROZEN = YES`   manifest sha256 `18ddbbd20bbcf24b1db2c49d0b126b8b080b1cba525c37ae3c4cdd363161c6e6`

No parameter below may change because of a performance result.

| | |
|---|---|
| RUN_CODE_COMMIT | `d122e01a911d58ba82e2aa83e12487b3fed251c9` |
| RUNTIME_SOURCE_TREE_HASH | `7fbd431c6a636df0c72bb6a40324f851d002d204` |
| runtime base commit | `595ec1f170e75a43897a7a2ad58ac5a9820aa2e8` |
| **FINAL_TAU** | **0.012859417696566448** |
| KVPR_WINDOW | 60 s |
| COOLDOWN | 30 s |
| Algorithm 1 | `kvpr-global-v4`, objective unchanged |
| Algorithm 2 | Moore-Hodgson, unchanged |
| GPUs | 2 x A100-SXM4-80GB, NVLink NV4 |

Token-rate estimator: input tokens of newly admitted requests **plus** decode
tokens of running requests, both over **one** 60 s sliding window. 60 s is
adopted for paper fidelity (Appendix A.4); it did not improve this workload and
reduced responsiveness under bursty shifts. That is recorded as a finding, not
repaired by tuning.

Full machine-readable detail in `FINAL_BASELINE_MANIFEST.json`.
