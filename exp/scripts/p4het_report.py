#!/usr/bin/env python3
"""Report-ready summary of the 4-model paired evaluation."""
import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RATES = (2, 4, 6, 8, 10)


def read_csv(p):
    return list(csv.DictReader(open(p))) if Path(p).is_file() else []


def n(v, spec=".4f"):
    try:
        return format(float(v), spec)
    except (TypeError, ValueError):
        return "--"


def pct(v):
    try:
        return f"{float(v):+.1f}%"
    except (TypeError, ValueError):
        return "--"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path,
                    default=ROOT / "exp/results/4het-paired")
    ap.add_argument("--report", type=Path, default=ROOT / "reports/prism/01_initial_4het/P4HET_REPORT.md")
    args = ap.parse_args()
    agg = args.out_dir / "aggregate"
    summary = json.loads((agg / "SUMMARY.json").read_text()) \
        if (agg / "SUMMARY.json").is_file() else {}
    wl = json.loads((args.out_dir / "WORKLOAD_MANIFEST.json").read_text()) \
        if (args.out_dir / "WORKLOAD_MANIFEST.json").is_file() else {}
    cond = read_csv(agg / "by_condition.csv")
    seed = read_csv(agg / "by_seed.csv")
    scope = read_csv(agg / "aggregate.csv")

    L = []
    A = L.append
    A("# Prism vs Released Prototype -- 4-model paired evaluation")
    A("")
    A(f"Generated {datetime.now(timezone.utc).isoformat()}")
    A("")
    A("| | |")
    A("| --- | --- |")
    A("| design | paired per condition on byte-identical traces, Prototype then Prism |")
    A("| hardware | 2 x NVIDIA A100-SXM4-80GB, **one server, both arms** |")
    A("| runtime | `6618671`, built source verified byte-identical to the freeze |")
    A("| tau | **0.00035**, frozen before this evaluation |")
    A("| models | Llama-3.2-3B, Qwen2.5-3B-Instruct, Llama-3.1-8B, Qwen2.5-7B-Instruct |")
    A(f"| paired conditions | {summary.get('paired_conditions', '?')}"
      f"/{summary.get('expected_pairs', '?')} |")
    A(f"| seeds per condition | {summary.get('seeds_per_condition', '?')} |")
    A("")
    A("Both arms consumed the same trace **file**, not merely the same seed: "
      "each run's own client log records the trace it loaded, that file is "
      "hashed at aggregation time, and a condition is only paired when both "
      "hashes agree with each other and with the frozen workload manifest.")
    A("")
    A("> **n = 2 seeds per condition.** Per-seed values are kept beside every "
      "mean below and in `aggregate/by_seed.csv`. No statistical significance "
      "is claimed and none should be read into these differences.")
    A("")

    for kind in ("bursty", "steady"):
        A(f"## {kind.capitalize()}")
        A("")
        A("| rate | Proto goodput | Prism goodput | Δ% | Proto attain | Prism attain | "
          "Proto TTFT p99 | Prism TTFT p99 | Proto TPOT p99 | Prism TPOT p99 |")
        A("| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for rate in RATES:
            r = next((c for c in cond if c["workload"] == kind
                      and int(c["rate"]) == rate), None)
            if not r:
                continue
            A(f"| {rate} | {n(r.get('prototype_goodput_req_s'))} | "
              f"{n(r.get('prism_goodput_req_s'))} | "
              f"{pct(r.get('delta_pct_goodput_req_s'))} | "
              f"{n(r.get('prototype_joint_slo_attainment'))} | "
              f"{n(r.get('prism_joint_slo_attainment'))} | "
              f"{n(r.get('prototype_ttft_p99'), '.2f')} | "
              f"{n(r.get('prism_ttft_p99'), '.2f')} | "
              f"{n(r.get('prototype_tpot_p99'))} | {n(r.get('prism_tpot_p99'))} |")
        A("")
        A("| rate | Proto thr | Prism thr | Δ% | Proto compl | Prism compl | "
          "Proto abort | Prism abort | Prism migrations |")
        A("| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for rate in RATES:
            r = next((c for c in cond if c["workload"] == kind
                      and int(c["rate"]) == rate), None)
            if not r:
                continue
            A(f"| {rate} | {n(r.get('prototype_achieved_throughput_req_s'), '.2f')} | "
              f"{n(r.get('prism_achieved_throughput_req_s'), '.2f')} | "
              f"{pct(r.get('delta_pct_achieved_throughput_req_s'))} | "
              f"{n(r.get('prototype_completed'), '.0f')} | "
              f"{n(r.get('prism_completed'), '.0f')} | "
              f"{n(r.get('prototype_aborted'), '.0f')} | "
              f"{n(r.get('prism_aborted'), '.0f')} | "
              f"{n(r.get('prism_migrations_executed'), '.1f')} |")
        A("")

    A("## Aggregates")
    A("")
    A("| scope | paired runs | Proto goodput | Prism goodput | Δ% | "
      "Proto TTFT p99 | Prism TTFT p99 | Δ% | Proto TPOT p99 | Prism TPOT p99 | Δ% |")
    A("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for r in scope:
        A(f"| {r['scope']} | {r.get('n_paired_runs')} | "
          f"{n(r.get('prototype_goodput_req_s'))} | {n(r.get('prism_goodput_req_s'))} | "
          f"{pct(r.get('delta_pct_goodput_req_s'))} | "
          f"{n(r.get('prototype_ttft_p99'), '.2f')} | {n(r.get('prism_ttft_p99'), '.2f')} | "
          f"{pct(r.get('delta_pct_ttft_p99'))} | "
          f"{n(r.get('prototype_tpot_p99'))} | {n(r.get('prism_tpot_p99'))} | "
          f"{pct(r.get('delta_pct_tpot_p99'))} |")
    A("")

    A("## Per-seed values")
    A("")
    A("| workload | rate | seed | Proto goodput | Prism goodput | "
      "Proto TTFT p99 | Prism TTFT p99 | paired |")
    A("| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
    for r in seed:
        A(f"| {r['workload']} | {r['rate']} | {r['seed']} | "
          f"{n(r.get('prototype_goodput_req_s'))} | {n(r.get('prism_goodput_req_s'))} | "
          f"{n(r.get('prototype_ttft_p99'), '.2f')} | {n(r.get('prism_ttft_p99'), '.2f')} | "
          f"{r.get('paired')} |")
    A("")

    A("## What the numbers say")
    A("")
    for f in summary.get("goodput_findings", []):
        lead = f.get("rates_where_prism_leads_on_goodput") or []
        no = f.get("rates_where_prism_does_not") or []
        A(f"- **{f['scope']}**: Prism leads on Joint-SLO goodput at rates "
          f"{lead or 'none'}; it does not at {no or 'none'}.")
    sat = summary.get("first_rate_below_90pct_of_offered", {})
    if sat:
        A(f"- First offered rate at which achieved throughput falls below 90% of "
          f"offered (a saturation marker): {sat}.")
    A("")

    A("## Workloads")
    A("")
    A("| trace | requests | span (s) | mean rate | peak 10 s rate | sha256 |")
    A("| --- | ---: | ---: | ---: | ---: | --- |")
    for name in sorted(wl.get("files", {})):
        d = wl["files"][name]
        A(f"| {name} | {d['requests']} | {d['arrival_span_s']:.1f} | "
          f"{d['achieved_mean_arrival_rate_req_s']:.2f} | "
          f"{d['peak_10s_arrival_rate_req_s']:.2f} | `{d['sha256'][:16]}` |")
    A("")

    A("## Limitations")
    A("")
    A("- **n = 2 seeds per condition.** Enough to show a direction, not enough "
      "for a significance claim. Per-seed values are published above.")
    A("- tau was selected on an independent prior heterogeneous calibration "
      "setup and frozen before this evaluation. It was not re-calibrated for "
      "the 4-model mix, and it was not adjusted after seeing any result here.")
    A("- `c_i` is reused from the A100 profile of the same model revisions at "
      "the same precision on the same execution path. It was not re-measured.")
    A("- A separate 6-model heterogeneous run exists as an exploratory / stress "
      "pilot. It is not part of this baseline and its numbers are not mixed in.")
    if summary.get("pair_problems"):
        A("- Unpaired conditions were excluded from the comparison: "
          + "; ".join(summary["pair_problems"]))
    A("")

    args.report.write_text("\n".join(L) + "\n")
    print(f"wrote {args.report} ({len(L)} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
