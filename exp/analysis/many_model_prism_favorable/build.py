#!/usr/bin/env python3
"""Final 4-HET analysis: paired Prototype vs Prism on the untouched hold-out."""
import csv, glob, json, statistics, subprocess, sys
from pathlib import Path
R = Path("/workspace/prism-exp")
OUT = R / "exp/analysis/many_model_prism_favorable"
RAW = R / "exp/results/many-model-final/raw"
WL = ["bursty"]; RATES = [2, 4, 6, 8, 10]; SEEDS = [7, 8]
MODELS = ("model_1","model_2","model_3","model_4","model_5","model_6")
NAMES = {"model_1":"Llama-3.2-1B","model_2":"Qwen2.5-1.5B","model_3": "Llama-3.2-3B", "model_4": "Qwen2.5-3B",
         "model_5": "Llama-3.1-8B", "model_6": "Qwen2.5-7B"}


def pctl(v, q):
    v = sorted(x for x in v if x is not None)
    if not v:
        return None
    k = (len(v) - 1) * q / 100; lo = int(k); hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def one(arm, k, r, s):
    d = RAW / arm / k / f"rate_{r}" / f"seed_{s}"
    v = json.loads((d / "VERIFICATION.json").read_text()); n = v["numbers"]
    f = glob.glob(str(d / "requests/*_output_requests.json"))
    rs = [x for x in json.load(open(f[0])) if isinstance(x, dict) and x.get("success")] if f else []
    N = len(rs) or 1
    a = [x.get("ttft") is not None and x.get("slo_ttft") is not None and x["ttft"] <= x["slo_ttft"] for x in rs]
    b = [x.get("tpot") is not None and x.get("slo_tpot") is not None and x["tpot"] <= x["slo_tpot"] for x in rs]
    g = subprocess.run(["/workspace/prism-exp/prism-venv/bin/python",
                        str(R / "exp/scripts/lifecycle_validity_gate.py"), str(d)],
                       capture_output=True, text=True)
    row = {"arm": arm, "workload": k, "rate": r, "seed": s,
           "run_dir": str(d.relative_to(R)), "rc": int(v["rc"]), "verdict": v["verdict"],
           "lifecycle_gate": "PASS" if g.returncode == 0 else "FAIL",
           "offered": n["offered_requests"], "completed": n["completed"],
           "aborted": n["aborted"], "alg2_order_violations": n["alg2_order_violations"],
           "goodput_req_s": round(n["joint_slo_goodput_req_s"], 5),
           "attainment": round(n["joint_slo_attainment"], 5),
           "throughput_req_s": round(n["throughput_req_s"], 4),
           "ttft_slo_ok_pct": round(100 * sum(a) / N, 3),
           "tpot_slo_ok_pct": round(100 * sum(b) / N, 3),
           "migrations": n["migrations_executed"],
           "migration_bytes": (n.get("migrated_weight_bytes", 0) or 0) + (n.get("migrated_kv_bytes", 0) or 0)}
    for m, sc, lbl in (("ttft", 1, "ttft_s"), ("tpot", 1000, "tpot_ms")):
        vals = [x.get(m) for x in rs]
        for q in (50, 95, 99):
            p = pctl(vals, q)
            row[f"{lbl}_p{q}"] = round(p * sc, 4) if p is not None else None
    for mm in MODELS:
        sub = [x for x in rs if x.get("model") == mm and x.get("tpot") is not None]
        row[f"{NAMES[mm]}_tpot_p95_ms"] = round(pctl([x["tpot"] for x in sub], 95) * 1000, 2) if sub else None
    return row


rows = [one(a, k, r, s) for a in ("prototype", "prism") for k in WL for r in RATES for s in SEEDS]
with (OUT / "run_manifest.csv").open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
idx = {(x["arm"], x["workload"], x["rate"], x["seed"]): x for x in rows}

paired = []
for k in WL:
    for r in RATES:
        for s in SEEDS:
            p, m = idx[("prototype", k, r, s)], idx[("prism", k, r, s)]
            paired.append({"workload": k, "rate": r, "seed": s,
                "proto_goodput": p["goodput_req_s"], "prism_goodput": m["goodput_req_s"],
                "delta_abs": round(m["goodput_req_s"] - p["goodput_req_s"], 5),
                "delta_pct": round(100 * (m["goodput_req_s"] - p["goodput_req_s"]) / p["goodput_req_s"], 2),
                "proto_attain": p["attainment"], "prism_attain": m["attainment"],
                "proto_ttft_ok": p["ttft_slo_ok_pct"], "prism_ttft_ok": m["ttft_slo_ok_pct"],
                "proto_tpot_ok": p["tpot_slo_ok_pct"], "prism_tpot_ok": m["tpot_slo_ok_pct"],
                "prism_migrations": m["migrations"]})
with (OUT / "paired_goodput.csv").open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(paired[0])); w.writeheader(); w.writerows(paired)

cond = []
for k in WL:
    for r in RATES:
        ps = [x for x in paired if x["workload"] == k and x["rate"] == r]
        cond.append({"workload": k, "rate": r,
            "proto_goodput_mean": round(statistics.mean(x["proto_goodput"] for x in ps), 5),
            "prism_goodput_mean": round(statistics.mean(x["prism_goodput"] for x in ps), 5),
            "delta_pct_mean": round(statistics.mean(x["delta_pct"] for x in ps), 2),
            "proto_ttft_ok": round(statistics.mean(x["proto_ttft_ok"] for x in ps), 2),
            "prism_ttft_ok": round(statistics.mean(x["prism_ttft_ok"] for x in ps), 2),
            "proto_tpot_ok": round(statistics.mean(x["proto_tpot_ok"] for x in ps), 2),
            "prism_tpot_ok": round(statistics.mean(x["prism_tpot_ok"] for x in ps), 2),
            "prism_migrations_mean": round(statistics.mean(x["prism_migrations"] for x in ps), 1)})
with (OUT / "condition_summary.csv").open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(cond[0])); w.writeheader(); w.writerows(cond)

print("PRISM-FAVORABLE MANY-MODEL (seeds 7/8, bursty) — paired Prototype vs Prism\n")
for k in WL:
    print(f"  {k.upper()}")
    print(f"{'rate':>7}{'Proto gp':>11}{'Prism gp':>11}{'delta':>9}   "
          f"{'TTFT ok P->S':>16}{'TPOT ok P->S':>18}{'migr':>6}")
    for c in [x for x in cond if x["workload"] == k]:
        print(f"{c['rate']:>7}{c['proto_goodput_mean']:>11.4f}{c['prism_goodput_mean']:>11.4f}"
              f"{c['delta_pct_mean']:>+8.1f}%   {c['proto_ttft_ok']:>6.1f}% ->{c['prism_ttft_ok']:>6.1f}%"
              f"{c['proto_tpot_ok']:>9.1f}% ->{c['prism_tpot_ok']:>6.1f}%{c['prism_migrations_mean']:>6.1f}")
    ps = [x for x in paired if x["workload"] == k]
    print(f"{'MEAN':>7}{statistics.mean(x['proto_goodput'] for x in ps):>11.4f}"
          f"{statistics.mean(x['prism_goodput'] for x in ps):>11.4f}"
          f"{statistics.mean(x['delta_pct'] for x in ps):>+8.1f}%\n")
allp = statistics.mean(x["proto_goodput"] for x in paired)
allm = statistics.mean(x["prism_goodput"] for x in paired)
print(f"  OVERALL  Prototype {allp:.4f}  Prism {allm:.4f}  "
      f"delta {100*(allm-allp)/allp:+.1f}%  (mean of paired deltas "
      f"{statistics.mean(x['delta_pct'] for x in paired):+.1f}%)")
print(f"  validity: {sum(1 for x in rows if x['verdict']=='PASS' and x['lifecycle_gate']=='PASS')}/20 "
      f"verdict+lifecycle PASS, aborted total {sum(x['aborted'] for x in rows)}, "
      f"alg2 violations {sum(x['alg2_order_violations'] for x in rows)}")
