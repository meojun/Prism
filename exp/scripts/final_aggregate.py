#!/usr/bin/env python3
"""Stage 6: aggregate Prototype A and Final C from raw runs, and compare.

Prototype A is 22 surviving runs plus the 2 re-run under the corrected 1 GiB
FlashInfer workspace; the originals are kept and not overwritten. Everything is
recomputed from raw records rather than from any earlier summary, which is also
how the old 23-row summary is corrected.
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

PROTO_OLD = ROOT / "exp/results/final-prototype-vs-paper-faithful/raw/armA/raw/released-prototype"
CORRECTED = {("bursty", "20", "3"), ("steady", "20", "3")}


def discover(base, arm):
    """(workload, rate, seed) -> run dir, for a raw tree laid out by the harness."""
    found = {}
    for result in Path(base).rglob("*_e2e_*rep.json"):
        run = result.parent
        parts = run.parts
        try:
            seed = parts[-1].split("_")[1]
            rate = parts[-2].split("_")[1]
            workload = parts[-3]
        except (IndexError, ValueError):
            continue
        found[(workload, rate, seed)] = run
    return {k: {"run": str(v), "arm": arm} for k, v in found.items()}


def rows_for(runs, arm):
    rows = []
    for (workload, rate, seed), meta in sorted(runs.items()):
        m = collect(meta["run"])
        m.update(arm=arm, workload=workload, rate=float(rate), seed=int(seed),
                 source=meta.get("source", "run"))
        rows.append(m)
    return rows


def write_csv(path, rows):
    if not rows:
        return
    fields = sorted({k for r in rows for k in r if not isinstance(r[k], dict)})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def summarise(rows, keys):
    out = []
    groups = {}
    for r in rows:
        groups.setdefault((r["arm"], r["workload"], r["rate"]), []).append(r)
    for (arm, workload, rate), rs in sorted(groups.items()):
        row = {"arm": arm, "workload": workload, "rate": rate, "n": len(rs),
               "seeds": ",".join(str(r["seed"]) for r in sorted(rs, key=lambda x: x["seed"]))}
        for k in keys:
            vals = [r[k] for r in rs if r.get(k) is not None]
            row[f"{k}_mean"] = statistics.fmean(vals) if vals else None
            row[f"{k}_std"] = statistics.pstdev(vals) if len(vals) > 1 else 0.0
            row[f"{k}_n"] = len(vals)
        out.append(row)
    return out


def finish(args, out, proto_rows, final_rows, decision):
    """Write every table from whichever pair of arms the fairness branch chose."""
    write_csv(out / "prototype_runs.csv", proto_rows)
    write_csv(out / "final_runs.csv", final_rows)
    write_csv(out / "all_runs.csv", proto_rows + final_rows)
    metrics = ["achieved_throughput_req_s", "goodput_req_s",
               "ttft_slo_attainment", "tpot_slo_attainment",
               "joint_slo_attainment", "ttft_p50", "ttft_p95", "ttft_p99",
               "tpot_p50", "tpot_p95", "tpot_p99", "e2e_p50", "e2e_p95",
               "e2e_p99", "completed", "aborted", "client_errors",
               "migrations_executed", "weight_bytes", "kv_bytes",
               "exposed_downtime_mean_s", "alg2_order_violations"]
    summary = summarise(proto_rows + final_rows, metrics)
    write_csv(out / "summary.csv", summary)
    write_csv(out / "latency_summary.csv",
              summarise(proto_rows + final_rows,
                        ["ttft_p50", "ttft_p95", "ttft_p99", "tpot_p50",
                         "tpot_p95", "tpot_p99", "e2e_p50", "e2e_p95", "e2e_p99"]))
    write_csv(out / "migration_summary.csv",
              summarise(final_rows,
                        ["migrations_executed", "migrations_p2p",
                         "migration_host_fallbacks", "weight_bytes", "kv_bytes",
                         "weight_gbps", "kv_gbps", "exposed_downtime_mean_s",
                         "exposed_downtime_p95_s"]))
    improvement = []
    proto_by = {(r["workload"], r["rate"]): r for r in summary if r["arm"] == "prototype"}
    for row in summary:
        if row["arm"] != "final":
            continue
        base = proto_by.get((row["workload"], row["rate"]))
        if not base:
            continue
        entry = {"workload": row["workload"], "rate": row["rate"],
                 "n_final": row["n"], "n_prototype": base["n"]}
        for k in ("goodput_req_s", "joint_slo_attainment",
                  "achieved_throughput_req_s", "ttft_p99", "tpot_p99"):
            a, b = base.get(f"{k}_mean"), row.get(f"{k}_mean")
            entry[f"prototype_{k}"] = a
            entry[f"final_{k}"] = b
            entry[f"delta_{k}"] = (b - a) if (a is not None and b is not None) else None
            entry[f"relative_{k}"] = ((b - a) / a) if (a not in (None, 0) and b is not None) else None
        improvement.append(entry)
    write_csv(out / "improvement.csv", improvement)
    manifest = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "fairness_case": decision.get("case"),
        "prototype_source": decision.get("action"),
        "prototype_runs": len(proto_rows),
        "final_runs": len(final_rows),
        "expected_prototype_runs": 24, "expected_final_runs": 24,
        "prototype_complete": len(proto_rows) == 24,
        "final_complete": len(final_rows) == 24,
        "artifacts": sorted(p.name for p in out.glob("*.csv")),
    }
    (out / "AGGREGATION_MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))
    return 0 if (manifest["final_complete"] and manifest["prototype_complete"]) else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()
    out = args.out_dir / "06-aggregate"
    out.mkdir(parents=True, exist_ok=True)

    # ---- Prototype A: 22 originals + 2 corrected ------------------------
    decision_path = args.out_dir / "FAIRNESS_DECISION.json"
    decision = json.loads(decision_path.read_text()) if decision_path.exists() else {}
    if decision.get("case") == "B":
        # The historical prototype arm is preserved but not comparable; the
        # paired comparison uses the arm re-run on the canonical workloads.
        proto = discover(args.out_dir / "04b-prototype-fresh/raw", "prototype")
        for meta in proto.values():
            meta["source"] = "fresh-on-canonical-workload"
        proto_rows = rows_for(proto, "prototype")
        final_runs = discover(args.out_dir / "05-final-c/raw", "final")
        final_rows = rows_for(final_runs, "final")
        return finish(args, out, proto_rows, final_rows, decision)

    proto = discover(PROTO_OLD, "prototype")
    for key in list(proto):
        if key in CORRECTED:
            proto.pop(key)          # superseded by the corrected run
    corrected = discover(args.out_dir / "04-prototype-correction/raw", "prototype")
    for key, meta in corrected.items():
        meta["source"] = "corrected-1GiB-workspace"
        proto[key] = meta
    proto_rows = rows_for(proto, "prototype")

    final_runs = discover(args.out_dir / "05-final-c/raw", "final")
    final_rows = rows_for(final_runs, "final")

    write_csv(out / "prototype_runs.csv", proto_rows)
    write_csv(out / "final_runs.csv", final_rows)
    write_csv(out / "all_runs.csv", proto_rows + final_rows)

    metrics = ["achieved_throughput_req_s", "goodput_req_s",
               "ttft_slo_attainment", "tpot_slo_attainment",
               "joint_slo_attainment", "ttft_p50", "ttft_p95", "ttft_p99",
               "tpot_p50", "tpot_p95", "tpot_p99", "e2e_p50", "e2e_p95",
               "e2e_p99", "completed", "aborted", "client_errors",
               "migrations_executed", "weight_bytes", "kv_bytes",
               "exposed_downtime_mean_s", "alg2_order_violations"]
    summary = summarise(proto_rows + final_rows, metrics)
    write_csv(out / "summary.csv", summary)

    latency = summarise(proto_rows + final_rows,
                        ["ttft_p50", "ttft_p95", "ttft_p99", "tpot_p50",
                         "tpot_p95", "tpot_p99", "e2e_p50", "e2e_p95", "e2e_p99"])
    write_csv(out / "latency_summary.csv", latency)
    migration = summarise(final_rows,
                          ["migrations_executed", "migrations_p2p",
                           "migration_host_fallbacks", "weight_bytes",
                           "kv_bytes", "weight_gbps", "kv_gbps",
                           "exposed_downtime_mean_s", "exposed_downtime_p95_s"])
    write_csv(out / "migration_summary.csv", migration)

    improvement = []
    proto_by = {(r["workload"], r["rate"]): r for r in summary if r["arm"] == "prototype"}
    for row in summary:
        if row["arm"] != "final":
            continue
        base = proto_by.get((row["workload"], row["rate"]))
        if not base:
            continue
        entry = {"workload": row["workload"], "rate": row["rate"],
                 "n_final": row["n"], "n_prototype": base["n"]}
        for k in ("goodput_req_s", "joint_slo_attainment",
                  "achieved_throughput_req_s", "ttft_p99", "tpot_p99"):
            a, b = base.get(f"{k}_mean"), row.get(f"{k}_mean")
            entry[f"prototype_{k}"] = a
            entry[f"final_{k}"] = b
            entry[f"delta_{k}"] = (b - a) if (a is not None and b is not None) else None
            entry[f"relative_{k}"] = ((b - a) / a) if (a not in (None, 0) and b is not None) else None
        improvement.append(entry)
    write_csv(out / "improvement.csv", improvement)

    manifest = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "prototype_runs": len(proto_rows),
        "prototype_corrected": sorted(f"{w}_r{r}_s{s}" for (w, r, s) in CORRECTED),
        "final_runs": len(final_rows),
        "expected_prototype_runs": 24,
        "expected_final_runs": 24,
        "prototype_complete": len(proto_rows) == 24,
        "final_complete": len(final_rows) == 24,
        "artifacts": sorted(p.name for p in out.glob("*.csv")),
    }
    (out / "AGGREGATION_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))
    return 0 if manifest["final_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
