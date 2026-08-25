#!/usr/bin/env python3
"""Section 21 -- is occupancy shape (3+1) or composition (large-large) the
stronger explanatory variable, plus the objective-margin distributions."""
import csv, json, statistics, sys
from collections import defaultdict
from pathlib import Path
ROOT = Path("/workspace/prism-exp")
OUT = ROOT / "exp/analysis/kvpr_placement_quality"
sys.path.insert(0, str(OUT))
MODELS = ("model_3", "model_4", "model_5", "model_6")
PRISM = {"OLD": ROOT / "exp/results/4het-paired/raw/prism",
         "NEW": ROOT / "exp/results/4het-estimator-correction/raw/prism-estimator"}
f = lambda x: float(x) if x not in ("", "None", None) else None


def pctl(v, q):
    v = sorted(x for x in v if x is not None)
    if not v:
        return None
    k = (len(v) - 1) * q / 100
    lo = int(k); hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


rows = [r for r in csv.DictReader(open(OUT / "cycle_objectives.csv"))
        if r["replay_match"] == "True"]

# ---------------- objective margin distributions -------------------------
print("=== Objective margin distributions (replay-confirmed cycles) ===")
print("  separated_penalty = (best large-separated peak KVPR - global best) / global best")
print(f"{'stratum':<22}{'n':>6}{'p50':>9}{'p90':>9}{'p95':>9}{'p99':>9}{'max':>9}")
marg = []
for lbl, sel in (("OLD steady", lambda r: r["estimator"] == "OLD" and r["workload"] == "steady"),
                 ("OLD bursty", lambda r: r["estimator"] == "OLD" and r["workload"] == "bursty"),
                 ("OLD seed1", lambda r: r["estimator"] == "OLD" and r["seed"] == "1"),
                 ("OLD seed2", lambda r: r["estimator"] == "OLD" and r["seed"] == "2"),
                 ("OLD all", lambda r: r["estimator"] == "OLD"),
                 ("NEW steady diag", lambda r: r["estimator"] == "NEW")):
    v = [f(r["separated_penalty"]) for r in rows if sel(r)]
    v = [x for x in v if x is not None]
    print(f"{lbl:<22}{len(v):>6}{pctl(v,50):>9.4f}{pctl(v,90):>9.4f}"
          f"{pctl(v,95):>9.4f}{pctl(v,99):>9.4f}{max(v):>9.4f}")
    marg.append({"stratum": lbl, "n": len(v), "p50": round(pctl(v, 50), 5),
                 "p90": round(pctl(v, 90), 5), "p95": round(pctl(v, 95), 5),
                 "p99": round(pctl(v, 99), 5), "max": round(max(v), 5)})
    band = lambda lo, hi: 100 * sum(1 for x in v if lo <= x < hi) / len(v)
    marg[-1].update({"pct_le_1": round(band(-1, 0.01), 1), "pct_1_5": round(band(0.01, 0.05), 1),
                     "pct_5_10": round(band(0.05, 0.10), 1),
                     "pct_gt_10": round(100 * sum(1 for x in v if x >= 0.10) / len(v), 1)})
with (OUT / "objective_margin.csv").open("w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(marg[0])); w.writeheader(); w.writerows(marg)
print("\n  descriptive bands (secondary to the raw percentiles above):")
for m in marg:
    print(f"    {m['stratum']:<22} <=1%:{m['pct_le_1']:5.1f}%  1-5%:{m['pct_1_5']:5.1f}%"
          f"  5-10%:{m['pct_5_10']:5.1f}%  >10%:{m['pct_gt_10']:5.1f}%")

# ---------- shape vs composition as explanatory variables -----------------
def intervals(est, cond, key, want):
    rs = sorted([r for r in rows if r["estimator"] == est and r["cond"] == cond],
                key=lambda r: float(r["timestamp"]))
    out, cur = [], None
    for a, b in zip(rs, rs[1:]):
        hit = (a[key] == "True") if want is True else (a[key] == want)
        if hit:
            t0, t1 = float(a["timestamp"]), float(b["timestamp"])
            if cur and abs(t0 - cur[1]) < 1e-6:
                cur = (cur[0], t1)
            else:
                if cur:
                    out.append(cur)
                cur = (t0, t1)
    if cur:
        out.append(cur)
    return out


print("\n=== Section 21: which variable separates Qwen2.5-7B TPOT better? ===")
print("  within-run ratio of Qwen7B TPOT p95, (state ON) / (state OFF)")
res, comp_r, shape_r = [], [], []
conds = sorted({(r["estimator"], r["cond"]) for r in rows})
for est, cond in conds:
    k, rr, s = cond.split("_")
    run = PRISM[est] / k / f"rate_{rr[1:]}" / f"seed_{s[1:]}"
    d = list(run.glob("requests/*_output_requests.json"))
    if not d:
        continue
    reqs = [x for x in json.load(d[0].open())
            if isinstance(x, dict) and x.get("success") and x.get("model") == "model_6"
            and x.get("arrival_time") is not None]
    if len(reqs) < 100:
        continue
    row = {"estimator": est, "cond": cond}
    for label, key, want in (("composition", "residency_colocated", True),
                             ("shape_3_1", "residency_shape", None)):
        if want is True:
            iv = intervals(est, cond, key, True)
        else:
            iv = [i for i in intervals(est, cond, key, "3+1")] + \
                 [i for i in intervals(est, cond, key, "1+3")]
        on = [x["tpot"] for x in reqs if any(a <= x["arrival_time"] <= b for a, b in iv)]
        off = [x["tpot"] for x in reqs if not any(a <= x["arrival_time"] <= b for a, b in iv)]
        if len(on) < 50 or len(off) < 50:
            row[label] = None
            continue
        row[label] = pctl(on, 95) / pctl(off, 95)
    res.append(row)
    if row.get("composition"):
        comp_r.append(row["composition"])
    if row.get("shape_3_1"):
        shape_r.append(row["shape_3_1"])
with (OUT / "shape_vs_composition.csv").open("w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=["estimator", "cond", "composition", "shape_3_1"])
    w.writeheader(); w.writerows(res)
print(f"  composition (large-large ON/OFF): n={len(comp_r):2d}  median ratio="
      f"{statistics.median(comp_r):.3f}  worse in {sum(1 for x in comp_r if x>1)}/{len(comp_r)}")
print(f"  occupancy shape (3+1 ON/OFF):     n={len(shape_r):2d}  median ratio="
      f"{statistics.median(shape_r):.3f}  worse in {sum(1 for x in shape_r if x>1)}/{len(shape_r)}")
