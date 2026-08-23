#!/usr/bin/env python3
"""Preflight for a new server: is this machine able to run the baseline?

Run this after bootstrap and before any benchmark. It checks the things that,
when missing, produce a run that looks like a result but is not one -- the
wrong GPU count, a model that is not really downloaded, a workload whose hash
does not match, a descriptor limit that throttles the client, a FlashInfer
workspace small enough to kill the server mid-run, a stale shared-memory
segment from a crashed server, a port already in use.

Exit is non-zero if any required check fails, and the pipeline refuses to start
on a failing preflight. Nothing here is repaired automatically: a machine that
is not ready should be made ready deliberately.
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

REQUIRED_GPUS = 2
REQUIRED_GPU_MEMORY_MIB = 80000
MIN_FD_SOFT = 65535
REQUIRED_FLASHINFER_BYTES = 1 << 30
DEFAULT_PORTS = (41800,)


class Checks:
    def __init__(self):
        self.rows = []

    def add(self, name, ok, detail, required=True):
        self.rows.append({"check": name, "pass": bool(ok), "required": required,
                          "detail": detail})
        return ok

    def failed(self):
        return [r for r in self.rows if r["required"] and not r["pass"]]


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
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    ap.add_argument("--manifest", type=Path, default=None,
                    help="exp/final_baseline_manifest.json (default: in --root)")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--ports", type=int, nargs="*", default=list(DEFAULT_PORTS))
    args = ap.parse_args()

    root = args.root.resolve()
    manifest_path = args.manifest or root / "exp/final_baseline_manifest.json"
    manifest = {}
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
    c = Checks()
    c.add("baseline manifest present", bool(manifest), str(manifest_path))

    # --- GPUs -------------------------------------------------------------
    r = sh("nvidia-smi --query-gpu=index,name,memory.total,driver_version,"
           "compute_cap --format=csv,noheader")
    gpus = [l.strip() for l in r.stdout.splitlines() if l.strip()]
    c.add(f"{REQUIRED_GPUS} GPUs visible", len(gpus) == REQUIRED_GPUS,
          gpus or r.stderr.strip())
    if gpus:
        mems = [int(re.search(r"(\d+)\s*MiB", g).group(1)) for g in gpus
                if re.search(r"(\d+)\s*MiB", g)]
        c.add("each GPU has at least 80 GB",
              bool(mems) and min(mems) >= REQUIRED_GPU_MEMORY_MIB,
              f"{mems} MiB")
        caps = {g.split(",")[-1].strip() for g in gpus}
        c.add("compute capability is supported by the pinned stack",
              all(float(x) < 10.0 for x in caps if x.replace(".", "").isdigit()),
              f"compute_cap {sorted(caps)}; the pinned torch 2.4.0+cu121 has no "
              "Blackwell kernels")

    # --- CUDA through torch, which is what the server actually uses --------
    r = sh(f"'{root}/prism-venv/bin/python' -c "
           "\"import torch,json;print(json.dumps({'cuda':torch.cuda.is_available(),"
           "'n':torch.cuda.device_count(),'torch':torch.__version__}))\" 2>&1 | tail -1")
    try:
        t = json.loads(r.stdout.strip())
        c.add("torch sees CUDA", t["cuda"] and t["n"] >= REQUIRED_GPUS, t)
    except Exception:                                   # noqa: BLE001
        c.add("torch sees CUDA", False, r.stdout.strip()[-300:] or "python failed")

    # --- models -----------------------------------------------------------
    cfg_path = root / "exp/configs/v2/6model_2gpu.json"
    hf_home = os.environ.get("HF_HOME", "/workspace/.hf_home")
    recorded = (manifest.get("models") or {})
    if cfg_path.is_file():
        cfg = json.loads(cfg_path.read_text())
        missing, wrong_rev = [], []
        for m in cfg:
            repo = m["model_path"].replace("/", "--")
            snaps = Path(hf_home) / "hub" / f"models--{repo}" / "snapshots"
            if not snaps.is_dir() or not any(snaps.iterdir()):
                missing.append(m["model_path"])
                continue
            want = (recorded.get(m["model_name"]) or {}).get("hf_snapshot")
            if want and Path(want).name not in {p.name for p in snaps.iterdir()}:
                wrong_rev.append(f"{m['model_path']}: want {Path(want).name}")
        c.add("all six models present in the cache", not missing,
              missing or f"{len(cfg)} models under {hf_home}")
        c.add("model revisions match the ones evaluated", not wrong_rev,
              wrong_rev or "revisions match")
    else:
        c.add("model config present", False, str(cfg_path))

    # --- workloads --------------------------------------------------------
    canon = (manifest.get("workloads") or {}).get("canonical_sha256") or {}
    wl = root / "exp/workloads/final-evaluation"
    bad, absent = [], []
    for name, want in canon.items():
        p = wl / name
        if not p.is_file():
            absent.append(name)
        elif sha256_file(p) != want:
            bad.append(name)
    c.add("all 24 canonical workloads present", not absent,
          absent or f"{len(canon)} files")
    c.add("canonical workload hashes match", not bad, bad or "all match")

    # --- environment ------------------------------------------------------
    env_sh = root / "exp/scripts/env.sh"
    c.add("env.sh present", env_sh.is_file(), str(env_sh))
    r = sh(f"source '{env_sh}' >/dev/null 2>&1; "
           "echo \"$FLASHINFER_WORKSPACE_SIZE|$HF_HOME|$DATASETS|$PRISM_ROOT\"")
    fw, hf, ds, proot = (r.stdout.strip().split("|") + ["", "", "", ""])[:4]
    c.add("FlashInfer workspace is at least 1 GiB",
          fw.isdigit() and int(fw) >= REQUIRED_FLASHINFER_BYTES,
          f"FLASHINFER_WORKSPACE_SIZE={fw or 'unset'}; below 1 GiB the server "
          "dies mid-run on model_6 prefill")
    c.add("HF_HOME resolves to a real cache", bool(hf) and Path(hf).is_dir(), hf)
    c.add("PRISM_ROOT resolves", bool(proot) and Path(proot).is_dir(), proot)
    c.add("ShareGPT dataset directory present", bool(ds) and Path(ds).is_dir(),
          ds, required=False)
    c.add("HF_TOKEN available for the gated Llama models",
          bool(sh("source '%s' >/dev/null 2>&1; [ -n \"$HF_TOKEN\" ] && echo y"
                  % env_sh).stdout.strip()),
          "set it in the .env file outside the repository; see .env.example")

    # --- redis ------------------------------------------------------------
    c.add("redis-server installed", shutil.which("redis-server") is not None,
          shutil.which("redis-server") or "not on PATH")
    c.add("redis answers PING",
          sh("redis-cli ping 2>/dev/null | grep -q PONG").returncode == 0,
          "the GPU scheduler's backend queues live in redis")

    # --- descriptors ------------------------------------------------------
    soft = sh("ulimit -Sn").stdout.strip()
    hard = sh("ulimit -Hn").stdout.strip()
    c.add("descriptor limit is high enough for the client",
          hard.isdigit() and int(hard) >= MIN_FD_SOFT or hard == "unlimited",
          f"soft={soft} hard={hard}; the benchmark client raises the soft limit "
          f"to {MIN_FD_SOFT} and a lower hard limit caps it, which measures the "
          "client instead of the system")

    # --- clean slate ------------------------------------------------------
    shm = sorted(p.name for p in Path("/dev/shm").glob("ipc_*")) \
        if Path("/dev/shm").is_dir() else []
    c.add("no stale kvcached shared-memory segments", not shm,
          shm or "/dev/shm is clean")
    r = sh("pgrep -af 'sglang.launch_multi_model_server' | head -5")
    c.add("no server already running", not r.stdout.strip(),
          r.stdout.strip().splitlines() or "none")
    busy = []
    for port in args.ports:
        s = socket.socket()
        s.settimeout(0.4)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            busy.append(port)
        s.close()
    c.add("required ports are free", not busy, busy or list(args.ports))

    # --- output -----------------------------------------------------------
    outdir = root / "exp/results"
    try:
        outdir.mkdir(parents=True, exist_ok=True)
        probe = outdir / ".preflight_write_probe"
        probe.write_text("ok")
        probe.unlink()
        ok = True
    except Exception as exc:                            # noqa: BLE001
        ok, outdir = False, f"{outdir}: {exc}"
    c.add("results directory is writable", ok, str(outdir))

    failed = c.failed()
    doc = {"verdict": "PASS" if not failed else "FAIL",
           "root": str(root), "checks": c.rows,
           "failed": [f["check"] for f in failed]}
    if args.out:
        args.out.write_text(json.dumps(doc, indent=2) + "\n")
    for row in c.rows:
        mark = "PASS" if row["pass"] else ("FAIL" if row["required"] else "warn")
        print(f"  {mark:4}  {row['check']}")
        if not row["pass"]:
            print(f"        {row['detail']}")
    print(f"\nVERDICT: {doc['verdict']}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
