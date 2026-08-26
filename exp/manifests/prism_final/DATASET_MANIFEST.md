# Dataset manifest

**Nothing here is committed to Git.**

| | |
|---|---|
| logical name | ShareGPT_V3_unfiltered_cleaned_split |
| source | `anon8231489123/ShareGPT_Vicuna_unfiltered` on Hugging Face, file `ShareGPT_V3_unfiltered_cleaned_split.json` |
| local path | `/workspace/datasets/sharegpt/ShareGPT_V3_unfiltered_cleaned_split.json` |
| size | 0.627 GB |
| sha256 | `35f0e213ce091ed9b9af2a1f0755e9d39f9ccec34ab281cd4ca60d70f6479ba4` |
| preprocessing | none — the generator reads it directly |
| used by | `exp/scripts/build_paired_workload.py --sharegpt` |

## Restore on a fresh server

```bash
mkdir -p /workspace/datasets/sharegpt
# download ShareGPT_V3_unfiltered_cleaned_split.json from the source above
sha256sum /workspace/datasets/sharegpt/ShareGPT_V3_unfiltered_cleaned_split.json       # must equal the value above
```

Traces are then regenerated deterministically from `(rate, seed, duration, model
set, revisions, slo_base)`; every trace SHA256 is recorded in the frozen trace
manifests under `exp/manifests/prism_final/`.

**Do not commit this file.** A historical local commit `51c5405` contains it and
is deliberately never pushed.
