#!/usr/bin/env python3
"""Handoff state for the 4-model paired evaluation -- written on success or
failure, so the next server never reconstructs it from logs."""
import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RATES = (2, 4, 6, 8, 10)
SEEDS = (1, 2)
KINDS = ("bursty", "steady")
ARMS = ("prototype", "prism")


def sh(c):
    return subprocess.run(["bash", "-lc", c], capture_output=True, text=True,
                          cwd=ROOT).stdout.strip()


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path,
                    default=ROOT / "exp/results/4het-paired")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    out = args.out or args.out_dir / "RESUME.json"
    cfg = ROOT / "exp/configs/v4het"
    wl = ROOT / "exp/workloads/4het"

    runs, pending = [], []
    for kind in KINDS:
        for rate in RATES:
            for seed in SEEDS:
                for arm in ARMS:
                    d = args.out_dir / "raw" / arm / kind / f"rate_{rate}" / f"seed_{seed}"
                    rec = {"id": f"{arm}:{kind}_r{rate}_s{seed}", "arm": arm,
                           "workload": kind, "rate": rate, "seed": seed,
                           "run_dir": str(d.relative_to(ROOT))}
                    v = d / "VERIFICATION.json"
                    if v.is_file():
                        j = json.loads(v.read_text())
                        rec["status"] = j.get("verdict", "UNKNOWN")
                        rec["numbers"] = j.get("numbers")
                    elif list(d.glob("*_e2e_*rep.json")):
                        rec["status"] = "COMPLETE_UNVERIFIED"
                    else:
                        rec["status"] = "PENDING"
                        pending.append(rec["id"])
                    runs.append(rec)

    passed = [r for r in runs if r.get("status") == "PASS"]
    patch = ROOT / "patches/final_baseline_ready/prism_research_worktree.patch"
    man = args.out_dir / "WORKLOAD_MANIFEST.json"
    hashes = ({n: m["sha256"] for n, m in json.loads(man.read_text())["files"].items()}
              if man.is_file() else {})

    rec = {
        "what_this_is": "state of the 4-model paired Prism-vs-Prototype "
                        "evaluation on this server",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "hardware": sh("nvidia-smi --query-gpu=index,name,memory.total,"
                       "driver_version,compute_cap --format=csv,noheader"),
        "runtime": {
            "final_runtime_sha": "6618671",
            "freeze_patch_sha256": sha256_file(patch) if patch.is_file() else None,
            "verified_by": "exp/scripts/restore_frozen_runtime.sh --verify-only "
                           "re-snapshots the BUILT source and compares hashes; "
                           "checking the patch file alone is not sufficient and "
                           "let a wrongly built runtime through once",
        },
        "models": json.loads((cfg / "model_revisions.json").read_text())
        if (cfg / "model_revisions.json").is_file() else None,
        "model_config": str((cfg / "4model_2gpu.json").relative_to(ROOT)),
        "slo_base": str((cfg / "slo_base.json").relative_to(ROOT)),
        "c_i": json.loads((cfg / "prefill_speed_4het_a100.json").read_text())
        if (cfg / "prefill_speed_4het_a100.json").is_file() else None,
        "c_i_provenance": "carried over unchanged from "
                          "exp/results/final-evaluation/01-ci-profile/"
                          "prefill_speed_final_a100.json (same revisions, same "
                          "dtype, same execution path); not re-measured",
        "selected_tau": 0.00035,
        "tau_provenance": "selected using an independent prior heterogeneous "
                          "calibration setup and frozen before this evaluation",
        "workload_dir": str(wl.relative_to(ROOT)),
        "workload_hashes": hashes,
        "execution_order": "paired per condition: Prototype then Prism on the "
                           "same trace file, bursty rates 2/4/6/8/10 seeds 1/2, "
                           "then steady rates 2/4/6/8/10 seeds 1/2",
        "counts": {"PASS": len(passed), "PENDING": len(pending),
                   "OTHER": len(runs) - len(passed) - len(pending)},
        "runs": runs,
        "NEXT_RUN": pending[0] if pending else None,
        "resume_command": "bash exp/scripts/p4het_overnight.sh",
        "six_model_pilot": "exp/results/6model-pilot/PILOT_MANIFEST.json -- "
                           "preserved, not invalidated, not part of this baseline",
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, indent=2, default=str))
    print(f"wrote {out}: {len(passed)}/40 PASS, {len(pending)} pending")
    return 0


if __name__ == "__main__":
    sys.exit(main())
