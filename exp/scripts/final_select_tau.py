#!/usr/bin/env python3
"""Pick tau from the held-out calibration runs, by the preregistered rule.

  primary   highest mean Joint-SLO goodput over the held-out seeds
  tie (within 3% relative of the best)  fewer migrated bytes
  then                                   fewer migrations
  then                                   higher tau

Held-out seeds only. The evaluation seeds 1, 2 and 3 are never read here, and
tau is not revisited once written.
"""

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from final_metrics import collect  # noqa: E402

EVALUATION_SEEDS = {"1", "2", "3"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibration", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--summary", type=Path, required=True)
    ap.add_argument("--git-sha", required=True)
    ap.add_argument("--ci-file", type=Path, required=True)
    ap.add_argument("--expect-runs", type=int, default=0,
                    help="fail unless exactly this many calibration runs were read")
    args = ap.parse_args()

    rows, by_tau = [], {}
    excluded = []
    for tau_dir in sorted(args.calibration.glob("tau_*")):
        label = tau_dir.name[len("tau_"):]
        tau = float("inf") if label == "inf" else float(label.replace("p", "."))
        for seed_dir in sorted(tau_dir.glob("seed_*")):
            # A run whose client could not open sockets measured the client,
            # not tau. Its artifacts are kept as evidence and excluded here.
            if (seed_dir / "INVALID").exists():
                excluded.append({
                    "run": str(seed_dir),
                    "class": (seed_dir / "INVALID").read_text().strip()})
                continue
            # Preserved failed attempts are evidence, not calibration input.
            if "." in seed_dir.name:
                continue
            seed = seed_dir.name.split("_")[1]
            if seed in EVALUATION_SEEDS:
                raise SystemExit(
                    f"FATAL: {seed_dir} uses an evaluation seed; calibration "
                    "must only read held-out seeds")
            m = collect(seed_dir)
            m["tau"] = tau
            m["tau_label"] = label
            m["seed"] = seed
            rows.append(m)
            by_tau.setdefault(label, []).append(m)

    if not rows:
        raise SystemExit("FATAL: no calibration runs found")
    if args.expect_runs and len(rows) != args.expect_runs:
        raise SystemExit(f"FATAL: read {len(rows)} calibration runs, expected "
                         f"{args.expect_runs}; the grid is incomplete or "
                         "carries runs from another freeze")

    fields = sorted({k for r in rows for k in r if not isinstance(r[k], dict)})
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    with args.summary.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    agg = []
    for label, runs in by_tau.items():
        goodputs = [r["goodput_req_s"] for r in runs if r["goodput_req_s"] is not None]
        if len(goodputs) != len(runs):
            raise SystemExit(f"FATAL: tau {label} has a run with no goodput")
        agg.append({
            "tau_label": label,
            "tau": runs[0]["tau"],
            "seeds": [r["seed"] for r in runs],
            "n": len(runs),
            "mean_joint_slo_goodput": sum(goodputs) / len(goodputs),
            "migrated_bytes": sum((r["weight_bytes"] or 0) + (r["kv_bytes"] or 0)
                                  for r in runs),
            "migrations": sum(r["migrations_executed"] or 0 for r in runs),
            "mean_joint_slo_attainment": sum(
                r["joint_slo_attainment"] or 0 for r in runs) / len(runs),
        })

    best = max(a["mean_joint_slo_goodput"] for a in agg)
    tied = [a for a in agg if a["mean_joint_slo_goodput"] >= best * 0.97]
    chosen = sorted(tied, key=lambda a: (a["migrated_bytes"], a["migrations"],
                                         -a["tau"]))[0]

    doc = {
        "tau": chosen["tau"] if chosen["tau"] != float("inf") else 1e9,
        "tau_label": chosen["tau_label"],
        "selection_rule": (
            "highest mean Joint-SLO goodput over the held-out seeds; ties within "
            "3% relative broken by fewer migrated bytes, then fewer migrations, "
            "then higher tau"),
        "calibration_seeds": sorted({r["seed"] for r in rows}),
        "evaluation_seeds_untouched": sorted(EVALUATION_SEEDS),
        "candidates": sorted(agg, key=lambda a: -a["mean_joint_slo_goodput"]),
        "best_mean_goodput": best,
        "tie_window_relative": 0.03,
        "tied_candidates": [a["tau_label"] for a in tied],
        "git_sha": args.git_sha,
        "c_i_file": str(args.ci_file),
        "c_i": json.loads(args.ci_file.read_text()),
        "frozen_utc": datetime.now(timezone.utc).isoformat(),
    }
    # A selection of infinity means "never migrate", which is a claim about the
    # mechanism rather than a tuning outcome, so it is not frozen unattended.
    if chosen["tau_label"] == "inf":
        doc["status"] = "TAU_REQUIRES_HUMAN_APPROVAL"
        review = args.out.parent / "TAU_REQUIRES_HUMAN_APPROVAL.json"
        finite = [a for a in agg if a["tau_label"] != "inf"]
        doc["review"] = {
            "why_infinity_won": {
                "infinity_mean_goodput": chosen["mean_joint_slo_goodput"],
                "best_finite": max(finite, key=lambda a: a["mean_joint_slo_goodput"])
                if finite else None,
                "tie_window_relative": 0.03,
                "tied_candidates": [a["tau_label"] for a in tied],
            },
            "did_finite_tau_migrate_at_all": {
                a["tau_label"]: a["migrations"] for a in finite},
            "excluded_runs": excluded,
            "per_seed_consistency": {
                a["tau_label"]: {
                    r["seed"]: {"goodput": r["goodput_req_s"],
                                "joint_slo": r["joint_slo_attainment"],
                                "migrations": r["migrations_executed"],
                                "completed": r["completed"],
                                "aborted": r["aborted"],
                                "client_errors": r["client_errors"]}
                    for r in by_tau[a["tau_label"]]}
                for a in agg},
        }
        review.write_text(json.dumps(doc, indent=2) + "\n")
        print(json.dumps(doc["review"], indent=2)[:1800])
        print(f"\nTAU_REQUIRES_HUMAN_APPROVAL -- wrote {review}")
        print("FROZEN_TAU.json was NOT written; the chain stops here.")
        return 1

    args.out.write_text(json.dumps(doc, indent=2) + "\n")
    print(json.dumps({k: doc[k] for k in
                      ("tau", "tau_label", "best_mean_goodput",
                       "tied_candidates", "calibration_seeds")}, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    raise SystemExit(main())
