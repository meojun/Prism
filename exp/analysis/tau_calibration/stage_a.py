#!/usr/bin/env python3
"""Stage A -- Delta-r scale on the frozen estimator + 60 s window. Offline only.

Delta_r = max(0, current_peak_KVPR - best_peak_KVPR) per controller cycle, where
current is the ACTUAL runtime residency and best is the global optimum over the
valid placements. Definitions are imported unchanged from the Phase 7b forensic
and the 4-HET quality machinery.
"""
import csv, json, statistics, sys
from pathlib import Path
ROOT = Path("/workspace/prism-exp")
OUT = ROOT / "exp/analysis/tau_calibration"
sys.path.insert(0, str(ROOT / "exp/analysis/estimator_correction/bad_placement_forensic"))
sys.path.insert(0, str(ROOT / "exp/analysis/kvpr_placement_quality"))
sys.path.insert(0, str(ROOT / "exp/analysis/planner_oscillation"))
from forensic import kvpr_of, colocated, BIG                    # noqa: E402
from quality import enumerate_valid, pctl, MODELS               # noqa: E402
from solve_rates import cycles, solve                           # noqa: E402

W60 = ROOT / "exp/results/4het-window-calibration/raw/prism-w60"
CONDS = [(k, r, s) for k in ("steady", "bursty") for r in (8, 10) for s in (3, 4)]
QS = (10, 25, 50, 75, 90, 95, 99)
HIST_TAU = 0.00035


def rows_for(k, r, s):
    log = W60 / k / f"rate_{r}" / f"seed_{s}" / "server-logs/server.log.global_controller.log"
    out = []
    for c in cycles(log):
        rates, _ = solve(c)
        cur = c.get("current_placement") or {}
        if rates is None or len(cur) < 4:
            continue
        cur_pk, _ = kvpr_of(cur, rates)
        cand = enumerate_valid(rates)
        if not cand or cur_pk == float("inf"):
            continue
        best = cand[0][0]
        out.append({"workload": k, "rate": r, "seed": s, "cond": f"{k}_r{r}_s{s}",
                    "cycle": c["cycle"], "timestamp": c["timestamp"],
                    "current_peak_kvpr": cur_pk, "best_peak_kvpr": best,
                    "delta_r": max(0.0, cur_pk - best),
                    "residency_colocated": colocated(cur)})
    return out


def summarize(label, ds):
    pos = [d for d in ds if d > 0]
    row = {"stratum": label, "cycles": len(ds), "positive": len(pos),
           "frac_positive": round(len(pos) / len(ds), 4) if ds else None,
           "frac_gt_hist_tau": round(sum(1 for d in ds if d > HIST_TAU) / len(ds), 4) if ds else None}
    for q in QS:
        row[f"p{q}"] = pctl(pos, q) if pos else None
    row["max"] = max(pos) if pos else None
    row["mean"] = statistics.mean(pos) if pos else None
    return row


def main():
    allrows = []
    for k, r, s in CONDS:
        allrows += rows_for(k, r, s)
    if not allrows:
        print("STOP: no cycles parsed", file=sys.stderr); return 1
    with (OUT / "delta_cycles.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(allrows[0])); w.writeheader(); w.writerows(allrows)

    summ = []
    for k, r, s in CONDS:
        cond = f"{k}_r{r}_s{s}"
        summ.append(summarize(cond, [x["delta_r"] for x in allrows if x["cond"] == cond]))
    for wl in ("steady", "bursty"):
        summ.append(summarize(f"{wl} aggregate",
                              [x["delta_r"] for x in allrows if x["workload"] == wl]))
    pooled = summarize("POOLED calibration", [x["delta_r"] for x in allrows])
    summ.append(pooled)
    with (OUT / "delta_distribution.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summ[0])); w.writeheader(); w.writerows(summ)

    print(f"{'stratum':<22}{'cyc':>5}{'pos':>5}{'frac+':>8}{'>tau_h':>8}"
          + "".join(f"{'p'+str(q):>11}" for q in (25, 50, 75, 90, 95)) + f"{'max':>11}")
    for r_ in summ:
        fmt = lambda v: f"{v:11.6f}" if v is not None else f"{'-':>11}"
        print(f"{r_['stratum']:<22}{r_['cycles']:>5}{r_['positive']:>5}"
              f"{r_['frac_positive']:>8.3f}{r_['frac_gt_hist_tau']:>8.3f}"
              + "".join(fmt(r_[f'p{q}']) for q in (25, 50, 75, 90, 95)) + fmt(r_['max']))
    json.dump(pooled, open(OUT / "stage_a_pooled.json", "w"), indent=1, default=float)
    return 0


if __name__ == "__main__":
    sys.exit(main())
