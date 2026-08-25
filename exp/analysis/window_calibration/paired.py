#!/usr/bin/env python3
"""Paired 30 s -> 60 s deltas and downstream latency, per condition.

Traces are byte-identical between arms, so every comparison is paired within
condition; nothing is pooled before the condition-level table is produced.
"""
import csv, json, statistics, sys
from collections import defaultdict
from pathlib import Path
ROOT = Path("/workspace/prism-exp")
OUT = ROOT / "exp/analysis/window_calibration"
sys.path.insert(0, str(ROOT / "exp/analysis/kvpr_placement_quality"))
from quality import pctl, MODELS, NAMES                        # noqa: E402
RAW = ROOT / "exp/results/4het-window-calibration/raw"
ARMS = {30: RAW / "prism-w30", 60: RAW / "prism-w60"}
CONDS = [(k, r, s) for k in ("steady", "bursty") for r in (8, 10) for s in (3, 4)]
f = lambda x: float(x) if x not in ("", "None", None) else None


def load(name):
    return list(csv.DictReader(open(OUT / f"{name}.csv")))


def latency():
    rows = []
    for win, base in ARMS.items():
        for k, r, s in CONDS:
            run = base / k / f"rate_{r}" / f"seed_{s}"
            vf = run / "VERIFICATION.json"
            if not vf.exists():
                continue
            n = json.loads(vf.read_text())["numbers"]
            d = list(run.glob("requests/*_output_requests.json"))
            per = defaultdict(lambda: defaultdict(list))
            if d:
                for x in json.load(d[0].open()):
                    if not isinstance(x, dict) or not x.get("success"):
                        continue
                    m = x.get("model")
                    if m in MODELS:
                        per[m]["tpot"].append(x.get("tpot"))
                        per[m]["itl"].extend(x.get("itl") or [])
            row = {"window_s": win, "workload": k, "rate": r, "seed": s,
                   "cond": f"{k}_r{r}_s{s}",
                   "completed": n["completed"], "offered": n["offered_requests"],
                   "aborted": n["aborted"], "client_errors": n["client_errors"],
                   "alg2_order_violations": n["alg2_order_violations"],
                   "staged_return_failures": n["staged_return_failures"],
                   "migrations": n["migrations_executed"],
                   "goodput_req_s": round(n["joint_slo_goodput_req_s"], 4),
                   "attainment": round(n["joint_slo_attainment"], 4),
                   "throughput_req_s": round(n["throughput_req_s"], 4),
                   "agg_tpot_p50_ms": round(n["tpot_mean_s"] * 1000, 2),
                   "agg_tpot_p99_ms": round(n["tpot_p99_s"] * 1000, 2),
                   "ttft_p99_s": round(n["ttft_p99_s"], 3),
                   "ttft_mean_s": round(n["ttft_mean_s"], 4)}
            for m in ("model_5", "model_6"):
                for q in (50, 95, 99):
                    v = pctl(per[m]["tpot"], q)
                    row[f"{NAMES[m]}_tpot_p{q}_ms"] = round(v * 1000, 2) if v else None
                for q in (50, 95, 99):
                    v = pctl(per[m]["itl"], q)
                    row[f"{NAMES[m]}_itl_p{q}_ms"] = round(v * 1000, 2) if v else None
            rows.append(row)
    with (OUT / "latency_summary.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    return rows


def main():
    rank = {(int(r["window_s"]), r["cond"]): r for r in load("rank_dynamics")}
    plan = {(int(r["window_s"]), r["cond"]): r for r in load("planner_stability")}
    comp = {(int(r["window_s"]), r["cond"]): r for r in load("placement_composition")}
    lat = {(r["window_s"], r["cond"]): r for r in latency()}

    fields = [("rank1_switches_per_min", rank), ("rank1_lifetime_p50_s", rank),
              ("rank1_small_pct", rank),
              ("kvpr_optimal_LL_pct", comp), ("plan_LL_pct", comp),
              ("residency_LL_pct", comp), ("three_one_exposure_pct", comp),
              ("plan_changes_per_min", plan), ("plan_lifetime_p50_s", plan),
              ("migrations", plan), ("reversals", plan), ("ping_pong", plan)]
    out = []
    for k, r, s in CONDS:
        cond = f"{k}_r{r}_s{s}"
        if (30, cond) not in plan or (60, cond) not in plan:
            continue
        row = {"workload": k, "rate": r, "seed": s, "cond": cond}
        for name, src in fields:
            a, b = f(src[(30, cond)].get(name)), f(src[(60, cond)].get(name))
            row[f"{name}_30"], row[f"{name}_60"] = a, b
            row[f"{name}_delta"] = (round(b - a, 4) if a is not None and b is not None else None)
        out.append(row)
    with (OUT / "paired_window_deltas.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0])); w.writeheader(); w.writerows(out)

    def show(title, keys, src=None):
        print(f"\n=== {title} ===")
        hdr = f"{'cond':<16}" + "".join(f"{k.split('_')[0][:9]:>11}" for k in keys)
        print(f"{'cond':<16}" + "".join(f"{n:>22}" for n in keys))
        for r in out:
            line = f"{r['cond']:<16}"
            for n in keys:
                a, b, d = r[f"{n}_30"], r[f"{n}_60"], r[f"{n}_delta"]
                line += f"{a!s:>7}->{b!s:>7}{('' if d is None else f'{d:+.2f}'):>8}"
            print(line)

    show("Rank dynamics (30s -> 60s, delta)",
         ["rank1_switches_per_min", "rank1_lifetime_p50_s", "rank1_small_pct"])
    show("Planner stability", ["plan_changes_per_min", "plan_lifetime_p50_s", "migrations"])
    show("Placement composition", ["kvpr_optimal_LL_pct", "plan_LL_pct", "residency_LL_pct"])

    print("\n=== pooled medians of the paired deltas (8 conditions) ===")
    for name, _ in fields:
        v = [r[f"{name}_delta"] for r in out if r[f"{name}_delta"] is not None]
        if not v:
            continue
        neg = sum(1 for x in v if x < 0); pos = sum(1 for x in v if x > 0)
        print(f"  {name:<26} median {statistics.median(v):+9.3f}   "
              f"down {neg}/{len(v)}  up {pos}/{len(v)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
