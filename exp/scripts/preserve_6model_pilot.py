#!/usr/bin/env python3
"""Retire the 6-model evaluation to pilot status -- preserved, not invalidated.

Nothing is deleted and no run is marked INVALID: every run that completed did
so cleanly and its numbers stand for what they are. What changes is the claim
attached to them. They are no longer a primary baseline; they are an
exploratory / stress pilot of a 6-model heterogeneous mix, and the primary
baseline is the 4-model paired evaluation that follows.

The raw run directories stay where they are, because the calibration, c_i,
fairness and workload manifests all reference those paths. This writes the
separate pilot record beside them and copies the small per-run artifacts into
it, so the pilot can be read on its own without walking the old tree.
"""
import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from final_metrics import collect  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-dir", type=Path,
                    default=ROOT / "exp/results/final-evaluation")
    ap.add_argument("--pilot-dir", type=Path,
                    default=ROOT / "exp/results/6model-pilot")
    args = ap.parse_args()
    raw = args.eval_dir / "05-final-c/raw"
    args.pilot_dir.mkdir(parents=True, exist_ok=True)

    runs, invalid_attempts = [], []
    for d in sorted(raw.glob("*/rate_*/seed_*")):
        name = d.name
        rec = {"run_dir": str(d.relative_to(ROOT)),
               "workload": d.parts[-3], "rate": int(d.parts[-2].split("_")[1])}
        if "." in name:                     # preserved failed attempt
            rec["seed"] = int(name.split(".")[0].split("_")[1])
            rec["status"] = "PRESERVED_ATTEMPT"
            rec["note"] = (d / "INVALID").read_text().strip() \
                if (d / "INVALID").is_file() else "attempt preserved as evidence"
            invalid_attempts.append(rec)
            continue
        rec["seed"] = int(name.split("_")[1])
        v = d / "VERIFICATION.json"
        if v.is_file():
            j = json.loads(v.read_text())
            rec["status"] = j.get("verdict", "UNKNOWN")
            rec["numbers"] = j.get("numbers")
            rec["failed_gates"] = j.get("failed_gates")
        elif list(d.glob("*_e2e_*rep.json")):
            rec["status"] = "COMPLETE_UNVERIFIED"
        else:
            continue
        try:
            rec["metrics"] = collect(d)
        except Exception:                                  # noqa: BLE001
            rec["metrics"] = None
        runs.append(rec)
        # A small, self-contained copy so the pilot reads without the old tree.
        dest = args.pilot_dir / "runs" / rec["workload"] / f"rate_{rec['rate']}" / f"seed_{rec['seed']}"
        dest.mkdir(parents=True, exist_ok=True)
        for f in ("VERIFICATION.json", "ALG2_INTERACTION.json",
                  "CLIENT_FD_VALIDATION.json", "SERVER_COMMAND.txt"):
            if (d / f).is_file():
                shutil.copy2(d / f, dest / f)

    passed = [r for r in runs if r.get("status") == "PASS"]
    manifest = {
        "what_this_is": "6-model heterogeneous exploratory / stress pilot",
        "status": "PILOT -- PRESERVED, NOT INVALIDATED, NOT A PRIMARY BASELINE",
        "why": "the primary baseline was re-frozen as a 4-model paired "
               "Prism-vs-Prototype evaluation on the same server. The 6-model "
               "runs below completed cleanly and their numbers stand; what "
               "changes is the claim attached to them, not their validity.",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "arm": "Final Prism (paper-faithful v6) only -- the released prototype "
               "arm was never run in this 6-model configuration on this server",
        "runtime_freeze": "6618671",
        "selected_tau": 0.00035,
        "hardware": "2 x NVIDIA A100-SXM4-80GB",
        "models": 6,
        "planned_runs": 24,
        "completed_runs": len(runs),
        "passed_runs": len(passed),
        "stopped_because": "graceful stop by instruction after the primary "
                           "baseline was re-frozen; no run failed",
        "correctness_of_completed_runs": {
            "verdict_PASS": len(passed),
            "alg2_order_violations_total": sum(
                (r.get("metrics") or {}).get("alg2_order_violations") or 0 for r in runs),
            "aborted_total": sum(
                (r.get("metrics") or {}).get("aborted") or 0 for r in runs),
            "client_errors_total": sum(
                (r.get("metrics") or {}).get("client_errors") or 0 for r in runs),
        },
        "runs": runs,
        "preserved_failed_attempts": invalid_attempts,
        "raw_stays_at": str(raw.relative_to(ROOT)),
        "raw_note": "the raw trees are left in place because the calibration, "
                    "c_i, fairness and workload manifests reference those paths",
    }
    (args.pilot_dir / "PILOT_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, default=str))
    print(f"pilot preserved: {len(runs)} completed run(s), {len(passed)} PASS, "
          f"{len(invalid_attempts)} preserved attempt(s) -> {args.pilot_dir}")
    for r in runs:
        n = r.get("numbers") or {}
        print(f"  {r['workload']:6s} r{r['rate']:<2d} s{r['seed']}  {r['status']:6s}  "
              f"completed={n.get('completed')}/{n.get('offered_requests')}  "
              f"goodput={n.get('joint_slo_goodput_req_s')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
