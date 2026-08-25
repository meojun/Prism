#!/usr/bin/env python3
"""Recompute the historical Prototype arm and compare it to Final Prism.

Both arms go through the same `final_metrics.collect`, so neither is read from
an earlier summary -- the stale armA summary.csv is never opened. Only the
conditions the provenance pass cleared are compared, and VERIFIED and
PARTIALLY_VERIFIED are kept in separate columns rather than pooled.

Sign convention: a positive delta always means Final Prism is better. Goodput,
attainment and throughput are (final - proto) / proto; the latencies are
(proto - final) / proto.
"""
import argparse
import csv
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from final_metrics import collect  # noqa: E402

CONDITIONS = [("bursty", 2), ("bursty", 4), ("bursty", 8), ("bursty", 14),
              ("bursty", 20), ("steady", 4), ("steady", 8), ("steady", 20)]
SEEDS = (1, 2, 3)
HIGHER_BETTER = ("goodput_req_s", "joint_slo_attainment",
                 "achieved_throughput_req_s", "completed")
LOWER_BETTER = ("ttft_mean", "ttft_p99", "tpot_mean", "tpot_p99",
                "e2e_mean", "e2e_p99", "aborted")
METRICS = HIGHER_BETTER + LOWER_BETTER


def delta_pct(proto, final, metric):
    if not isinstance(proto, (int, float)) or not isinstance(final, (int, float)):
        return None
    if proto == 0:
        return None
    if metric in HIGHER_BETTER:
        return (final - proto) / proto * 100.0
    return (proto - final) / proto * 100.0


def mean(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return statistics.fmean(vals) if vals else None


def sd(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return statistics.stdev(vals) if len(vals) > 1 else (0.0 if vals else None)


def write_csv(path, rows):
    if not rows:
        return
    fields = list(dict.fromkeys(k for r in rows for k in r))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path,
                    default=ROOT / "exp/results/final-evaluation")
    ap.add_argument("--historical", type=Path,
                    default=ROOT / ("exp/results/final-prototype-vs-paper-faithful"
                                    "/raw/armA/raw/released-prototype"))
    args = ap.parse_args()
    out = args.out_dir / "06-aggregate"
    out.mkdir(parents=True, exist_ok=True)

    prov_path = out / "HISTORICAL_PROTOTYPE_PROVENANCE.json"
    if not prov_path.is_file():
        print("FATAL: run historical_prototype_verify.py first", file=sys.stderr)
        return 2
    prov = json.loads(prov_path.read_text())
    verdict_of = {(c["workload"], c["rate"], c["seed"]): c["verdict"]
                  for c in prov["conditions"]}

    # ---- per-seed rows, both arms, one metric implementation --------------
    seed_rows, usable = [], 0
    for wl, rate in CONDITIONS:
        for seed in SEEDS:
            v = verdict_of.get((wl, rate, seed), "NOT_VERIFIED")
            fdir = args.out_dir / "05-final-c/raw" / wl / f"rate_{rate}" / f"seed_{seed}"
            hdir = args.historical / wl / f"rate_{rate}" / f"seed_{seed}"
            row = {"condition": f"{wl}{rate}", "workload": wl, "rate": rate,
                   "seed": seed, "provenance_verdict": v}
            fin = collect(fdir) if list(fdir.glob("*_e2e_*rep.json")) else None
            pro = (collect(hdir) if (v != "NOT_VERIFIED"
                                     and list(hdir.glob("*_e2e_*rep.json")))
                   else None)
            row["final_present"] = fin is not None
            row["prototype_usable"] = pro is not None
            for m in METRICS:
                row[f"final_{m}"] = fin.get(m) if fin else None
                row[f"prototype_{m}"] = pro.get(m) if pro else None
                row[f"delta_pct_{m}"] = delta_pct(
                    pro.get(m) if pro else None,
                    fin.get(m) if fin else None, m)
            if fin and pro:
                usable += 1
            seed_rows.append(row)
    write_csv(out / "prototype_vs_final_by_seed.csv", seed_rows)

    # ---- condition level: seeds 1-3 averaged ------------------------------
    cond_rows = []
    for wl, rate in CONDITIONS:
        rs = [r for r in seed_rows if r["workload"] == wl and r["rate"] == rate]
        paired = [r for r in rs if r["final_present"] and r["prototype_usable"]]
        row = {"condition": f"{wl}{rate}", "workload": wl, "rate": rate,
               "n_final": sum(1 for r in rs if r["final_present"]),
               "n_prototype": sum(1 for r in rs if r["prototype_usable"]),
               "n_paired": len(paired),
               "provenance": ",".join(sorted({r["provenance_verdict"] for r in rs})),
               "paired_seeds": ",".join(str(r["seed"]) for r in paired)}
        for m in METRICS:
            fm = mean([r[f"final_{m}"] for r in rs])
            pm = mean([r[f"prototype_{m}"] for r in rs])
            row[f"final_{m}"] = fm
            row[f"final_{m}_sd"] = sd([r[f"final_{m}"] for r in rs])
            row[f"prototype_{m}"] = pm
            row[f"prototype_{m}_sd"] = sd([r[f"prototype_{m}"] for r in rs])
            row[f"delta_pct_{m}"] = delta_pct(pm, fm, m)
        cond_rows.append(row)
    write_csv(out / "prototype_vs_final_by_condition.csv", cond_rows)

    # ---- bursty / steady / overall ---------------------------------------
    scope_rows = []
    for scope, sel in (("bursty", lambda r: r["workload"] == "bursty"),
                       ("steady", lambda r: r["workload"] == "steady"),
                       ("overall", lambda r: True)):
        rs = [r for r in seed_rows if sel(r)]
        paired = [r for r in rs if r["final_present"] and r["prototype_usable"]]
        row = {"scope": scope, "n_runs": len(rs), "n_paired": len(paired)}
        for m in METRICS:
            fm = mean([r[f"final_{m}"] for r in rs])
            pm = mean([r[f"prototype_{m}"] for r in rs])
            row[f"final_{m}"] = fm
            row[f"prototype_{m}"] = pm
            row[f"delta_pct_{m}"] = delta_pct(pm, fm, m)
        scope_rows.append(row)
    write_csv(out / "prototype_vs_final_aggregate.csv", scope_rows)

    manifest = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "metric_implementation": "exp/scripts/final_metrics.py collect() -- "
                                 "identical for both arms; no stale summary read",
        "sign_convention": "positive delta always means Final Prism is better",
        "provenance_tally": prov["tally"],
        "paired_conditions": usable,
        "expected_pairs": 24,
        "limitations": [
            "Historical Prototype and Final Prism ran on the same GPU model "
            "and count (A100 80GB x2) but on DIFFERENT SERVER INSTANCES. This "
            "is not a same-server paired comparison and must not be described "
            "as one.",
            "The prototype arm was not re-run on this server, so no "
            "same-server control exists for it.",
        ],
        "artifacts": ["06-aggregate/prototype_vs_final_by_seed.csv",
                      "06-aggregate/prototype_vs_final_by_condition.csv",
                      "06-aggregate/prototype_vs_final_aggregate.csv"],
    }
    (out / "PROTOTYPE_VS_FINAL_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, default=str))
    print(json.dumps(manifest, indent=2, default=str))
    return 0 if usable else 1


if __name__ == "__main__":
    sys.exit(main())
