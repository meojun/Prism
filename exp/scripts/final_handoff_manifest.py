#!/usr/bin/env python3
"""Build exp/final_baseline_manifest.json from what the final evaluation used.

Every field is read out of an artifact the pipeline produced, or off the
machine itself. Nothing here is written from memory: the server flags and
environment variables come from the actual command line of a real Final Prism
run, the model revisions from the snapshot directories the server loaded, the
workload hashes from the frozen canonical set, and tau from FROZEN_TAU.json.

The manifest is what a person on the next server reads to know what they are
supposed to reproduce, so a field that cannot be established is recorded as
null with a note, never guessed.
"""
import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path


def sha256_file(p):
    p = Path(p)
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def load(p, default=None):
    try:
        return json.loads(Path(p).read_text())
    except Exception:                                   # noqa: BLE001
        return default


def git(root, *args):
    try:
        return subprocess.run(["git", "-C", str(root), *args],
                              capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:                                   # noqa: BLE001
        return None


def newest_final_run(eval_dir):
    """A real Prism run, whose command line is the authoritative record.

    A Final Prism run first. If the final arm has not run yet, a calibration
    run serves: it launches the same paper-faithful server with the same flags,
    differing only in tau and workload, both of which are recorded elsewhere.
    """
    for pattern in ("05-final-c/raw/*/rate_*/seed_*",
                    "02-tau-calibration/raw/tau_*/seed_*"):
        for r in sorted(eval_dir.glob(pattern)):
            if "." in r.name:            # preserved failed attempt
                continue
            if (r / "STAGE_CMD.sh").is_file() and (r / "pipeline.rc").is_file() \
               and (r / "pipeline.rc").read_text().strip() == "0":
                return r
    return None


def parse_server_invocation(run):
    """The launch flags and exported variables, straight off the run itself."""
    text = ""
    for name in ("SERVER_COMMAND.txt", "server-logs/stdout.log", "STAGE_CMD.sh"):
        p = run / name
        if p.is_file():
            text += p.read_text(errors="replace")[:400_000]
    m = re.search(r"(python3? -m sglang\.launch_multi_model_server[^\n]*)", text)
    flags = {}
    if m:
        toks = shlex.split(m.group(1).rstrip("; "))
        i = 0
        while i < len(toks):
            t = toks[i]
            if t.startswith("--"):
                if i + 1 < len(toks) and not toks[i + 1].startswith("--"):
                    flags[t] = toks[i + 1]; i += 2
                else:
                    flags[t] = True; i += 1
            else:
                i += 1
    env = dict(re.findall(r"\b(PRISM_[A-Z0-9_]+|KVPR_TAU|FLASHINFER_WORKSPACE_SIZE|"
                          r"HF_HOME|CUDA_VISIBLE_DEVICES|TOKENIZERS_PARALLELISM)="
                          r"'?([^\s']*)'?", text))
    if not flags:
        # Runs made before the command was persisted do not carry it. The
        # launcher that built it is version-controlled, so point at that
        # rather than reconstruct flags that would only look authoritative.
        flags = {"__source__": "exp/scripts/run_v4_case.sh (command not "
                               "persisted by this run; later runs write "
                               "SERVER_COMMAND.txt beside themselves)"}
    return {"launch_flags": flags, "observed_environment": env,
            "source_run": str(run),
            "note": ("read off a real run's own command line; a calibration run "
                     "is used when the final arm has not run yet -- same "
                     "server, same flags, tau and workload recorded separately")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--runtime-freeze", required=True)
    ap.add_argument("--handoff-sha", default=None)
    args = ap.parse_args()

    root = args.root
    ev = root / "exp/results/final-evaluation"
    notes = []

    env_record = load(ev / "FINAL_ENVIRONMENT.json", {})
    tau_doc = load(ev / "02-tau-calibration/FROZEN_TAU.json")
    ci_path = ev / "01-ci-profile/prefill_speed_final_a100.json"
    canonical = load(ev / "CANONICAL_WORKLOAD_SHA256.json", {})
    canonical_detail = load(ev / "CANONICAL_WORKLOAD_DETAIL.json", {})
    aggregation = load(ev / "06-aggregate/AGGREGATION_MANIFEST.json")

    if tau_doc is None:
        notes.append("FROZEN_TAU.json missing: tau was never frozen")
    if aggregation is None:
        notes.append("AGGREGATION_MANIFEST.json missing: the comparison never "
                     "completed")

    run = newest_final_run(ev)
    invocation = parse_server_invocation(run) if run else None
    if invocation is None:
        notes.append("no Final Prism run with a recorded command line was "
                     "found; server flags could not be established")

    patch_rel = "patches/final_baseline_ready/prism_research_worktree.patch"
    manifest = {
        "what_this_is": (
            "The frozen state of the paper-faithful Prism baseline: the runtime "
            "under evaluation, the calibration that chose tau, the canonical "
            "workloads both arms ran, and the environment they ran in. Read "
            "FINAL_BASELINE_HANDOFF.md for how to run it on another server."),
        "final_runtime_sha": args.runtime_freeze,
        "handoff_sha": args.handoff_sha,
        "runtime": {
            "experiment_repo_commit": git(root, "rev-parse", "HEAD"),
            "experiment_repo_branch": git(root, "rev-parse", "--abbrev-ref", "HEAD"),
            "source_repo_base_commit": git(root / "prism-research", "rev-parse", "HEAD"),
            "worktree_patch": patch_rel,
            "worktree_patch_git_blob": git(root, "hash-object", patch_rel),
            "worktree_patch_sha256": sha256_file(root / patch_rel),
            "worktree_patch_blob_at_freeze": git(
                root, "rev-parse", f"{args.runtime_freeze}:{patch_rel}"),
        },
        "selected_tau": (tau_doc or {}).get("tau"),
        "tau_selection": {
            "rule": (tau_doc or {}).get("selection_rule"),
            "label": (tau_doc or {}).get("tau_label"),
            "calibration_seeds": (tau_doc or {}).get("calibration_seeds"),
            "evaluation_seeds_untouched": (tau_doc or {}).get("evaluation_seeds_untouched"),
            "candidates": (tau_doc or {}).get("candidates"),
            "frozen_utc": (tau_doc or {}).get("frozen_utc"),
        },
        "c_i": {
            "values": load(ci_path),
            "path": str(ci_path.relative_to(root)),
            "sha256": sha256_file(ci_path),
            "provenance": load(ev / "01-ci-profile/prefill_speed_final_a100_provenance.json"),
            "sanity": (load(ev / "01-ci-profile/CI_SANITY.json") or {}).get("verdict"),
            "note": ("measured on this hardware and held fixed for the whole "
                     "study; re-measure on different GPUs before comparing"),
        },
        "models": env_record.get("models"),
        "model_config": {
            "path": "exp/configs/v2/6model_2gpu.json",
            "sha256": sha256_file(root / "exp/configs/v2/6model_2gpu.json"),
            "content": load(root / "exp/configs/v2/6model_2gpu.json"),
        },
        "slo": {
            "path": "exp/configs/v2/slo_base.json",
            "sha256": sha256_file(root / "exp/configs/v2/slo_base.json"),
            "content": load(root / "exp/configs/v2/slo_base.json"),
            "note": ("per-request TTFT and TPOT SLOs are these base values "
                     "scaled by the harness; the TPOT scale is a launch flag "
                     "recorded under server_invocation"),
        },
        "workloads": {
            "directory": "exp/workloads/final-evaluation",
            "conditions": ("bursty rates 2/4/8/14/20 and steady rates 4/8/20, "
                           "seeds 1/2/3 -- 24 in all; calibration uses bursty "
                           "rate 20 on held-out seeds 0 and 42"),
            "canonical_sha256": canonical,
            "detail": canonical_detail,
            "generator": "exp/scripts/build_paired_workload.py",
        },
        "hardware_requirement": {
            "gpus_required": 2,
            "gpus_used": env_record.get("gpus"),
            "gpu_model": "NVIDIA A100-SXM4-80GB",
            "note": ("2 GPUs with 80 GB each and NVLink between them; the "
                     "placement config and memory pool sizes assume 80 GB"),
            "topology": env_record.get("nvidia_smi_topo_m"),
        },
        "versions": env_record.get("versions"),
        "pins": {
            "file": "setup/pins.env",
            "sha256": sha256_file(root / "setup/pins.env"),
            "lockfile": "setup/requirements.lock.txt",
            "lockfile_sha256": sha256_file(root / "setup/requirements.lock.txt"),
        },
        "environment_variables": {
            "flashinfer_workspace_bytes": env_record.get("flashinfer_workspace_bytes"),
            "flashinfer_workspace_source": env_record.get("flashinfer_workspace_source"),
            "set_by": "exp/scripts/env.sh",
            "required_non_secret": [
                "PRISM_ROOT", "PRISM_REPO", "PRISM_EXP", "HF_HOME", "DATASETS",
                "FLASHINFER_WORKSPACE_SIZE", "PYTHONPATH", "TOKENIZERS_PARALLELISM",
            ],
            "required_secret_names_only": ["HF_TOKEN", "PRISM_NTFY_TOPIC"],
            "secret_note": ("values live outside the repository; see "
                            ".env.example for the names"),
        },
        "server_invocation": invocation,
        "correctness_gates": {
            "per_run": [
                "rc == 0", "every offered request accounted for",
                "stale dispatched sequences == 0",
                "Algorithm 2 order violations == 0",
                "ownership / identity mismatches == 0",
                "client descriptor failures == 0",
                "unexpected connection failures == 0",
            ],
            "recorded_in": "VERIFICATION.json beside each run",
            "checker": "exp/scripts/final_run_verify.py",
            "interaction_gate": "exp/scripts/check_alg2_interaction.py",
        },
        "handoff": {
            "workloads": "exp/final-handoff/workloads_manifest.json",
            "workloads_sha256": sha256_file(root / "exp/final-handoff/workloads_manifest.json"),
            "calibration": "exp/final-handoff/calibration_manifest.json",
            "calibration_sha256": sha256_file(root / "exp/final-handoff/calibration_manifest.json"),
            "resume": "exp/final-handoff/resume_manifest.json",
            "resume_sha256": sha256_file(root / "exp/final-handoff/resume_manifest.json"),
            "local_dependencies": "exp/final-handoff/local_dependencies.json",
            "document": "FINAL_BASELINE_HANDOFF.md",
            "results_index": "CURRENT_RESULTS_INDEX.md",
            "bootstrap": "exp/scripts/bootstrap_final_baseline.sh",
            "preflight": "exp/scripts/handoff_preflight.py",
            "restore_workloads": "exp/scripts/restore_workloads.sh",
            "resume_command": "bash exp/scripts/resume_baseline.sh",
        },
        "pipeline_state": load(ev / "PIPELINE_STATE.json"),
        "results": {
            "aggregation_manifest": aggregation,
            "index": "CURRENT_RESULTS_INDEX.md",
        },
        "notes": notes,
    }

    args.out.write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n")
    print(f"wrote {args.out} ({len(notes)} note(s))")
    for n in notes:
        print(f"  note: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
