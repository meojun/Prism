#!/usr/bin/env python3
"""Stage A3b -- what the counterfactual TPOT would do to Joint-SLO goodput.

Only the TPOT of requests that decoded under 3-model co-residency is replaced,
by the SAME RUN's SAME MODEL empirical 2-model-GPU quantile. TTFT is left
untouched. The primary SLO is unchanged. Descriptive bound, not a prediction.
"""
import bisect, csv, json, statistics, sys
from pathlib import Path
ROOT = Path("/workspace/prism-exp")
sys.path.insert(0, str(ROOT / "exp/analysis"))
from model_state_overlap import load_reqs   # noqa: E402
RAW = ROOT / "exp/results/4het-paired/raw/prism"
PROTO = ROOT / "exp/results/4het-paired/raw/prototype"
OUT = ROOT / "exp/analysis/swap_pathology_bound"
CONDS = [("steady", 8, 1), ("steady", 8, 2), ("steady", 10, 1), ("steady", 10, 2)]


def pct(v, q):
    v = sorted(v)
    if not v:
        return None
    k = (len(v) - 1) * q / 100
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def main():
    allc = json.loads((ROOT / "exp/analysis/alg1/alg1_cycles.json").read_text())
    rows = []
    for kind, rate, seed in CONDS:
        cyc = sorted([c for c in allc if (c["workload"], c["rate"], c["seed"]) == (kind, rate, seed)],
                     key=lambda x: x["timestamp"])
        ts, per_model = [], []
        for c in cyc:
            p = json.loads(c["current_placement"])
            if not p:
                continue
            cnt = {}
            for m, g in p.items():
                cnt[g] = cnt.get(g, 0) + 1
            ts.append(c["timestamp"]); per_model.append({m: cnt[g] for m, g in p.items()})
        run = RAW / kind / f"rate_{rate}" / f"seed_{seed}"
        reqs = [r for r in load_reqs(run) if isinstance(r, dict) and r.get("success")]
        if not (ts and reqs):
            continue
        dur = max(r["finish_time"] for r in reqs) - min(r["arrival_time"] for r in reqs)
        # empirical 2-model-GPU TPOT distribution, per model, same run
        two = {}
        tagged = []
        for r in reqs:
            m, dts = r.get("model"), r.get("decode_timestamps") or []
            if not dts or r.get("tpot") is None:
                continue
            j = bisect.bisect_right(ts, dts[0]) - 1
            n = per_model[j].get(m) if j >= 0 else None
            tagged.append((r, m, n))
            if n == 2:
                two.setdefault(m, []).append(r["tpot"])
        def score(mode):
            p = 0
            for r, m, n in tagged:
                t, st, sq = r.get("ttft"), r.get("slo_ttft"), r.get("slo_tpot")
                q = r["tpot"]
                if mode != "obs" and n is not None and n >= 3 and two.get(m):
                    q = pct(two[m], {"central": 50, "low": 75, "high": 25}[mode])
                if st is not None and sq is not None and t is not None \
                   and t <= st and q <= sq:
                    p += 1
            return p
        base = score("obs")
        pr = PROTO / kind / f"rate_{rate}" / f"seed_{seed}"
        preq = [x for x in load_reqs(pr) if isinstance(x, dict) and x.get("success")]
        pdur = (max(x["finish_time"] for x in preq) - min(x["arrival_time"] for x in preq)) if preq else 1
        ppass = sum(1 for x in preq
                    if x.get("ttft") is not None and x.get("tpot") is not None
                    and x.get("slo_ttft") and x.get("slo_tpot")
                    and x["ttft"] <= x["slo_ttft"] and x["tpot"] <= x["slo_tpot"])
        row = {"workload": kind, "rate": rate, "seed": seed, "n": len(tagged),
               "n_exposed": sum(1 for _, _, n in tagged if n and n >= 3),
               "prototype_goodput": round(ppass / pdur, 4),
               "observed_goodput": round(base / dur, 4)}
        for mode in ("low", "central", "high"):
            g = score(mode) / dur
            row[f"cf_{mode}_goodput"] = round(g, 4)
            row[f"cf_{mode}_recovery_pct"] = round(100 * (g - base / dur) / (base / dur), 1)
            row[f"cf_{mode}_gap_closed_pct"] = round(
                100 * (g - base / dur) / (ppass / pdur - base / dur), 1) if ppass / pdur > base / dur else None
        rows.append(row)
    (OUT / "goodput_bound.json").write_text(json.dumps(rows, indent=1))
    with (OUT / "goodput_bound.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    print("A3b. Goodput bound -- replacing ONLY the exposed requests' TPOT\n")
    print(f"{'cond':>12}{'exposed':>9}{'proto':>8}{'observed':>10}{'cf_low':>8}{'cf_central':>11}"
          f"{'cf_high':>9} | {'recov_ce%':>10}{'gapclosed_ce%':>14}")
    for r in rows:
        print(f"{r['workload']+str(r['rate'])+'s'+str(r['seed']):>12}{r['n_exposed']:>9}"
              f"{r['prototype_goodput']:>8.3f}{r['observed_goodput']:>10.3f}{r['cf_low_goodput']:>8.3f}"
              f"{r['cf_central_goodput']:>11.3f}{r['cf_high_goodput']:>9.3f} | "
              f"{r['cf_central_recovery_pct']:>10.1f}{str(r['cf_central_gap_closed_pct']):>14}")
    print("\ngap_closed% = share of the Prototype-vs-Prism goodput gap this bound would close.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
