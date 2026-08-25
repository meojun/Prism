#!/usr/bin/env python3
"""Aggregate the Final Prism arm on its own, seed 1-3 averaged per condition.

The released-prototype arm is not run on this server, so nothing here depends
on it. Every number is recomputed from the raw run records by final_metrics,
never from an earlier summary. Conditions are reported in the canonical order
and a condition short of its three seeds is reported as such rather than
averaged quietly.
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

METRICS = ["goodput_req_s", "joint_slo_attainment", "achieved_throughput_req_s",
           "ttft_mean", "ttft_p99", "tpot_mean", "tpot_p99", "e2e_mean",
           "e2e_p99", "completed", "aborted", "client_errors",
           "migrations_executed", "weight_bytes", "kv_bytes"]

GATES = ["alg2_order_violations", "staged_return_failures"]


def mean_sd(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    if not vals:
        return None, None, 0
    return (statistics.fmean(vals),
            statistics.stdev(vals) if len(vals) > 1 else 0.0, len(vals))


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
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()
    out = args.out_dir / "06-aggregate"
    out.mkdir(parents=True, exist_ok=True)
    raw = args.out_dir / "05-final-c/raw"

    run_rows, missing = [], []
    for wl, rate in CONDITIONS:
        for seed in SEEDS:
            d = raw / wl / f"rate_{rate}" / f"seed_{seed}"
            if not list(d.glob("*_e2e_*rep.json")):
                missing.append(f"{wl}_r{rate}_s{seed}")
                continue
            m = collect(d)
            m.update(arm="final-prism", workload=wl, rate=rate, seed=seed,
                     condition=f"{wl}{rate}", run_dir=str(d))
            v = d / "VERIFICATION.json"
            if v.is_file():
                rec = json.loads(v.read_text())
                m["verdict"] = rec.get("verdict")
                m["staged_return_failures"] = rec.get("numbers", {}).get(
                    "staged_return_failures")
            run_rows.append(m)

    write_csv(out / "final_only_runs.csv", run_rows)

    by = {}
    for r in run_rows:
        by.setdefault((r["workload"], r["rate"]), []).append(r)

    cond_rows = []
    for wl, rate in CONDITIONS:
        rs = by.get((wl, rate), [])
        row = {"condition": f"{wl}{rate}", "workload": wl, "rate": rate,
               "n_seeds": len(rs),
               "seeds": ",".join(str(r["seed"]) for r in sorted(
                   rs, key=lambda x: x["seed"]))}
        for k in METRICS:
            mu, sd, n = mean_sd([r.get(k) for r in rs])
            row[f"{k}_mean"] = mu
            row[f"{k}_sd"] = sd
        for g in GATES:
            vals = [r.get(g) for r in rs if isinstance(r.get(g), (int, float))]
            row[f"{g}_total"] = sum(vals) if vals else None
        cond_rows.append(row)
    write_csv(out / "final_only_by_condition.csv", cond_rows)

    scopes = {"bursty": [r for r in run_rows if r["workload"] == "bursty"],
              "steady": [r for r in run_rows if r["workload"] == "steady"],
              "overall": run_rows}
    scope_rows = []
    for scope, rs in scopes.items():
        row = {"scope": scope, "n_runs": len(rs)}
        for k in METRICS:
            mu, sd, n = mean_sd([r.get(k) for r in rs])
            row[f"{k}_mean"] = mu
            row[f"{k}_sd"] = sd
        scope_rows.append(row)
    write_csv(out / "final_only_aggregate.csv", scope_rows)

    manifest = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "arm": "Final Prism (paper-faithful v6)",
        "expected_runs": 24,
        "runs_found": len(run_rows),
        "missing_runs": missing,
        "final_complete": len(run_rows) == 24 and not missing,
        "prototype_arm": "NOT RUN ON THIS SERVER (by instruction)",
        "hardware": "2 x NVIDIA A100-SXM4-80GB (current server instance)",
        "correctness": {
            "runs_with_verdict_PASS": sum(1 for r in run_rows
                                          if r.get("verdict") == "PASS"),
            "alg2_order_violations_total": sum(
                r.get("alg2_order_violations") or 0 for r in run_rows),
            "staged_return_failures_total": sum(
                r.get("staged_return_failures") or 0 for r in run_rows),
            "aborted_total": sum(r.get("aborted") or 0 for r in run_rows),
            "client_errors_total": sum(r.get("client_errors") or 0
                                       for r in run_rows),
        },
        "artifacts": ["06-aggregate/final_only_runs.csv",
                      "06-aggregate/final_only_by_condition.csv",
                      "06-aggregate/final_only_aggregate.csv"],
    }
    (out / "FINAL_ONLY_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, default=str))
    print(json.dumps(manifest, indent=2, default=str))
    return 0 if manifest["final_complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
