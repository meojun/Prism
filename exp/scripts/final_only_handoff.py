#!/usr/bin/env python3
"""Handoff state for THIS server: what ran, what did not, and where to resume.

Written whether the sweep succeeded or failed, so the next server never has to
reconstruct the state from logs.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONDITIONS = [("bursty", 2), ("bursty", 4), ("bursty", 8), ("bursty", 14),
              ("bursty", 20), ("steady", 4), ("steady", 8), ("steady", 20)]
SEEDS = (1, 2, 3)


def sh(cmd):
    return subprocess.run(["bash", "-lc", cmd], capture_output=True,
                          text=True, cwd=ROOT).stdout.strip()


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path,
                    default=ROOT / "exp/results/final-evaluation")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "exp/final-handoff/this_server_resume.json")
    args = ap.parse_args()
    raw = args.out_dir / "05-final-c/raw"

    runs, pending = [], []
    for wl, rate in CONDITIONS:
        for seed in SEEDS:
            d = raw / wl / f"rate_{rate}" / f"seed_{seed}"
            rec = {"id": f"{wl}_r{rate}_s{seed}", "workload": wl, "rate": rate,
                   "seed": seed, "run_dir": str(d.relative_to(ROOT))}
            v = d / "VERIFICATION.json"
            if v.is_file():
                try:
                    j = json.loads(v.read_text())
                    rec["status"] = j.get("verdict", "UNKNOWN")
                    rec["numbers"] = j.get("numbers")
                except json.JSONDecodeError:
                    rec["status"] = "UNREADABLE"
            elif list(d.glob("*_e2e_*rep.json")):
                rec["status"] = "COMPLETE_UNVERIFIED"
            else:
                rec["status"] = "PENDING"
                pending.append(rec["id"])
            runs.append(rec)

    passed = [r for r in runs if r.get("status") == "PASS"]
    patch = ROOT / "patches/final_baseline_ready/prism_research_worktree.patch"
    wl_dir = ROOT / "exp/workloads/final-evaluation"
    workload_hashes = {p.name: sha256_file(p)
                       for p in sorted(wl_dir.glob("*.pkl"))}

    out = {
        "what_this_is": "the state this server ends in, so the next one repeats "
                        "nothing and skips nothing",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "server": {
            "hardware": sh("nvidia-smi --query-gpu=index,name,memory.total,"
                           "driver_version,compute_cap --format=csv,noheader"),
            "note": "same GPU model and count as the previous server instance "
                    "(A100 80GB x2), but a DIFFERENT server instance",
        },
        "runtime": {
            "final_runtime_sha": "6618671",
            "freeze_patch_sha256": sha256_file(patch) if patch.is_file() else None,
            "built_source_verified": "exp/scripts/restore_frozen_runtime.sh "
                                     "--verify-only regenerates the worktree "
                                     "patch and compares hashes",
            "known_trap": "bootstrap.sh originally applied only the "
                          "paper_faithful + paper_faithful_v3 patches, which is "
                          "an EARLIER runtime with no kvpr-global-v4 policy: the "
                          "Final arm cannot start on it, and the freeze gates "
                          "did not catch it because they hash the patch file, "
                          "not the built source.",
        },
        "selected_tau": 0.00035,
        "tau_provenance": "exp/results/final-evaluation/02-tau-calibration/"
                          "FROZEN_TAU.json; calibration 12/12, never re-run",
        "c_i_provenance": "exp/results/final-evaluation/01-ci-profile/"
                          "prefill_speed_final_a100.json; reused unchanged "
                          "because the GPUs are the same model",
        "workload_hashes": workload_hashes,
        "final_arm": {
            "stage": "05-final-c",
            "counts": {"PASS": len(passed), "PENDING": len(pending),
                       "OTHER": len(runs) - len(passed) - len(pending)},
            "runs": runs,
        },
        "prototype_arm_this_server": {
            "status": "NOT RUN BY INSTRUCTION",
            "consequence": "the prototype/final comparison is across server "
                           "instances and is not a same-server paired comparison",
        },
        "NEXT_STAGE": None if not pending else "05-final-c",
        "NEXT_RUN": pending[0] if pending else None,
        "resume_command": "bash exp/scripts/final_only_overnight.sh",
        "post_sweep_command": "bash exp/scripts/post_sweep_chain.sh",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, default=str))
    print(f"wrote {args.out}: {len(passed)}/24 PASS, {len(pending)} pending")
    return 0


if __name__ == "__main__":
    sys.exit(main())
