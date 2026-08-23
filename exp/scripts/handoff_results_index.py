#!/usr/bin/env python3
"""CURRENT_RESULTS_INDEX.md -- every result and artifact, in one place.

Written for the state the work is actually in, which right now is partway
through. It says what exists, what does not, where each thing lives and what it
hashes to, so a person on another machine can find any number and check that
the copy they have is the one that was produced here.
"""
import argparse
import json
import sys
from pathlib import Path


def load(p, default=None):
    try:
        return json.loads(Path(p).read_text())
    except Exception:                                   # noqa: BLE001
        return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--handoff-sha", default="(stamped at push)")
    args = ap.parse_args()
    root = args.root.resolve()
    ev = root / "exp/results/final-evaluation"
    hd = root / "exp/final-handoff"

    cal = load(hd / "calibration_manifest.json", {})
    res = load(hd / "resume_manifest.json", {})
    wl = load(hd / "workloads_manifest.json", {})
    arch = load(hd / "evidence_manifest.json", {})
    man = load(root / "exp/FINAL_BASELINE_MANIFEST.json", {})
    state = load(ev / "PIPELINE_STATE.json", {})

    L, A = [], None
    A = L.append
    A("# Current results index")
    A("")
    A("Where every number and artifact is, as of this handoff. The comparison "
      "is not finished; this says exactly how far it got.")
    A("")
    A("| | |")
    A("| --- | --- |")
    A(f"| PIPELINE_STATUS | **{state.get('PIPELINE_STATUS', 'UNKNOWN')}** |")
    A(f"| FINAL_RUNTIME_SHA | `{man.get('final_runtime_sha')}` |")
    A(f"| HANDOFF_SHA | `{args.handoff_sha}` |")
    A(f"| SELECTED_TAU | **{cal.get('SELECTED_TAU')}** |")
    A(f"| calibration | {cal.get('runs_passed')}/12 PASS |")
    pc = (res.get("prototype_arm") or {}).get("counts", {})
    fc = (res.get("final_arm") or {}).get("counts", {})
    A(f"| prototype arm | {pc.get('PASS', 0)}/24 PASS, {pc.get('PENDING', 0)} pending |")
    A(f"| final arm | {fc.get('PASS', 0)}/24 PASS, {fc.get('PENDING', 0)} pending |")
    A(f"| aggregation | {(res.get('aggregation') or {}).get('status')} |")
    A(f"| next run | `{res.get('NEXT_STAGE')}` / `{res.get('NEXT_RUN')}` |")
    A("")

    A("## Calibration -- how tau was chosen")
    A("")
    A("Six candidate tau on held-out seeds 0 and 42; evaluation seeds 1-3 were "
      "never read. Selection: highest mean Joint-SLO goodput, ties within 3% "
      "relative broken by fewer migrated bytes, then fewer migrations, then "
      "higher tau.")
    A("")
    sel = cal.get("selector_output") or {}
    cands = sel.get("candidates") or []
    if cands:
        A("| tau | mean Joint-SLO goodput | mean attainment | migrations | migrated bytes |")
        A("| --- | --- | --- | --- | --- |")
        for c in cands:
            mark = " **<- selected**" if c["tau_label"] == cal.get("selected_tau_label") else ""
            A(f"| {c['tau_label']}{mark} | {c['mean_joint_slo_goodput']:.4f} | "
              f"{c['mean_joint_slo_attainment']:.4f} | {c['migrations']} | "
              f"{c['migrated_bytes']:,} |")
        A("")
        A(f"Tied within 3%: {sel.get('tied_candidates')} -- "
          f"{'no tie-break was needed' if len(sel.get('tied_candidates') or []) < 2 else 'broken by the rule above'}.")
        A("")

    A("### Every calibration run")
    A("")
    A("| tau | seed | verdict | completed/offered | goodput | attainment | migrations | TTFT p99 |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in cal.get("runs", []):
        if r["verdict"] != "PASS":
            A(f"| {r['tau_label']} | {r['seed']} | {r['verdict']} | - | - | - | - | - |")
            continue
        A(f"| {r['tau_label']} | {r['seed']} | {r['verdict']} | "
          f"{r['completed']}/{r['offered']} | {r['joint_slo_goodput_req_s']:.4f} | "
          f"{r['joint_slo_attainment']:.4f} | {r['migrations']} | "
          f"{r['ttft_p99_s']:.1f} s |")
    A("")
    A("Full detail, including every correctness gate per run: "
      "`exp/final-handoff/calibration_manifest.json`.")
    A("")

    A("## Evaluation arms")
    A("")
    A("Both arms run fresh on the same 24 canonical workloads. The prototype "
      "arm has one condition done.")
    A("")
    A("| stage | PASS | PENDING | INVALID |")
    A("| --- | --- | --- | --- |")
    A(f"| 04-prototype-fresh | {pc.get('PASS', 0)} | {pc.get('PENDING', 0)} | {pc.get('INVALID', 0)} |")
    A(f"| 05-final-c | {fc.get('PASS', 0)} | {fc.get('PENDING', 0)} | {fc.get('INVALID', 0)} |")
    A("")
    done = [c for c in (res.get("prototype_arm") or {}).get("conditions", [])
            if c["status"] == "PASS"]
    if done:
        A("Completed prototype runs:")
        A("")
        for c in done:
            v = load(Path(c["run_dir"]) / "VERIFICATION.json", {})
            n = v.get("numbers", {})
            A(f"- `{c['id']}` -- {n.get('completed')}/{n.get('offered_requests')} "
              f"completed, {n.get('aborted')} aborted, throughput "
              f"{n.get('throughput_req_s', 0):.2f} req/s, verdict {v.get('verdict')}")
        A("")
    A("Per-condition status: `exp/final-handoff/resume_manifest.json`.")
    A("")

    A("## Workloads")
    A("")
    A(f"{wl.get('count')} canonical traces, {wl.get('conditions')}. They are "
      "not distributed -- they carry raw ShareGPT prompt text, some of which "
      "contains real leaked credentials. They are rebuilt instead, and the "
      "rebuild is byte-identical:")
    A("")
    A("```bash")
    A("bash exp/scripts/restore_workloads.sh    # rebuild, then verify all 24 SHA256")
    A("```")
    A("")
    A("Digests and the exact recipe: `exp/final-handoff/workloads_manifest.json`. "
      f"Source dataset: `{(wl.get('sharegpt_source') or {}).get('huggingface_dataset')}` "
      f"(ungated), sha256 `{(wl.get('sharegpt_source') or {}).get('sha256', '')[:16]}...`.")
    A("")

    A("## Evidence archive")
    A("")
    if arch:
        A(f"- `{arch.get('archive')}`")
        A(f"- sha256 `{arch.get('sha256')}`")
        A(f"- {arch.get('size_bytes', 0) / 1e6:.1f} MB")
        A(f"- durable location: {arch.get('durable_location')}")
        A("")
        A("Contains: " + "; ".join(arch.get("contains", [])) + ".")
        A("")
        ex = arch.get("excluded") or {}
        A(f"**Not included:** {ex.get('what')} ({ex.get('count')} files, "
          f"{ex.get('size')}). {ex.get('why')} {ex.get('impact')}")
    else:
        A("_no evidence archive recorded_")
    A("")

    A("## Artifact map")
    A("")
    A("| what | where |")
    A("| --- | --- |")
    A("| baseline manifest | `exp/FINAL_BASELINE_MANIFEST.json` |")
    A("| calibration provenance | `exp/final-handoff/calibration_manifest.json` |")
    A("| resume state | `exp/final-handoff/resume_manifest.json` |")
    A("| workload digests + recipe | `exp/final-handoff/workloads_manifest.json` |")
    A("| machine-local dependencies | `exp/HANDOFF_LOCAL_DEPENDENCIES.md` |")
    A("| evidence archive + hash | `exp/final-handoff/evidence_manifest.json` |")
    A("| c_i profile | `exp/results/final-evaluation/01-ci-profile/` |")
    A("| tau selector output | `exp/results/final-evaluation/02-tau-calibration/FROZEN_TAU.json` |")
    A("| per-run verification | `<run>/VERIFICATION.json` |")
    A("| Algorithm 2 interaction | `<run>/ALG2_INTERACTION.json` |")
    A("| how to continue | `FINAL_BASELINE_HANDOFF.md` |")
    A("")

    A("## Correctness findings this run produced")
    A("")
    A("- **staged-return type mismatch** (fixed in `6618671`): a request staged "
      "but never admitted was lost when its model migrated away, because the "
      "return path ran a backend payload through the admitted-`Req` converter. "
      "Evidence: `exp/results/final-evaluation/STAGED_RETURN_TYPE_DEFECT.md` "
      "and the preserved run "
      "`02-tau-calibration/raw/tau_0p00035/seed_0.staged-return-defect1`.")
    A("- **GPU-scoped backend queue**: `alg2_seq` is per-GPU while the backend "
      "queue was per-model and shared. See `FINAL_BASELINE_HANDOFF.md`; do not "
      "merge those queues back together.")
    A("")

    args.out.write_text("\n".join(L) + "\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
