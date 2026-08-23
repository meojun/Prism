#!/usr/bin/env python3
"""Where the pipeline actually got to, and exactly how to resume it elsewhere.

Written so that the next server needs nothing but this file and the repository.
It reads the stage records, the raw run directories and the stop markers, and
reports:

  PIPELINE_STATUS   SUCCESS | FAILED | STOPPED | APPROVAL_REQUIRED | INCOMPLETE
  what completed    per stage, and per run inside the two 24-run arms
  what remains      the exact conditions still to run
  the resume point  the stage and run to start from, and the command for it

A failure is recorded as a failure. Nothing here rounds a stopped chain up to a
successful one.
"""
import argparse
import json
import sys
from pathlib import Path

STAGES = ["00-preflight", "01-ci-profile", "02-tau-calibration", "03-readiness",
          "03b-fairness", "04-prototype-fresh", "05-final-c", "06-aggregate"]

CAL_GRID = [(t, s) for t in ("0p00035", "0p07", "0p10", "0p13", "0p171086", "inf")
            for s in (0, 42)]
EVAL_GRID = ([("bursty", r, s) for r in (2, 4, 8, 14, 20) for s in (1, 2, 3)]
             + [("steady", r, s) for r in (4, 8, 20) for s in (1, 2, 3)])


def load(p, default=None):
    try:
        return json.loads(Path(p).read_text())
    except Exception:                                   # noqa: BLE001
        return default


def run_ok(d):
    """A run counts only if it finished and passed its own verification."""
    d = Path(d)
    if not d.is_dir():
        return False
    rc = (d / "pipeline.rc")
    if not rc.is_file() or rc.read_text().strip() != "0":
        return False
    v = load(d / "VERIFICATION.json")
    if v is not None:
        return v.get("verdict") == "PASS"
    # Older runs predate the per-run record; fall back to the harness result.
    return not (d / "INVALID").exists()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--runtime-freeze", required=True)
    args = ap.parse_args()
    ev = args.eval_dir

    stages = {}
    for s in STAGES:
        rec = load(ev / s / "STATUS.json") or {}
        stages[s] = {"result": rec.get("result"), "git_sha": rec.get("git_sha"),
                     "reason": rec.get("reason"),
                     "finished_at": rec.get("finished_at")}

    cal_done, cal_todo = [], []
    for tau, seed in CAL_GRID:
        d = ev / f"02-tau-calibration/raw/tau_{tau}/seed_{seed}"
        (cal_done if run_ok(d) else cal_todo).append(f"tau={tau} seed={seed}")

    def arm(rel):
        done, todo = [], []
        for kind, rate, seed in EVAL_GRID:
            d = ev / rel / kind / f"rate_{rate}" / f"seed_{seed}"
            (done if run_ok(d) else todo).append(f"{kind} r{rate} s{seed}")
        return done, todo

    proto_done, proto_todo = arm("04b-prototype-fresh/raw")
    final_done, final_todo = arm("05-final-c/raw")

    stop = (ev / "STOP").read_text().strip() if (ev / "STOP").is_file() else None
    approval = (ev / "02-tau-calibration/TAU_REQUIRES_HUMAN_APPROVAL.json").is_file()
    tau_doc = load(ev / "02-tau-calibration/FROZEN_TAU.json") or {}
    failed_stages = [s for s, r in stages.items() if r["result"] == "FAIL"]

    if stages["06-aggregate"]["result"] == "PASS":
        status = "SUCCESS"
    elif approval:
        status = "APPROVAL_REQUIRED"
    elif stop:
        status = "STOPPED"
    elif failed_stages:
        status = "FAILED"
    else:
        status = "INCOMPLETE"

    # The stage to resume from is the first one that has not passed.
    resume_stage = next((s for s in STAGES if stages[s]["result"] != "PASS"), None)
    if resume_stage == "02-tau-calibration":
        resume_run = cal_todo[0] if cal_todo else "tau selection"
    elif resume_stage == "04-prototype-fresh":
        resume_run = proto_todo[0] if proto_todo else "arm complete"
    elif resume_stage == "05-final-c":
        resume_run = final_todo[0] if final_todo else "arm complete"
    else:
        resume_run = None

    doc = {
        "PIPELINE_STATUS": status,
        "FINAL_RUNTIME_SHA": args.runtime_freeze,
        "stop_reason": stop,
        "failed_stages": failed_stages,
        "stages": stages,
        "selected_tau": tau_doc.get("tau"),
        "tau_frozen": bool(tau_doc),
        "calibration": {"expected": len(CAL_GRID), "complete": len(cal_done),
                        "done": cal_done, "remaining": cal_todo},
        "prototype_arm": {"expected": len(EVAL_GRID), "complete": len(proto_done),
                          "done": proto_done, "remaining": proto_todo},
        "final_arm": {"expected": len(EVAL_GRID), "complete": len(final_done),
                      "done": final_done, "remaining": final_todo},
        "resume": {
            "stage": resume_stage,
            "run": resume_run,
            "command": ("bash exp/scripts/final_overnight.sh"
                        if resume_stage else None),
            "note": ("the chain is resumable: a stage that passed under this "
                     "runtime freeze is skipped, and a run with rc=0 and a "
                     "passing VERIFICATION.json is not repeated. Clear "
                     "exp/results/final-evaluation/STOP first, deliberately, "
                     "after reading why it is there."),
        },
        "invalidated": {
            "what": ("runs recorded under an earlier runtime freeze, and every "
                     "directory whose name carries a suffix after the seed "
                     "(seed_0.staged-return-defect1, seed_0.watchdog-abort1, "
                     "raw.pre-freeze-*)"),
            "why": ("the runtime changed, so their numbers are not comparable "
                    "with runs on this freeze; they are kept as evidence and "
                    "the tau selector refuses to read them"),
        },
    }
    args.out.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"PIPELINE_STATUS = {status}")
    print(f"  calibration {len(cal_done)}/{len(CAL_GRID)}, "
          f"prototype {len(proto_done)}/{len(EVAL_GRID)}, "
          f"final {len(final_done)}/{len(EVAL_GRID)}")
    print(f"  resume at: {resume_stage} / {resume_run}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
