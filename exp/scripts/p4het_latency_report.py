#!/usr/bin/env python3
"""TTFT / TPOT / E2E percentile tables for the 4-model paired evaluation,
alongside the SLO each request actually carried.

Both arms go through final_metrics.collect(), the same implementation the
aggregation uses. Percentiles are computed per run from that run's own
per-request records, then averaged across the seeds of a condition; a
condition's per-seed values are printed too, because n=2.
"""
import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from final_metrics import collect  # noqa: E402

RATES = (2, 4, 6, 8, 10)
SEEDS = (1, 2)
KINDS = ("bursty", "steady")
ARMS = ("prototype", "prism")
PCT = ["ttft_p50", "ttft_p95", "ttft_p99",
       "tpot_p50", "tpot_p95", "tpot_p99",
       "e2e_p50", "e2e_p95", "e2e_p99"]


def mean(v):
    v = [x for x in v if isinstance(x, (int, float))]
    return statistics.fmean(v) if v else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path,
                    default=ROOT / "exp/results/4het-paired")
    args = ap.parse_args()
    out = args.out_dir / "aggregate"
    out.mkdir(parents=True, exist_ok=True)

    rows = []
    for kind in KINDS:
        for rate in RATES:
            for seed in SEEDS:
                for arm in ARMS:
                    d = args.out_dir / "raw" / arm / kind / f"rate_{rate}" / f"seed_{seed}"
                    if not list(d.glob("*_e2e_*rep.json")):
                        continue
                    m = collect(d)
                    r = {"workload": kind, "rate": rate, "seed": seed, "arm": arm,
                         "condition": f"{kind}{rate}"}
                    for k in PCT + ["ttft_mean", "tpot_mean", "e2e_mean",
                                    "joint_slo_attainment", "ttft_slo_attainment",
                                    "tpot_slo_attainment", "goodput_req_s",
                                    "completed", "aborted"]:
                        r[k] = m.get(k)
                    rows.append(r)

    with (out / "latency_percentiles_by_seed.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)

    cond = []
    for kind in KINDS:
        for rate in RATES:
            for arm in ARMS:
                rs = [r for r in rows if r["workload"] == kind
                      and r["rate"] == rate and r["arm"] == arm]
                if not rs:
                    continue
                c = {"workload": kind, "rate": rate, "arm": arm, "n_seeds": len(rs),
                     "seeds": ",".join(str(r["seed"]) for r in rs)}
                for k in PCT + ["ttft_mean", "tpot_mean", "e2e_mean",
                                "joint_slo_attainment"]:
                    c[k] = mean([r[k] for r in rs])
                    for r in rs:
                        c[f"{k}_s{r['seed']}"] = r[k]
                cond.append(c)
    with (out / "latency_percentiles_by_condition.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(cond[0]))
        w.writeheader(); w.writerows(cond)

    def fmt(v, s=3):
        return f"{v:.{s}f}" if isinstance(v, (int, float)) else "--"

    for kind in KINDS:
        print(f"\n### {kind}  (seconds; seed-averaged, n={len(SEEDS)})")
        print(f"{'rate':>4} {'arm':<10} "
              f"{'TTFT p50':>9}{'TTFT p95':>10}{'TTFT p99':>10}  "
              f"{'TPOT p50':>9}{'TPOT p95':>9}{'TPOT p99':>9}  "
              f"{'E2E p50':>9}{'E2E p95':>10}{'E2E p99':>10}")
        for rate in RATES:
            for arm in ARMS:
                c = next((x for x in cond if x["workload"] == kind
                          and x["rate"] == rate and x["arm"] == arm), None)
                if not c:
                    continue
                print(f"{rate:>4} {arm:<10} "
                      f"{fmt(c['ttft_p50']):>9}{fmt(c['ttft_p95']):>10}{fmt(c['ttft_p99']):>10}  "
                      f"{fmt(c['tpot_p50'],4):>9}{fmt(c['tpot_p95'],4):>9}{fmt(c['tpot_p99'],4):>9}  "
                      f"{fmt(c['e2e_p50']):>9}{fmt(c['e2e_p95']):>10}{fmt(c['e2e_p99']):>10}")
    print(f"\nruns included: {len(rows)}/40")
    print(f"-> {out/'latency_percentiles_by_condition.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
