# Many-model hot-pair pre-declaration (§22)

**Declared 2026-08-25T12:49:35.356571+00:00, before any pilot or many-model performance result
existed.** After this document, `HOT_SET_A/B/C` are immutable.

## Inputs

Used — static only:

- `model_size` (GB of weights)
- `cell_size` (KV bytes per token)

both read from `prism-research/python/sglang/multi_model/utils/model_info.json`,
the same file the runtime itself loads.

Explicitly **not** used: goodput, TTFT, TPOT, any measured performance, any
previous many-model result, any future seed outcome.

## Metric

Borda **rank-sum** of (model_size descending) and (cell_size descending). Ties
broken by larger `model_size`, then by slot name.

Rank-sum is used deliberately: GB and bytes/token have no natural common scale,
and any weighted sum would require inventing an exchange rate that could be
tuned. Rank-sum needs no such choice.

## Static memory-pressure ranking

| rank | slot | model | weight GB | KV B/tok | rank(w) | rank(c) | rank-sum |
|---:|---|---|---:|---:|---:|---:|---:|
| 1 | `model_5` | meta-llama/Llama-3.1-8B | 15.0801 | 131072 | 1 | 1 | **2** |
| 2 | `model_6` | Qwen/Qwen2.5-7B-Instruct | 14.2832 | 57344 | 2 | 3 | **5** |
| 3 | `model_3` | meta-llama/Llama-3.2-3B | 6.0000 | 114688 | 3 | 2 | **5** |
| 4 | `model_4` | Qwen/Qwen2.5-3B-Instruct | 5.8359 | 36864 | 4 | 4 | **8** |
| 5 | `model_2` | Qwen/Qwen2.5-1.5B-Instruct | 3.0078 | 28672 | 5 | 6 | **11** |
| 6 | `model_1` | meta-llama/Llama-3.2-1B | 2.2793 | 32768 | 6 | 5 | **11** |

Note `model_6` and `model_3` tie at rank-sum 5 and are separated by the declared
tie-break (larger weight first), which places `model_6` at rank 2.

## Pairing

Per §22: rank 1 + rank 6, rank 2 + rank 5, rank 3 + rank 4.

| hot set | heavy member | lighter member |
|---|---|---|
| **HOT_SET_A** | `model_5` meta-llama/Llama-3.1-8B (rank 1) | `model_1` meta-llama/Llama-3.2-1B (rank 6) |
| **HOT_SET_B** | `model_6` Qwen/Qwen2.5-7B-Instruct (rank 2) | `model_2` Qwen/Qwen2.5-1.5B-Instruct (rank 5) |
| **HOT_SET_C** | `model_3` meta-llama/Llama-3.2-3B (rank 3) | `model_4` Qwen/Qwen2.5-3B-Instruct (rank 4) |

## Why this matters

Pairing by model index would have produced a `model_5` + `model_6` hot set —
Llama-3.1-8B and Qwen2.5-7B together, the exact large-large co-residency that the
4-HET placement forensic showed to be memory-optimal but compute-hostile. That
would have handed the workload a built-in compute-contention pathology unrelated
to Prism's memory management.

**The declared ranking separates `model_5` and `model_6` into different hot
sets**, so each phase concentrates demand on one heavy model plus a light one.
This is the cleanest opportunity for Prism to demonstrate memory-management
value, consistent with the deliberately Prism-favorable framing of this regime.
