#!/usr/bin/env python3
"""Record exactly what the final evaluation is being run on, once, before it starts."""

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def sh(args):
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              timeout=60).stdout.strip()
    except Exception as e:
        return f"<unavailable: {e}>"


def sha256(path):
    path = Path(path)
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "exp/results/final-evaluation/FINAL_ENVIRONMENT.json"

    git_sha = sh(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
    git_short = sh(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"])
    # The chain writes its own results and workloads as it runs, so those
    # paths are excluded: what must be clean is the code and configuration the
    # evaluation is running.
    ignore = ("exp/results/final-evaluation", "exp/workloads/final-evaluation")
    dirty = "\n".join(
        line for line in sh(["git", "-C", str(ROOT), "status", "--porcelain"]).splitlines()
        if not any(part in line for part in ignore))
    src = ROOT / "prism-research"
    src_sha = sh(["git", "-C", str(src), "rev-parse", "HEAD"])
    src_dirty = sh(["git", "-C", str(src), "status", "--porcelain"])

    versions = {}
    py = "/workspace/prism-exp/prism-venv/bin/python"
    code = ("import json,sys,torch;"
            "import sglang;"
            "print(json.dumps({'python':sys.version.split()[0],"
            "'torch':torch.__version__,'cuda_runtime':torch.version.cuda,"
            "'cudnn':torch.backends.cudnn.version(),"
            "'sglang':getattr(sglang,'__version__','unknown'),"
            "'device_count':torch.cuda.device_count()}))")
    raw = sh([py, "-c", code])
    try:
        versions = json.loads(raw.splitlines()[-1])
    except Exception:
        versions = {"error": raw[:400]}
    for pkg in ("flashinfer", "kvcached", "vllm", "transformers"):
        versions[pkg] = sh([py, "-c",
                            f"import {pkg};print(getattr({pkg},'__version__','unknown'))"]).splitlines()[-1:] or None
        versions[pkg] = versions[pkg][0] if versions[pkg] else None

    gpus = sh(["nvidia-smi",
               "--query-gpu=index,name,memory.total,driver_version,compute_cap",
               "--format=csv,noheader"])
    topo = sh(["nvidia-smi", "topo", "-m"])
    nvlink = sh(["nvidia-smi", "nvlink", "--status"])

    exp = ROOT / "exp"
    workload_dir = exp / "results/baseline-readiness/raw/migration-D2/workload"
    hashes = {
        "config_6model_2gpu": sha256(exp / "configs/v2/6model_2gpu.json"),
        "slo_base": sha256(exp / "configs/v2/slo_base.json"),
        "prefill_speed_current": sha256(exp / "configs/v2/prefill_speed.json"),
        "bursty_r20_s1_pkl": sha256(workload_dir / "bursty_r20_s1.pkl"),
        "paired_requests_r20_s1": sha256(workload_dir / "paired_requests_r20_s1.json"),
        "phases_r20_s1": sha256(workload_dir / "phases_r20_s1.json"),
        "run_v4_case_sh": sha256(exp / "scripts/run_v4_case.sh"),
    }

    models = {}
    for entry in json.loads((exp / "configs/v2/6model_2gpu.json").read_text()):
        path = entry["model_path"]
        cache = list(Path("/workspace/.hf_home/hub").glob(
            f"models--{path.replace('/', '--')}/snapshots/*"))
        models[entry["model_name"]] = {
            "model_path": path,
            "tp_size": entry["tp_size"],
            "hf_snapshot": str(cache[0]) if cache else None,
        }

    # The runtime lives in prism-research/, which this repo gitignores, so the
    # thing to pin is the source patch -- not this repo's HEAD, which moves as
    # pipeline scripts are added. RUNTIME_FREEZE is the commit the runtime was
    # frozen at; the patch must still be byte-identical to the one it carried.
    runtime_freeze = os.environ.get("PRISM_RUNTIME_FREEZE", "205e0e9")
    patch_rel = "patches/final_baseline_ready/prism_research_worktree.patch"
    # Compared as git blob identities: sh() strips trailing whitespace, which
    # would make a byte-identical patch look changed.
    frozen_patch_sha = sh(["git", "-C", str(ROOT), "rev-parse",
                           f"{runtime_freeze}:{patch_rel}"])
    current_patch_sha = sh(["git", "-C", str(ROOT), "hash-object", patch_rel])
    runtime_unchanged = (
        bool(frozen_patch_sha) and frozen_patch_sha == current_patch_sha
        and not frozen_patch_sha.startswith("<"))

    env = {
        "recorded_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_freeze": {
            "commit": runtime_freeze,
            "patch": patch_rel,
            "patch_blob_at_freeze": frozen_patch_sha,
            "patch_blob_now": current_patch_sha,
            "runtime_unchanged_since_freeze": runtime_unchanged,
        },
        "freeze": {
            "git_sha": git_sha,
            "git_sha_short": git_short,
            "git_clean": dirty == "",
            "git_dirty_files": dirty.splitlines(),
            "clean_check_excludes": list(ignore),
            "source_repo_sha": src_sha,
            "source_repo_dirty_files": len(src_dirty.splitlines()),
            "source_patch": "patches/final_baseline_ready/prism_research_worktree.patch",
            "source_patch_sha256": sha256(
                ROOT / "patches/final_baseline_ready/prism_research_worktree.patch"),
        },
        "gpus": gpus.splitlines(),
        "nvidia_smi_topo_m": topo,
        "nvlink_status": nvlink.splitlines()[:8],
        "versions": versions,
        "flashinfer_workspace_bytes": int(os.environ.get(
            "FLASHINFER_WORKSPACE_SIZE", 1073741824)),
        "flashinfer_workspace_source": "exp/scripts/env.sh",
        "hashes": hashes,
        "models": models,
        "workload_dir": str(workload_dir),
        "python_interpreter": py,
        "client_fd": _client_fd_gate(),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(env, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: env[k] for k in ("freeze", "gpus", "versions",
                                          "flashinfer_workspace_bytes")},
                     indent=2)[:1600])
    print(f"\nwrote {out}")
    # A run under a low client descriptor limit measures the client, so the
    # experiment must not start at all.
    if not env["client_fd"].get("pass"):
        print("\nFAIL: benchmark client fd limit\n"
              + json.dumps(env["client_fd"], indent=2), file=sys.stderr)
        return 1
    if not runtime_unchanged:
        print("FATAL: the runtime source patch differs from the frozen commit",
              file=sys.stderr)
        return 1
    if not env["freeze"]["git_clean"]:
        print("FATAL: the experiment repository has uncommitted changes",
              file=sys.stderr)
        return 1
    return 0


def _client_fd_gate():
    """The benchmark client's descriptor limit, checked the way runs launch."""
    import subprocess, json as _json
    here = os.path.dirname(os.path.abspath(__file__))
    r = subprocess.run(
        [sys.executable, os.path.join(here, "check_client_fd.py"),
         "--mode", "preflight"], capture_output=True, text=True, timeout=120)
    try:
        return _json.loads(r.stdout)
    except Exception:
        return {"pass": False, "reason": "client fd preflight did not report"}


if __name__ == "__main__":
    raise SystemExit(main())
