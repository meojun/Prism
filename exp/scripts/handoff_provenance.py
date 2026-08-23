#!/usr/bin/env python3
"""Calibration provenance and resume state, in a form a machine can act on.

Two files:

  calibration_manifest.json  every candidate tau, every held-out run, the
                             numbers each produced and the rule that chose
                             between them. The next server does not repeat
                             calibration; it reads tau from here.

  resume_manifest.json       each of the 48 evaluation conditions as PASS,
                             PENDING or INVALID, and the exact next run.

Both are generated from the run artifacts, never from memory.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from final_metrics import collect  # noqa: E402

CAL_TAUS = [("0p00035", 0.00035), ("0p07", 0.07), ("0p10", 0.10),
            ("0p13", 0.13), ("0p171086", 0.171086), ("inf", float("inf"))]
CAL_SEEDS = [0, 42]
EVAL_GRID = ([("bursty", r, s) for r in (2, 4, 8, 14, 20) for s in (1, 2, 3)]
             + [("steady", r, s) for r in (4, 8, 20) for s in (1, 2, 3)])


def load(p, default=None):
    try:
        return json.loads(Path(p).read_text())
    except Exception:                                   # noqa: BLE001
        return default


def sha256(p):
    p = Path(p)
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def classify(d):
    """PASS, PENDING or INVALID -- and why, when it is not simply pending."""
    d = Path(d)
    if not d.is_dir():
        return "PENDING", "never run"
    if (d / "INVALID").exists():
        return "INVALID", (d / "INVALID").read_text().strip()
    rc = d / "pipeline.rc"
    if not rc.is_file():
        return "PENDING", "started but never finished"
    if rc.read_text().strip() != "0":
        return "INVALID", f"rc={rc.read_text().strip()}"
    v = load(d / "VERIFICATION.json")
    if v is None:
        return "PENDING", "finished but never verified"
    if v.get("verdict") != "PASS":
        return "INVALID", ", ".join(v.get("failed_gates", [])) or "verification failed"
    return "PASS", None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--runtime-freeze", required=True)
    args = ap.parse_args()
    ev, out = args.eval_dir, args.out_dir
    out.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------- calibration
    tau_doc = load(ev / "02-tau-calibration/FROZEN_TAU.json", {})
    runs = []
    for label, value in CAL_TAUS:
        for seed in CAL_SEEDS:
            d = ev / f"02-tau-calibration/raw/tau_{label}/seed_{seed}"
            verdict, why = classify(d)
            row = {"tau_label": label, "tau": None if value == float("inf") else value,
                   "seed": seed, "run_dir": str(d), "verdict": verdict,
                   "note": why}
            if verdict == "PASS":
                m = collect(d)
                v = load(d / "VERIFICATION.json", {})
                row.update({
                    "rc": 0,
                    "completed": m["completed"], "aborted": m["aborted"],
                    "offered": m["offered_requests"],
                    "joint_slo_goodput_req_s": m["goodput_req_s"],
                    "joint_slo_attainment": m["joint_slo_attainment"],
                    "throughput_req_s": m["achieved_throughput_req_s"],
                    "ttft_mean_s": m["ttft_mean"], "ttft_p99_s": m["ttft_p99"],
                    "tpot_mean_s": m["tpot_mean"], "tpot_p99_s": m["tpot_p99"],
                    "migrations": m["migrations_executed"],
                    "migrated_weight_bytes": m["weight_bytes"],
                    "migrated_kv_bytes": m["kv_bytes"],
                    "alg2_order_violations": m["alg2_order_violations"],
                    "correctness_verdict": v.get("verdict"),
                    "correctness_gates": v.get("gates"),
                })
            runs.append(row)

    ci = ev / "01-ci-profile/prefill_speed_final_a100.json"
    calibration = {
        "what_this_is": ("the held-out calibration that chose tau, and every "
                         "number it was chosen on -- so the choice can be "
                         "audited or reproduced without running it again"),
        "candidate_taus": [t[0] for t in CAL_TAUS],
        "candidate_tau_values": {t[0]: (None if t[1] == float("inf") else t[1])
                                 for t in CAL_TAUS},
        "held_out_seeds": CAL_SEEDS,
        "evaluation_seeds_never_read": [1, 2, 3],
        "runtime_freeze": args.runtime_freeze,
        "runs": runs,
        "runs_expected": len(CAL_TAUS) * len(CAL_SEEDS),
        "runs_passed": sum(1 for r in runs if r["verdict"] == "PASS"),
        "SELECTED_TAU": tau_doc.get("tau"),
        "selected_tau_label": tau_doc.get("tau_label"),
        "selector_rule": tau_doc.get("selection_rule"),
        "selector_output": tau_doc,
        "c_i_file": str(ci.relative_to(ci.parents[4])) if ci.is_file() else None,
        "c_i_sha256": sha256(ci),
        "raw_results": {
            "path": "exp/results/final-evaluation/02-tau-calibration/raw",
            "note": ("per-request dumps and server logs are not in git; the "
                     "derived metrics, VERIFICATION.json and "
                     "ALG2_INTERACTION.json for every run are"),
        },
    }
    (out / "calibration_manifest.json").write_text(
        json.dumps(calibration, indent=2) + "\n")

    # --------------------------------------------------------------- resume
    def arm(rel, stage):
        rows, counts = [], {"PASS": 0, "PENDING": 0, "INVALID": 0}
        for kind, rate, seed in EVAL_GRID:
            d = ev / rel / kind / f"rate_{rate}" / f"seed_{seed}"
            verdict, why = classify(d)
            counts[verdict] += 1
            rows.append({"condition": kind, "rate": rate, "seed": seed,
                         "id": f"{kind}_r{rate}_s{seed}",
                         "stage": stage, "run_dir": str(d),
                         "status": verdict, "note": why})
        return rows, counts

    proto_rows, proto_counts = arm("04b-prototype-fresh/raw", "04-prototype-fresh")
    final_rows, final_counts = arm("05-final-c/raw", "05-final-c")

    nxt = next((r for r in proto_rows if r["status"] != "PASS"), None)
    if nxt is None:
        nxt = next((r for r in final_rows if r["status"] != "PASS"), None)
    aggregate_done = (load(ev / "06-aggregate/STATUS.json", {}) or {}).get("result") == "PASS"

    resume = {
        "what_this_is": ("exactly what has run and what has not, so the next "
                         "server repeats nothing and skips nothing"),
        "runtime_freeze": args.runtime_freeze,
        "selected_tau": tau_doc.get("tau"),
        "calibration": {"status": "COMPLETE" if calibration["runs_passed"] == 12
                        else "INCOMPLETE",
                        "passed": calibration["runs_passed"], "expected": 12},
        "prototype_arm": {"stage": "04-prototype-fresh", "counts": proto_counts,
                          "conditions": proto_rows},
        "final_arm": {"stage": "05-final-c", "counts": final_counts,
                      "conditions": final_rows},
        "aggregation": {"status": "COMPLETE" if aggregate_done else "PENDING"},
        "NEXT_STAGE": nxt["stage"] if nxt else ("06-aggregate" if not aggregate_done else None),
        "NEXT_RUN": nxt["id"] if nxt else None,
        "NEXT_RUN_DIR": nxt["run_dir"] if nxt else None,
        "resume_command": "bash exp/scripts/resume_baseline.sh",
        "dry_run_command": "bash exp/scripts/resume_baseline.sh --dry-run",
        "semantics": {
            "skip": ("a run with rc=0 and a passing VERIFICATION.json is never "
                     "repeated; it is re-verified on the way past"),
            "rerun": ("a stage that passed under a different runtime freeze is "
                      "re-run, because its numbers came from different code; "
                      "c_i is exempt, being a property of the hardware"),
            "stop": ("exp/results/final-evaluation/STOP halts everything; it is "
                     "cleared by a person after reading why it is there, never "
                     "automatically"),
        },
    }
    (out / "resume_manifest.json").write_text(json.dumps(resume, indent=2) + "\n")

    print(f"calibration: {calibration['runs_passed']}/12 PASS, "
          f"tau = {calibration['SELECTED_TAU']}")
    print(f"prototype:   {proto_counts}")
    print(f"final:       {final_counts}")
    print(f"NEXT: {resume['NEXT_STAGE']} / {resume['NEXT_RUN']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
