#!/usr/bin/env python3
"""Freeze the final baseline and BOTH evaluation protocols (sections 18/19/26/27).

Runs immediately after FINAL_TAU is selected and before any pilot, so no
protocol can be influenced by a performance result.
"""
import hashlib, json, os, subprocess, sys, tempfile, datetime
from pathlib import Path
R = Path("/workspace/prism-exp"); MAN = R / "exp/manifests/prism_final"
MAN.mkdir(parents=True, exist_ok=True)
FINAL_TAU = float(sys.argv[1])
E = R / "repro/prism_final/environment"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def txt(p):
    q = E / p
    return q.read_text().strip() if q.exists() else "unavailable"


base = json.load(open(R / "exp/FINAL_BASELINE_MANIFEST.json"))
slo = base["slo"]["content"]
models = base["models"]
info = json.load(open(R / "prism-research/python/sglang/multi_model/utils/model_info.json"))
cfg6 = json.load(open(R / "exp/configs/v2/6model_2gpu.json"))
paths = {c["model_name"]: c["model_path"] for c in cfg6}

bm = {
 "what_this_is": "the frozen final Prism baseline; no parameter here may change "
                 "because of a performance result",
 "frozen_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
 "RUN_CODE_COMMIT": subprocess.run(["git","-C",str(R),"rev-parse","HEAD"],
                                   capture_output=True,text=True).stdout.strip(),
 "RUNTIME_SOURCE_TREE_HASH": (R/"patches/lifecycle_containment/RUNTIME_SOURCE_TREE_HASH").read_text().strip(),
 "runtime_worktree_patch_sha256": (R/"patches/lifecycle_containment/WORKTREE_PATCH_SHA256").read_text().strip(),
 "runtime_base_commit": "595ec1f170e75a43897a7a2ad58ac5a9820aa2e8",
 "FINAL_TAU": FINAL_TAU,
 "KVPR_WINDOW": 60,
 "COOLDOWN": 30,
 "token_rate_estimator": {
   "semantics": "token_rate = input tokens of newly admitted requests + decode "
                "tokens produced by running requests, both counted over ONE "
                "configured sliding window (paper section 4 + appendix A.4)",
   "window_s": 60,
   "decode_component": "windowed token counts with wall-clock expiry; not a "
                       "cached short-interval achieved-throughput scalar",
   "note": "60 s adopted for paper fidelity (A.4); it did not improve this "
           "workload and reduced responsiveness under bursty shifts"},
 "alg1": {"policy": "kvpr-global-v4", "objective": "unchanged from the paper",
          "tau_mode": "absolute-line8"},
 "alg2": {"policy": "Moore-Hodgson local arbitration, unchanged"},
 "migration": {"cooldown_s": 30, "overlap_migration": True, "kv_migration": True,
               "one_migration_per_cycle": True},
 "worker_pool": True, "tp_size": 1, "anti_affinity": None,
 "slo": {"definition": "joint: TTFT <= slo_ttft AND TPOT <= slo_tpot, per request",
         "base_source": "exp/configs/v2/slo_base.json (4-HET: exp/configs/v4het/slo_base.json)",
         "scale": "solo-unloaded p95 base x5 TTFT, x3 TPOT (paper 7.1 method)",
         "per_model_base": slo},
 "models": {k: {"model_path": paths.get(k), "revision": v["hf_snapshot"].rsplit("/",1)[-1],
                "tp_size": v["tp_size"],
                "model_size_gb": info[paths[k]]["model_size"],
                "cell_size_bytes": info[paths[k]]["cell_size"]} for k,v in models.items()},
 "trace_generation": {"generator": "exp/scripts/build_paired_workload.py",
                      "duration_4het_s": 420, "duration_many_model_s": 540},
 "environment": {"python": txt("python-version.txt"),
                 "torch": txt("torch-environment.txt").splitlines()[0] if (E/"torch-environment.txt").exists() else "unavailable",
                 "sglang": txt("sglang-version.txt").splitlines()[0] if (E/"sglang-version.txt").exists() else "unavailable",
                 "gpu": "2 x NVIDIA A100-SXM4-80GB", "nvlink": "NV4 between GPU0 and GPU1",
                 "driver": next((l.split("Driver Version:")[1].split()[0]
                                 for l in txt("nvidia-smi.txt").splitlines()
                                 if "Driver Version" in l), "unavailable")},
 "FINAL_BASELINE_FROZEN": "YES",
}
p = MAN/"FINAL_BASELINE_MANIFEST.json"
p.write_text(json.dumps(bm, indent=1))
h = sha(p)
(MAN/"FINAL_BASELINE_MANIFEST.sha256").write_text(h+"\n")

(MAN/"FINAL_BASELINE_MANIFEST.md").write_text(f"""# Final Prism baseline — FROZEN

`FINAL_BASELINE_FROZEN = YES`   manifest sha256 `{h}`

No parameter below may change because of a performance result.

| | |
|---|---|
| RUN_CODE_COMMIT | `{bm['RUN_CODE_COMMIT']}` |
| RUNTIME_SOURCE_TREE_HASH | `{bm['RUNTIME_SOURCE_TREE_HASH']}` |
| runtime base commit | `{bm['runtime_base_commit']}` |
| **FINAL_TAU** | **{FINAL_TAU!r}** |
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
""")

mm = {"what_this_is":"frozen Prism-favorable many-model protocol; fixed before the pilot ran",
 "regime_name":"PRISM_FAVORABLE_STRESS_REGIME",
 "framing":"deliberately best-case / applicability upper bound. NOT a representative "
           "general production workload. If Prism wins here that is not evidence of "
           "universal superiority; if it loses here, that is prominent evidence.",
 "frozen_utc": bm["frozen_utc"],
 "models":[c["model_name"] for c in cfg6],
 "hot_sets": json.load(open(MAN/"MANY_MODEL_PAIRING_PREDECLARATION.json"))["hot_sets"],
 "phase_sequence":["HOT_SET_A","HOT_SET_B","HOT_SET_C"],
 "phase_duration_s":180, "trace_duration_s":540,
 "hot_share":0.9, "per_hot_model_share":0.45, "per_background_model_share":0.025,
 "arrival_process":"canonical historical six-model bursty generator, reused unchanged; "
                   "only phase construction is deterministic",
 "TOTAL_OFFERED_RATES":[4,8,12,16,20], "final_seeds":[7,8],
 "arms":["prototype","prism"], "final_runs":20,
 "pilot":{"rate":16,"seed":9,"runs":2,"role":"DIAGNOSTIC ONLY, never in final statistics"},
 "immutable_after_freeze":["rates","seeds","phase_duration","hot_share","hot_sets","arms"],
 "no_performance_early_stop_once_final_matrix_starts": True}
(MAN/"MANY_MODEL_PROTOCOL_FROZEN.json").write_text(json.dumps(mm,indent=1))
(MAN/"MANY_MODEL_PROTOCOL_FROZEN.md").write_text(f"""# Prism-favorable many-model protocol — FROZEN

**Regime name: `PRISM_FAVORABLE_STRESS_REGIME`.** This workload is *deliberately
constructed to favour Prism*. It is a best-case, applicability-upper-bound
experiment, not a representative production distribution. If Prism wins here that
is not evidence of universal superiority. If Prism loses even here, that is
reported prominently.

| | |
|---|---|
| models | six-model historical set (`model_1` … `model_6`) |
| hot sets | A = model_5+model_1, B = model_6+model_2, C = model_3+model_4 |
| phases | A → B → C, **180 s each**, trace 540 s |
| skew | **90 %** on the phase's hot pair (45 % each), 10 % across the other four (2.5 % each) |
| arrivals | canonical historical six-model bursty generator, unchanged |
| rates | 4, 8, 12, 16, 20 |
| final seeds | 7, 8 |
| arms | Prototype, final Prism |
| final runs | **20** |
| pilot | r16 seed 9, 2 runs, **diagnostic only** |

180 s is chosen so the 60 s estimator can observe the new phase, Algorithm 1 can
respond, a migration can complete, and there is still time to amortise its cost.
Frozen before the pilot so no pilot result can change it.
""")

fh = {"what_this_is":"frozen final 4-HET protocol; untouched hold-out",
 "frozen_utc": bm["frozen_utc"],
 "workloads":["steady","bursty"], "rates":[2,4,6,8,10], "seeds":[5,6],
 "arms":["prototype","prism"], "conditions":20, "runs":40,
 "role":"unbiased hold-out applicability-boundary evaluation",
 "seeds_never_used_for_tuning": True,
 "no_performance_early_stop_once_started": True,
 "frozen_before_many_model_pilot": True}
(MAN/"FINAL_4HET_PROTOCOL_FROZEN.json").write_text(json.dumps(fh,indent=1))
print(f"FINAL_BASELINE_FROZEN = YES  manifest sha256 {h[:16]}")
print(f"  FINAL_TAU = {FINAL_TAU!r}")
print("  MANY_MODEL_PROTOCOL_FROZEN.json / .md written")
print("  FINAL_4HET_PROTOCOL_FROZEN.json written")
