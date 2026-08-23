#!/usr/bin/env python3
"""Decide HANDOFF_COMPLETE, and therefore whether the server may be released.

Judged apart from whether the evaluation succeeded. They are separate
questions: a pipeline that stopped partway, handed off completely, is a correct
and safe ending. Every condition below is checked against an artifact, not
against an intention.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path


def load(p, default=None):
    try:
        return json.loads(Path(p).read_text())
    except Exception:                                   # noqa: BLE001
        return default


def sh(cmd):
    return subprocess.run(["bash", "-lc", cmd], capture_output=True,
                          text=True).stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--branch", default="exp/final-baseline-ready")
    args = ap.parse_args()
    root = args.root.resolve()
    ev, hd = root / "exp/results/final-evaluation", root / "exp/final-handoff"

    wl = load(hd / "workloads_manifest.json", {})
    wlv = load(ev / "WORKLOAD_VERIFICATION.json", {})
    dep = load(hd / "local_dependencies.json", {})
    cal = load(hd / "calibration_manifest.json", {})
    res = load(hd / "resume_manifest.json", {})
    arch = load(hd / "evidence_manifest.json", {})
    clone = load(ev / "CLEAN_CLONE_VALIDATION.json", {})
    pre = load(ev / "PREFLIGHT.json", {})
    state = load(ev / "PIPELINE_STATE.json", {})
    scan = (hd / "SECRET_SCAN.txt").read_text() if (hd / "SECRET_SCAN.txt").is_file() else ""

    local = sh(f"git -C '{root}' rev-parse HEAD")
    remote = sh(f"git ls-remote '{root and 'origin'}' refs/heads/{args.branch} "
                f"| awk '{{print $1}}'") or sh(
        f"git -C '{root}' ls-remote origin refs/heads/{args.branch} | awk '{{print $1}}'")
    tag = sh(f"git -C '{root}' ls-remote origin "
             f"'refs/tags/final-baseline-handoff^{{}}' | awk '{{print $1}}'")
    dirty = sh(f"git -C '{root}' status --porcelain "
               f"-- . ':!exp/results/final-evaluation'")

    dry = sh(f"cd '{root}' && bash exp/scripts/resume_baseline.sh --dry-run 2>&1")
    want = f"{res.get('NEXT_STAGE')} / {res.get('NEXT_RUN')}"

    conditions = {
        "workloads_archived_or_restorable": (
            wl.get("count") == 24 and not wl.get("missing")
            and (hd / "workloads_manifest.json").is_file()
            and (root / "exp/scripts/restore_workloads.sh").is_file()),
        "workload_sha_verification_pass": wlv.get("verdict") == "PASS"
        and wlv.get("checked") == 24,
        "machine_local_unknown_zero": dep.get("unclassified") == [],
        "bootstrap_usable": (root / "exp/scripts/bootstrap_final_baseline.sh").is_file(),
        "preflight_pass": pre.get("verdict") == "PASS",
        "c_i_provenance_recorded": bool(cal.get("c_i_sha256")),
        "resume_manifest_valid": (
            res.get("NEXT_RUN") is not None
            and sum(((res.get("prototype_arm") or {}).get("counts") or {}).values())
            + sum(((res.get("final_arm") or {}).get("counts") or {}).values()) == 48),
        "dry_run_resumes_at_expected_run": f"next = {want}" in dry,
        "artifact_backup_hash_verified": bool(arch.get("sha256")) and (
            sh(f"sha256sum '{arch.get('archive_absolute', '')}' 2>/dev/null "
               f"| cut -d' ' -f1") == arch.get("sha256")),
        "clean_clone_validation_pass": clone.get("verdict") == "PASS",
        "secret_scan_pass": "VERDICT: CLEAN" in scan,
        "remote_push_verified": bool(local) and local == remote,
        "handoff_tag_points_at_head": bool(tag) and tag == local,
        "worktree_clean_outside_results": dirty == "",
    }
    complete = all(conditions.values())
    doc = {
        "decided_utc": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(),
        "HANDOFF_COMPLETE": "YES" if complete else "NO",
        "SERVER_MAY_BE_RELEASED": "YES" if complete else "NO",
        "PIPELINE_STATUS": state.get("PIPELINE_STATUS"),
        "FINAL_RUNTIME_SHA": state.get("FINAL_RUNTIME_SHA"),
        "HANDOFF_SHA": local,
        "handoff_tag": "final-baseline-handoff",
        "branch": args.branch,
        "SELECTED_TAU": cal.get("SELECTED_TAU"),
        "NEXT_STAGE": res.get("NEXT_STAGE"),
        "NEXT_RUN": res.get("NEXT_RUN"),
        "conditions": conditions,
        "unmet": [k for k, v in conditions.items() if not v],
        "workload_archive": {
            "mechanism": "deterministic rebuild, verified by SHA256",
            "manifest": "exp/final-handoff/workloads_manifest.json",
            "command": "bash exp/scripts/restore_workloads.sh",
            "verified": wlv.get("verdict"),
        },
        "raw_artifact_archive": {
            "archive": arch.get("archive"),
            "sha256": arch.get("sha256"),
            "size_bytes": arch.get("size_bytes"),
            "durable_location": arch.get("durable_location"),
        },
        "next_server_command": (
            "git clone https://github.com/meojun/Prism.git prism-exp && "
            "cd prism-exp && git checkout final-baseline-handoff && "
            "./bootstrap.sh && bash exp/scripts/restore_workloads.sh && "
            "bash exp/scripts/resume_baseline.sh"),
    }
    (ev / "SAFE_TO_RELEASE.json").write_text(json.dumps(doc, indent=2) + "\n")
    for k, v in conditions.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    print(f"\nHANDOFF_COMPLETE = {doc['HANDOFF_COMPLETE']}")
    return 0 if complete else 1


if __name__ == "__main__":
    sys.exit(main())
