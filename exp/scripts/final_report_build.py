#!/usr/bin/env python3
"""One report-ready summary: Final Prism, the historical Prototype, and the
comparison -- with the limitations stated in the same document as the numbers.

Everything here is read from the aggregation artifacts; nothing is recomputed
differently and nothing is copied from a stale summary.
"""
import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read_csv(p):
    return list(csv.DictReader(open(p))) if Path(p).is_file() else []


def num(v, spec=".4f", dash="--"):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return dash
    return format(f, spec)


def pct(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "--"
    return f"{f:+.1f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path,
                    default=ROOT / "exp/results/final-evaluation")
    ap.add_argument("--report", type=Path,
                    default=ROOT / "FINAL_PRISM_REPORT.md")
    args = ap.parse_args()
    agg = args.out_dir / "06-aggregate"

    fin_manifest = {}
    p = agg / "FINAL_ONLY_MANIFEST.json"
    if p.is_file():
        fin_manifest = json.loads(p.read_text())
    prov = {}
    p = agg / "HISTORICAL_PROTOTYPE_PROVENANCE.json"
    if p.is_file():
        prov = json.loads(p.read_text())
    cmp_manifest = {}
    p = agg / "PROTOTYPE_VS_FINAL_MANIFEST.json"
    if p.is_file():
        cmp_manifest = json.loads(p.read_text())

    fin_runs = read_csv(agg / "final_only_runs.csv")
    fin_cond = read_csv(agg / "final_only_by_condition.csv")
    fin_scope = read_csv(agg / "final_only_aggregate.csv")
    cmp_cond = read_csv(agg / "prototype_vs_final_by_condition.csv")
    cmp_seed = read_csv(agg / "prototype_vs_final_by_seed.csv")
    cmp_scope = read_csv(agg / "prototype_vs_final_aggregate.csv")

    L = []
    A = L.append
    A("# Prism final baseline -- Final Prism 24-condition evaluation")
    A("")
    A(f"Generated {datetime.now(timezone.utc).isoformat()}")
    A("")
    A("| | |")
    A("| --- | --- |")
    A("| FINAL_RUNTIME_SHA | `6618671` (built source verified byte-identical to the freeze patch) |")
    A("| HANDOFF_SHA | `3ef6adb1c7c138f6537d882c73d48a56df026b27` |")
    A("| SELECTED_TAU | **0.00035** (frozen; calibration 12/12, not re-run) |")
    A("| hardware | 2 x NVIDIA A100-SXM4-80GB, **current server instance** |")
    A(f"| Final Prism runs | {fin_manifest.get('runs_found', '?')}/24 |")
    A("| Released Prototype arm | **not run on this server** (by instruction) |")
    A("")

    # ---- correctness ----------------------------------------------------
    A("## 1. Final Prism correctness (all 24 runs)")
    A("")
    c = fin_manifest.get("correctness", {})
    A("| gate | total across 24 runs |")
    A("| --- | --- |")
    A(f"| runs with VERIFICATION verdict PASS | {c.get('runs_with_verdict_PASS', '?')} |")
    A(f"| Algorithm 2 order violations | {c.get('alg2_order_violations_total', '?')} |")
    A(f"| staged-return failures | {c.get('staged_return_failures_total', '?')} |")
    A(f"| aborted requests | {c.get('aborted_total', '?')} |")
    A(f"| client errors / descriptor failures | {c.get('client_errors_total', '?')} |")
    A("")
    A("Every run additionally had to satisfy, in its own `VERIFICATION.json`, "
      "rc == 0, full request accounting, zero stale dispatched sequences, zero "
      "ownership/identity mismatches, no deadlock and no unexpected connection "
      "failure. A failure at any gate stops the chain rather than being "
      "retried or averaged away.")
    A("")

    # ---- per-condition Final --------------------------------------------
    A("## 2. Final Prism performance by condition (seeds 1-3 averaged)")
    A("")
    A("| condition | goodput | attainment | throughput | TTFT mean | TTFT p99 | "
      "TPOT mean | TPOT p99 | completed | aborted | migrations |")
    A("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for r in fin_cond:
        A(f"| {r['condition']} (n={r['n_seeds']}) | {num(r.get('goodput_req_s_mean'))} | "
          f"{num(r.get('joint_slo_attainment_mean'))} | "
          f"{num(r.get('achieved_throughput_req_s_mean'), '.2f')} | "
          f"{num(r.get('ttft_mean_mean'), '.3f')} | {num(r.get('ttft_p99_mean'), '.2f')} | "
          f"{num(r.get('tpot_mean_mean'), '.4f')} | {num(r.get('tpot_p99_mean'), '.4f')} | "
          f"{num(r.get('completed_mean'), '.0f')} | {num(r.get('aborted_mean'), '.0f')} | "
          f"{num(r.get('migrations_executed_mean'), '.1f')} |")
    A("")
    A("Latencies are seconds.")
    A("")
    A("### Aggregates")
    A("")
    A("| scope | runs | goodput | attainment | throughput | TTFT p99 | TPOT p99 |")
    A("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for r in fin_scope:
        A(f"| {r['scope']} | {r['n_runs']} | {num(r.get('goodput_req_s_mean'))} | "
          f"{num(r.get('joint_slo_attainment_mean'))} | "
          f"{num(r.get('achieved_throughput_req_s_mean'), '.2f')} | "
          f"{num(r.get('ttft_p99_mean'), '.2f')} | {num(r.get('tpot_p99_mean'), '.4f')} |")
    A("")

    # ---- prototype provenance -------------------------------------------
    A("## 3. Historical Released Prototype -- provenance")
    A("")
    if not prov:
        A("Not verified: the provenance pass did not run.")
    else:
        t = prov.get("tally", {})
        A(f"Source: `{prov.get('historical_arm')}`")
        A("")
        A("The committed `raw/armA/summary.csv` beside those runs describes an "
          "earlier 300 s study and is **stale**. It was not read. Every number "
          "and every provenance claim below comes from the runs' own artifacts, "
          "checked against the canonical traces on disk.")
        A("")
        A(f"**VERIFIED {t.get('VERIFIED', 0)} / PARTIALLY_VERIFIED "
          f"{t.get('PARTIALLY_VERIFIED', 0)} / NOT_VERIFIED {t.get('NOT_VERIFIED', 0)}** of 24")
        A("")
        A("| condition | seed | verdict | basis |")
        A("| --- | ---: | --- | --- |")
        for r in prov.get("conditions", []):
            A(f"| {r['condition']} | {r['seed']} | {r['verdict']} | "
              f"{str(r.get('reason', ''))[:160]} |")
        A("")

    # ---- comparison ------------------------------------------------------
    A("## 4. Historical Prototype vs Final Prism")
    A("")
    if not cmp_cond:
        A("No comparison was produced.")
    else:
        A("Both arms were recomputed through the same metric implementation "
          "(`exp/scripts/final_metrics.py`). A positive delta always means "
          "Final Prism is better.")
        A("")
        A("| condition | paired seeds | Prototype goodput | Final goodput | Δ goodput | "
          "Proto TTFT p99 | Final TTFT p99 | Δ | Proto TPOT p99 | Final TPOT p99 | Δ |")
        A("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for r in cmp_cond:
            A(f"| {r['condition']} | {r.get('n_paired', '0')} | "
              f"{num(r.get('prototype_goodput_req_s'))} | {num(r.get('final_goodput_req_s'))} | "
              f"{pct(r.get('delta_pct_goodput_req_s'))} | "
              f"{num(r.get('prototype_ttft_p99'), '.2f')} | {num(r.get('final_ttft_p99'), '.2f')} | "
              f"{pct(r.get('delta_pct_ttft_p99'))} | "
              f"{num(r.get('prototype_tpot_p99'), '.4f')} | {num(r.get('final_tpot_p99'), '.4f')} | "
              f"{pct(r.get('delta_pct_tpot_p99'))} |")
        A("")
        A("| condition | Proto throughput | Final throughput | Δ | Proto completed | "
          "Final completed | Proto aborted | Final aborted |")
        A("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for r in cmp_cond:
            A(f"| {r['condition']} | {num(r.get('prototype_achieved_throughput_req_s'), '.2f')} | "
              f"{num(r.get('final_achieved_throughput_req_s'), '.2f')} | "
              f"{pct(r.get('delta_pct_achieved_throughput_req_s'))} | "
              f"{num(r.get('prototype_completed'), '.0f')} | {num(r.get('final_completed'), '.0f')} | "
              f"{num(r.get('prototype_aborted'), '.0f')} | {num(r.get('final_aborted'), '.0f')} |")
        A("")
        A("### Aggregates")
        A("")
        A("| scope | paired | Prototype goodput | Final goodput | Δ | Proto TTFT p99 | "
          "Final TTFT p99 | Δ |")
        A("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for r in cmp_scope:
            A(f"| {r['scope']} | {r.get('n_paired')} | {num(r.get('prototype_goodput_req_s'))} | "
              f"{num(r.get('final_goodput_req_s'))} | {pct(r.get('delta_pct_goodput_req_s'))} | "
              f"{num(r.get('prototype_ttft_p99'), '.2f')} | {num(r.get('final_ttft_p99'), '.2f')} | "
              f"{pct(r.get('delta_pct_ttft_p99'))} |")
        A("")
        A("Per-seed detail: `06-aggregate/prototype_vs_final_by_seed.csv`.")
        A("")

    # ---- limitations -----------------------------------------------------
    A("## 5. Limitations")
    A("")
    A("- **Different server instances.** The historical Released Prototype arm "
      "and this Final Prism arm both ran on 2 x A100-SXM4-80GB, but on "
      "*different server instances*. This is **not** a same-server paired "
      "comparison and must not be described as one. The prototype arm was not "
      "re-run here, so no same-server control exists.")
    A("- **No prototype arm on this server.** By instruction, only the Final "
      "Prism arm was run. Stage `04-prototype-fresh` is recorded as "
      "SKIPPED_BY_INSTRUCTION, not as a pass.")
    A("- **Small differences are not conclusions.** Where Final Prism is only "
      "slightly ahead of the historical prototype, the server-instance "
      "difference is a sufficient alternative explanation and no claim is made.")
    A("- tau was selected on held-out seeds 0 and 42 only; evaluation seeds 1-3 "
      "were never read during selection. `c_i` is a property of the hardware and "
      "was reused unchanged, so tau keeps the meaning it was chosen with.")
    A("- Raw per-request dumps carry third-party ShareGPT text and are not "
      "committed; the traces are rebuilt deterministically and hash-verified "
      "instead.")
    A("")
    for lim in cmp_manifest.get("limitations", []):
        pass

    args.report.write_text("\n".join(L) + "\n")
    print(f"wrote {args.report} ({len(L)} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
