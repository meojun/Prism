#!/usr/bin/env python3
"""Aggregate the 4-model paired evaluation and emit report/graph-ready data.

Both arms go through the same metric implementation (final_metrics.collect).
A condition is only paired when BOTH arms provably consumed the same trace
file: each run's own bench.log records the trace it loaded, that file is
hashed, and the two hashes must agree with each other and with the frozen
workload manifest. A condition that cannot be paired that way is reported as
unpaired rather than quietly averaged.

n = 2 seeds. The per-seed values are kept alongside every mean; nothing here
computes a p-value, because two seeds do not support one.
"""
import argparse
import csv
import hashlib
import json
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from final_metrics import collect  # noqa: E402

RATES = (2, 4, 6, 8, 10)
SEEDS = (1, 2)
KINDS = ("bursty", "steady")
ARMS = ("prototype", "prism")

HIGHER_BETTER = ("goodput_req_s", "joint_slo_attainment",
                 "achieved_throughput_req_s", "completed")
LOWER_BETTER = ("ttft_p50", "ttft_p95", "ttft_p99", "tpot_p50", "tpot_p95",
                "tpot_p99", "e2e_p50", "e2e_p95", "e2e_p99", "ttft_mean",
                "tpot_mean", "e2e_mean", "aborted", "client_errors")
METRICS = HIGHER_BETTER + LOWER_BETTER


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def trace_of(run):
    """The trace this run actually loaded, from its own client log."""
    bench = Path(run) / "server-logs/bench.log"
    try:
        with open(bench, errors="replace") as f:
            for _ in range(6):
                m = re.search(r"Real trace file:\s*(\S+)", next(f))
                if m:
                    return m.group(1)
    except (OSError, StopIteration):
        pass
    return None


def delta_pct(proto, final, metric):
    if not isinstance(proto, (int, float)) or not isinstance(final, (int, float)):
        return None
    if proto == 0:
        return None
    if metric in HIGHER_BETTER:
        return (final - proto) / proto * 100.0
    return (proto - final) / proto * 100.0


def mean(v):
    v = [x for x in v if isinstance(x, (int, float))]
    return statistics.fmean(v) if v else None


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
                    default=ROOT / "exp/results/4het-paired")
    args = ap.parse_args()
    out = args.out_dir / "aggregate"
    out.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((args.out_dir / "WORKLOAD_MANIFEST.json").read_text())
    want_sha = {n: m["sha256"] for n, m in manifest["files"].items()}

    seed_rows, pair_problems = [], []
    for kind in KINDS:
        for rate in RATES:
            for seed in SEEDS:
                name = f"{kind}_r{rate}_s{seed}.pkl"
                row = {"workload": kind, "rate": rate, "seed": seed,
                       "condition": f"{kind}{rate}", "trace": name,
                       "expected_sha256": want_sha.get(name)}
                shas, got = {}, {}
                for arm in ARMS:
                    d = args.out_dir / "raw" / arm / kind / f"rate_{rate}" / f"seed_{seed}"
                    if not list(d.glob("*_e2e_*rep.json")):
                        got[arm] = None
                        continue
                    got[arm] = collect(d)
                    t = trace_of(d)
                    shas[arm] = (sha256_file(t) if t and Path(t).is_file()
                                 else None)
                    row[f"{arm}_trace_path"] = t
                    row[f"{arm}_trace_sha256"] = shas.get(arm)
                paired = (got["prototype"] is not None and got["prism"] is not None
                          and shas.get("prototype") is not None
                          and shas.get("prototype") == shas.get("prism")
                          == row["expected_sha256"])
                row["paired"] = paired
                if not paired:
                    why = []
                    for arm in ARMS:
                        if got[arm] is None:
                            why.append(f"{arm} run missing")
                        elif shas.get(arm) != row["expected_sha256"]:
                            why.append(f"{arm} trace sha {str(shas.get(arm))[:12]} "
                                       f"!= frozen {str(row['expected_sha256'])[:12]}")
                    row["unpaired_reason"] = "; ".join(why) or "unknown"
                    pair_problems.append(f"{kind}_r{rate}_s{seed}: {row['unpaired_reason']}")
                for arm in ARMS:
                    for m in METRICS:
                        row[f"{arm}_{m}"] = got[arm].get(m) if got[arm] else None
                for m in ("migrations_executed", "weight_bytes", "kv_bytes"):
                    row[f"prism_{m}"] = got["prism"].get(m) if got["prism"] else None
                for m in METRICS:
                    row[f"delta_pct_{m}"] = delta_pct(
                        row.get(f"prototype_{m}"), row.get(f"prism_{m}"), m) \
                        if paired else None
                seed_rows.append(row)
    write_csv(out / "by_seed.csv", seed_rows)

    # ---- per condition: seeds averaged, per-seed values kept beside them ----
    cond_rows = []
    for kind in KINDS:
        for rate in RATES:
            rs = [r for r in seed_rows if r["workload"] == kind and r["rate"] == rate]
            pr = [r for r in rs if r["paired"]]
            row = {"workload": kind, "rate": rate, "condition": f"{kind}{rate}",
                   "n_seeds": len(rs), "n_paired": len(pr),
                   "paired_seeds": ",".join(str(r["seed"]) for r in pr)}
            for m in METRICS + ("migrations_executed", "weight_bytes", "kv_bytes"):
                for arm in ARMS:
                    key = f"{arm}_{m}"
                    if key in rs[0]:
                        vals = [r.get(key) for r in pr]
                        row[key] = mean(vals)
                        for r in pr:
                            row[f"{key}_s{r['seed']}"] = r.get(key)
            for m in METRICS:
                row[f"delta_pct_{m}"] = delta_pct(row.get(f"prototype_{m}"),
                                                  row.get(f"prism_{m}"), m)
            cond_rows.append(row)
    write_csv(out / "by_condition.csv", cond_rows)

    # ---- bursty / steady / overall ----------------------------------------
    scope_rows = []
    for scope, sel in (("bursty", lambda r: r["workload"] == "bursty"),
                       ("steady", lambda r: r["workload"] == "steady"),
                       ("overall", lambda r: True)):
        pr = [r for r in seed_rows if sel(r) and r["paired"]]
        row = {"scope": scope, "n_paired_runs": len(pr)}
        for m in METRICS:
            for arm in ARMS:
                row[f"{arm}_{m}"] = mean([r.get(f"{arm}_{m}") for r in pr])
            row[f"delta_pct_{m}"] = delta_pct(row.get(f"prototype_{m}"),
                                              row.get(f"prism_{m}"), m)
        row["prism_migrations_executed"] = mean(
            [r.get("prism_migrations_executed") for r in pr])
        scope_rows.append(row)
    write_csv(out / "aggregate.csv", scope_rows)

    # ---- graph-ready long form (A: goodput, B: attainment, C/D: tails) -----
    graphs = {"A": "goodput_req_s", "B": "joint_slo_attainment",
              "C": "ttft_p99", "D": "tpot_p99"}
    for g, metric in graphs.items():
        rows = []
        for kind in KINDS:
            for rate in RATES:
                rs = [r for r in seed_rows
                      if r["workload"] == kind and r["rate"] == rate and r["paired"]]
                for arm in ARMS:
                    vals = [r.get(f"{arm}_{metric}") for r in rs]
                    vals = [v for v in vals if isinstance(v, (int, float))]
                    rows.append({
                        "workload": kind, "rate": rate, "arm": arm,
                        "metric": metric,
                        "mean": mean(vals),
                        "min": min(vals) if vals else None,
                        "max": max(vals) if vals else None,
                        "n": len(vals),
                        **{f"seed{r['seed']}": r.get(f"{arm}_{metric}") for r in rs},
                    })
        write_csv(out / f"graph_{g}_{metric}.csv", rows)
        (out / f"graph_{g}_{metric}.json").write_text(
            json.dumps(rows, indent=2, default=str))

    # ---- what the numbers say, stated plainly -----------------------------
    findings = []
    for kind in KINDS:
        wins = [r for r in cond_rows if r["workload"] == kind
                and isinstance(r.get("delta_pct_goodput_req_s"), (int, float))
                and r["delta_pct_goodput_req_s"] > 0]
        losses = [r for r in cond_rows if r["workload"] == kind
                  and isinstance(r.get("delta_pct_goodput_req_s"), (int, float))
                  and r["delta_pct_goodput_req_s"] <= 0]
        findings.append({
            "scope": kind,
            "rates_where_prism_leads_on_goodput": [r["rate"] for r in wins],
            "rates_where_prism_does_not": [r["rate"] for r in losses],
        })
    # Saturation: the rate past which achieved throughput stops tracking offered.
    sat = {}
    for kind in KINDS:
        for arm in ARMS:
            point = None
            for rate in RATES:
                r = next((c for c in cond_rows if c["workload"] == kind
                          and c["rate"] == rate), None)
                th = r.get(f"{arm}_achieved_throughput_req_s") if r else None
                if isinstance(th, (int, float)) and th < 0.9 * rate:
                    point = rate
                    break
            sat[f"{kind}_{arm}"] = point
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "design": "4 heterogeneous models, 2 x A100-80GB, same server, paired "
                  "per condition on byte-identical traces; Prototype then Prism",
        "seeds_per_condition": len(SEEDS),
        "statistical_note": "n=2 seeds per condition. Per-seed values are kept "
                            "beside every mean. No significance is claimed and "
                            "none should be read into these differences.",
        "paired_conditions": sum(1 for r in seed_rows if r["paired"]),
        "expected_pairs": len(seed_rows),
        "pair_problems": pair_problems,
        "goodput_findings": findings,
        "first_rate_below_90pct_of_offered": sat,
        "tau": 0.00035,
        "tau_provenance": "selected using an independent prior heterogeneous "
                          "calibration setup and frozen before this evaluation; "
                          "not re-calibrated and not adjusted after any result",
        "artifacts": sorted(p.name for p in out.iterdir()),
    }
    (out / "SUMMARY.json").write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps(summary, indent=2, default=str))
    return 0 if summary["paired_conditions"] == summary["expected_pairs"] else 1


if __name__ == "__main__":
    sys.exit(main())
