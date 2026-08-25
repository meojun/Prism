#!/usr/bin/env python3
"""One row per Prism condition (Section 18)."""
import csv, json, statistics
from collections import Counter, defaultdict
from pathlib import Path
ROOT = Path("/workspace/prism-exp"); OUT = ROOT / "exp/analysis/kvpr_placement_quality"
PRISM = {"OLD": ROOT / "exp/results/4het-paired/raw/prism",
         "NEW": ROOT / "exp/results/4het-estimator-correction/raw/prism-estimator"}
NM = {"model_3": "L3.2-3B", "model_4": "Q2.5-3B", "model_5": "L3.1-8B", "model_6": "Q2.5-7B"}
f = lambda x: float(x) if x not in ("", "None", None) else None
allrows = list(csv.DictReader(open(OUT / "cycle_objectives.csv")))
lat = {(r["estimator"], r["cond"], r["residency"]): r
       for r in csv.DictReader(open(OUT / "composition_latency.csv"))}
by = defaultdict(list)
for r in allrows:
    by[(r["estimator"], r["cond"])].append(r)

rows = []
for (est, cond), rs in sorted(by.items()):
    ok = [r for r in rs if r["replay_match"] == "True"]
    n = len(ok) or 1
    k, rr, s = cond.split("_")
    run = PRISM[est] / k / f"rate_{rr[1:]}" / f"seed_{s[1:]}"
    num = json.loads((run / "VERIFICATION.json").read_text())["numbers"]
    span = max(float(r["timestamp"]) for r in rs) - min(float(r["timestamp"]) for r in rs)
    resid = sum(1 for r in rs if r["residency_colocated"] == "True") / len(rs)
    c = Counter(r["rank1_model"] for r in ok)
    top = c.most_common(1)[0] if c else (None, 0)
    sp = [f(r["separated_penalty"]) for r in ok if f(r["separated_penalty"]) is not None]
    q = lat.get((est, cond, "LARGE_COLOCATED"))
    rows.append({
        "estimator": est, "workload": k, "rate": int(rr[1:]), "seed": int(s[1:]),
        "cycles_total": len(rs), "replay_pct": round(100 * len(ok) / len(rs), 1),
        "rank1_top_model": NM.get(top[0]), "rank1_top_pct": round(100 * top[1] / n, 1),
        "kvpr_optimal_LL_pct": round(100 * sum(1 for r in ok if r["optimum_is_colocated"] == "True") / n, 1),
        "plan_LL_pct": round(100 * sum(1 for r in ok if r["plan_colocated"] == "True") / n, 1),
        "residency_LL_pct": round(100 * resid, 1),
        "LL_duration_pct_of_run": round(100 * resid, 1),
        "sep_penalty_p50": round(statistics.median(sp), 5) if sp else None,
        "qwen7b_tpot_p50_LLms": round(f(q["qwen7b_tpot_p50"]) * 1000, 1) if q and f(q["qwen7b_tpot_p50"]) else None,
        "qwen7b_tpot_p95_LLms": round(f(q["qwen7b_tpot_p95"]) * 1000, 1) if q and f(q["qwen7b_tpot_p95"]) else None,
        "qwen7b_tpot_p99_LLms": round(f(q["qwen7b_tpot_p99"]) * 1000, 1) if q and f(q["qwen7b_tpot_p99"]) else None,
        "goodput_req_s": round(num["joint_slo_goodput_req_s"], 4),
        "attainment": round(num["joint_slo_attainment"], 4),
        "throughput_req_s": round(num["throughput_req_s"], 4),
        "migrations": num["migrations_executed"],
    })
with (OUT / "condition_summary.csv").open("w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
hdr = f"{'est':>4}{'cond':>14}{'cyc':>5}{'rply':>6}{'rank1':>10}{'%':>6}{'optLL':>7}{'planLL':>8}{'resLL':>7}{'gp':>8}{'att':>7}{'mig':>5}"
print(hdr); print("-" * len(hdr))
for r in rows:
    print(f"{r['estimator']:>4}{r['workload']+'_r'+str(r['rate'])+'_s'+str(r['seed']):>14}"
          f"{r['cycles_total']:>5}{r['replay_pct']:>5.0f}%{str(r['rank1_top_model']):>10}"
          f"{r['rank1_top_pct']:>5.0f}%{r['kvpr_optimal_LL_pct']:>6.1f}%{r['plan_LL_pct']:>7.1f}%"
          f"{r['residency_LL_pct']:>6.1f}%{r['goodput_req_s']:>8.3f}{r['attainment']:>7.3f}{r['migrations']:>5}")
