#!/usr/bin/env python3
"""Is this machine able to run the 4-model paired evaluation, and is every
frozen input actually what it claims to be?

Fails closed. Nothing here is repaired automatically.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_GPUS = 2
REQUIRED_GPU_MIB = 80000
MIN_FD = 65535
FLASHINFER_MIN = 1 << 30
KEEP = ("model_3", "model_4", "model_5", "model_6")
TAU = 0.00035


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def sh(cmd):
    return subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path,
                    default=ROOT / "exp/results/4het-paired/PREFLIGHT.json")
    args = ap.parse_args()
    cfgdir = ROOT / "exp/configs/v4het"
    wl = ROOT / "exp/workloads/4het"
    outdir = ROOT / "exp/results/4het-paired"
    rows = []

    def add(name, ok, detail, required=True):
        rows.append({"check": name, "pass": bool(ok), "required": required,
                     "detail": detail})
        return ok

    # ---- GPUs ------------------------------------------------------------
    r = sh("nvidia-smi --query-gpu=index,name,memory.total,driver_version,"
           "compute_cap --format=csv,noheader")
    gpus = [l.strip() for l in r.stdout.splitlines() if l.strip()]
    add(f"{REQUIRED_GPUS} GPUs visible", len(gpus) == REQUIRED_GPUS, gpus)
    mems = [int(m.group(1)) for g in gpus
            if (m := re.search(r"(\d+)\s*MiB", g))]
    add("each GPU has at least 80 GB", bool(mems) and min(mems) >= REQUIRED_GPU_MIB,
        f"{mems} MiB")
    add("every GPU is an A100-80GB",
        bool(gpus) and all("A100" in g and "80GB" in g.replace("-", "").replace(" ", "")
                           or "A100-SXM4-80GB" in g for g in gpus),
        [g.split(",")[1].strip() for g in gpus] if gpus else "none")

    # ---- runtime: the BUILT source, not the patch file -------------------
    r = sh(f"bash '{ROOT}/exp/scripts/restore_frozen_runtime.sh' --verify-only")
    add("built runtime source is byte-identical to freeze 6618671",
        r.returncode == 0, (r.stdout + r.stderr).strip()[-200:])

    # ---- frozen configs ---------------------------------------------------
    base = json.loads((ROOT / "exp/FINAL_BASELINE_MANIFEST.json").read_text())
    cfg = json.loads((cfgdir / "4model_2gpu.json").read_text())
    add("model config has exactly the four models",
        sorted(m["model_name"] for m in cfg) == sorted(KEEP),
        sorted(m["model_name"] for m in cfg))
    orig = {m["model_name"]: m for m in base["model_config"]["content"]}
    same = all(cfg_m == orig[cfg_m["model_name"]] for cfg_m in cfg)
    add("each model's placement/tp/pool is carried over unchanged", same,
        "identical to the frozen 6-model entries" if same else "differs")

    slo = json.loads((cfgdir / "slo_base.json").read_text())
    add("SLO base carried over unchanged for all four models",
        all(slo[k] == base["slo"]["content"][k] for k in KEEP),
        {k: slo[k] for k in KEEP})

    ci = json.loads((cfgdir / "prefill_speed_4het_a100.json").read_text())
    ci_src = ROOT / "exp/results/final-evaluation/01-ci-profile/prefill_speed_final_a100.json"
    ci_full = json.loads(ci_src.read_text())
    add("c_i carried over unchanged from the A100 profile",
        all(ci[k] == ci_full[k] for k in KEEP), {k: ci[k] for k in KEEP})
    add("c_i source profile still hashes to the profiled original",
        sha256_file(ci_src) == base["c_i"]["sha256"],
        f"{sha256_file(ci_src)[:16]} vs {base['c_i']['sha256'][:16]}")

    # ---- models: revision, tokenizer, dtype ------------------------------
    revs = json.loads((cfgdir / "model_revisions.json").read_text())
    for k in KEEP:
        want = Path(base["models"][k]["hf_snapshot"])
        add(f"{k} revision matches the 6-model baseline",
            revs[k] == want.name, f"{base['models'][k]['model_path']} @ {revs[k][:12]}")
        add(f"{k} snapshot present in the cache", want.is_dir(), str(want))
        if want.is_dir():
            add(f"{k} tokenizer comes from that revision",
                (want / "tokenizer_config.json").is_file(),
                sorted(p.name for p in want.glob("tokenizer*"))[:4])
            c = want / "config.json"
            dt = json.loads(c.read_text()).get("torch_dtype") if c.is_file() else None
            add(f"{k} loads at bfloat16", dt == "bfloat16",
                f"torch_dtype={dt}")

    # ---- tau --------------------------------------------------------------
    add("tau is the frozen 0.00035", abs(TAU - 0.00035) < 1e-12,
        "selected using an independent prior heterogeneous calibration setup "
        "and frozen before this evaluation")

    # ---- workloads --------------------------------------------------------
    man = outdir / "WORKLOAD_MANIFEST.json"
    if man.is_file():
        files = json.loads(man.read_text())["files"]
        bad = [n for n, m in files.items()
               if not (wl / n).is_file() or sha256_file(wl / n) != m["sha256"]]
        add("all 20 canonical workloads present and matching",
            len(files) == 20 and not bad, bad or f"{len(files)} traces verified")
        bursty = sum(1 for n in files if n.startswith("bursty_"))
        steady = sum(1 for n in files if n.startswith("steady_"))
        add("10 bursty and 10 steady", bursty == 10 and steady == 10,
            f"bursty={bursty} steady={steady}")
    else:
        add("workload manifest present", False, str(man))

    # ---- machine ----------------------------------------------------------
    env_sh = ROOT / "exp/scripts/env.sh"
    r = sh(f"source '{env_sh}' >/dev/null 2>&1; echo \"$FLASHINFER_WORKSPACE_SIZE\"")
    fw = r.stdout.strip()
    add("FlashInfer workspace is at least 1 GiB",
        fw.isdigit() and int(fw) >= FLASHINFER_MIN, f"={fw or 'unset'}")
    add("redis answers PING",
        sh("redis-cli ping 2>/dev/null | grep -q PONG").returncode == 0, "")
    hard = sh("ulimit -Hn").stdout.strip()
    add("descriptor limit high enough",
        hard == "unlimited" or (hard.isdigit() and int(hard) >= MIN_FD), hard)
    shm = sorted(p.name for p in Path("/dev/shm").glob("ipc_*"))
    add("no stale kvcached shared-memory segments", not shm, shm or "clean")
    r = sh("pgrep -af 'sglang[.]launch_multi_model_server' | grep -v pgrep")
    running = [l for l in r.stdout.splitlines() if l.strip()]
    add("no server already running", not running, running or "none")
    stop = ROOT / "exp/results/final-evaluation/STOP"
    add("no STOP in force", not stop.is_file(),
        stop.read_text().strip()[:120] if stop.is_file() else "none")
    add("HF_TOKEN available",
        bool(sh(f"source '{env_sh}' >/dev/null 2>&1; [ -n \"$HF_TOKEN\" ] && echo y")
             .stdout.strip()), "gated Llama models")

    failed = [r for r in rows if r["required"] and not r["pass"]]
    out = {"verdict": "PASS" if not failed else "FAIL",
           "checks": rows, "failed": [r["check"] for r in failed]}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, default=str))
    for r in rows:
        print(f"  {'PASS' if r['pass'] else 'FAIL'}  {r['check']}: "
              f"{str(r['detail'])[:110]}")
    print(f"\nVERDICT: {out['verdict']}  ({len(rows)-len(failed)}/{len(rows)})")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
