# Model manifest

**Weights are never committed to Git.** `HF_HOME=/workspace/.hf_home`.

| slot | Hugging Face id | revision | weight GB | KV B/tok | TP |
|---|---|---|---:|---:|---:|
| `model_1` | meta-llama/Llama-3.2-1B | `4e20de362430…` | 2.2793 | 32768 | 1 |
| `model_2` | Qwen/Qwen2.5-1.5B-Instruct | `989aa7980e4c…` | 3.0078 | 28672 | 1 |
| `model_3` | meta-llama/Llama-3.2-3B | `13afe5124825…` | 6.0000 | 114688 | 1 |
| `model_4` | Qwen/Qwen2.5-3B-Instruct | `aa8e72537993…` | 5.8359 | 36864 | 1 |
| `model_5` | meta-llama/Llama-3.1-8B | `d04e592bb4f6…` | 15.0801 | 131072 | 1 |
| `model_6` | Qwen/Qwen2.5-7B-Instruct | `a09a35458c70…` | 14.2832 | 57344 | 1 |

dtype: engine default (bfloat16). Tokenizer ships with each pinned revision.

## Retrieval

```bash
export HF_HOME=/workspace/.hf_home
export HUGGING_FACE_HUB_TOKEN=...        # meta-llama repos are gated
huggingface-cli download <hf_id> --revision <revision>
```

Total weight footprint ≈ 46.5 GB.
The 4-HET experiments use `model_3`…`model_6`; the many-model experiments use all six.
`exp/configs/v2/model_revisions.json` and `exp/configs/v4het/model_revisions.json`
pin the tokenizer revisions the trace generator uses.
