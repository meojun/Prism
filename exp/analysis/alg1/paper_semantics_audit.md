# Algorithm 1 implementation vs the paper's description

Reference for the paper's Algorithm 1 is `docs/paper_faithful/design_analysis.md`
§2, which tabulates the paper's rule while comparing it to the released
prototype. That table is the only line-level statement of the paper's algorithm
available in this repository; the paper itself is not in the tree. Items that
cannot be checked against it are marked UNKNOWN rather than assumed.

| element | paper (per design_analysis.md §2) | our `kvpr-global-v4` | verdict |
|---|---|---|---|
| per-model weight | `token_rate × token_size / SLO` | `token_rate * cell_size / tpot_slo` (`kvpr_global.py:123`) | **MATCH** |
| `token_size` | KV bytes per token | `model_weights_info[name]["cell_size"]` (`:115`) | **MATCH** |
| SLO weighting | TPOT SLO in the denominator | `self._tpot_slo(name)` (`:116`) | **MATCH** |
| model ordering | descending weighted token rate | `sorted(key=lambda m: (-weights[m]["weighted_token_rate"], m))` (`kvpr_global_v3.py:65`) | **MATCH** (name breaks ties) |
| KVPR | `Σ weighted_token_rate / shared_kv` | `_kvpr` (`kvpr_global.py:135-149`) | **MATCH** |
| `shared_kv` | `C − Σ resident model weights` | `_shared_kv` (`:127-133`) | **MATCH** |
| destination choice | GPU minimising resulting KVPR | `best = min(ratios, key=(ratio, index))` (`v3:66`) | **MATCH** |
| migration criterion | improvement > τ | `chosen = best if (current_r - best_r) > tau else current` (`v3:74`) | **MATCH** (literal absolute form) |
| τ application | single threshold | same, one τ for all models | **MATCH** |
| multi-model-per-GPU | allowed | allowed; `shared_kv` shrinks per placed model | **MATCH** |
| tie-breaking | not specified | lowest GPU index | **UNKNOWN** (paper silent) |
| **how many migrations per planning cycle** | **not stated in the reference** | **at most one** (`kvpr_global_v4.py:168-171`) | **DEVIATION (documented)** — the v4 docstring says outright: *"Emitting the whole plan at once is not obviously the paper's intent"* |
| **migration cooldown** | **not in the reference** | **30 s** (`kvpr_global.py:77`, `--kvpr-migration-cooldown`) | **DEVIATION (added)** |
| **target memory reserve gate** | **not in the reference** | `need = weights + target_reserve_gib` (6.46 GiB here); placement refused if the destination has less free (`kvpr_global_v4.py:110-121`) | **DEVIATION (added in v4, documented as a fix for a 7-minute activation stall)** |
| "last active model on source GPU" block | not in the reference | blocks the move (`v4:100-103`) | **DEVIATION (added)** |

## Reading

Every element of the **decision rule** — the objective, the weighting, the
ordering, the destination choice and the τ test — matches the paper description
available here. The deviations are all in **how the resulting plan is applied**:
one migration per cycle, a 30 s cooldown between migrations, a memory-reserve
feasibility gate, and a source-GPU emptiness block.

That distinction is what the Phase 3 evidence turns on: the plan is 2+2, and the
deviations are what stop the cluster from reaching it.
