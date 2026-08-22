#!/usr/bin/env python3
"""Measure why the KV migration path moves bytes ~30x slower than the weight path.

Both paths move GPU-resident tensors between the same two A100s over the same
link, but the D2 runs measured weights at ~13 GB/s and KV at 0.31-0.48 GB/s.
This decomposes the KV path against the weight path on the same pair, and
reports -- separately, per the questions being asked of it:

    actual KV bytes
    source -> target transfer wall time
    P2P/NVLink used (driver counters, not inference)
    host staging
    copy call count and mean chunk size
    per-copy synchronization
    serialization
    target-side inject/rebuild time
    whether transfer and inject overlap or are serial

Nothing here changes production behaviour: it imports the shipped
`kv_migration_v6` and `parallel_loading_v4` and times them as they are.

    python microbench_kv_migration.py --profile model_3 --out results.json
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import torch

REPO = Path(__file__).resolve().parents[2] / "prism-research"
sys.path.insert(0, str(REPO / "python"))

from sglang.multi_model import kv_migration_v6 as kvm  # noqa: E402
from sglang.multi_model.parallel_loading_v4 import (  # noqa: E402
    StreamPool, copy_model_to_gpu_v4, enable_peer_access,
)

# Shapes and per-request token counts taken from the D2 runs of 2026-08-22, so
# the microbenchmark moves the same tensors the real migrations moved.
PROFILES = {
    # name: (model path, layers, kv_heads, head_dim, requests, tokens/request)
    "model_1": ("meta-llama/Llama-3.2-1B", 16, 8, 64, 50, 270),
    "model_2": ("Qwen/Qwen2.5-1.5B-Instruct", 28, 2, 128, 79, 321),
    "model_3": ("meta-llama/Llama-3.2-3B", 28, 8, 128, 161, 323),
    "model_4": ("Qwen/Qwen2.5-3B-Instruct", 36, 2, 128, 114, 390),
    "model_6": ("Qwen/Qwen2.5-7B-Instruct", 28, 4, 128, 47, 239),
}


def nvlink_counters(gpu):
    """Bytes on this GPU's NVLink lanes, from the driver's own counters."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "nvlink", "-gt", "d", "-i", str(gpu)],
            capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return None
    tx = sum(int(v) for v in re.findall(r"Data Tx:\s+(\d+) KiB", out))
    rx = sum(int(v) for v in re.findall(r"Data Rx:\s+(\d+) KiB", out))
    if not tx and not rx:
        return None
    return {"tx_bytes": tx * 1024, "rx_bytes": rx * 1024}


def delta(before, after):
    if not before or not after:
        return None
    return {k: after[k] - before[k] for k in before}


class Probe:
    """Counts every device copy and every synchronize the path performs.

    `torch.Tensor.to` and `torch.cuda.synchronize` are patched for the duration
    of one measurement. The counts are the point: a path that moves its bytes in
    a few large copies and synchronizes once behaves nothing like one that
    issues thousands of small copies and synchronizes after each request, even
    when both are "GPU to GPU over NVLink".
    """

    def __init__(self):
        self.copies = []          # (bytes, src_device, dst_device)
        self.host_staged = 0
        self.syncs = []           # seconds spent inside each synchronize
        self._orig_to = None
        self._orig_cpu = None
        self._orig_sync = None

    def __enter__(self):
        probe = self
        self._orig_to = torch.Tensor.to
        self._orig_cpu = torch.Tensor.cpu
        self._orig_sync = torch.cuda.synchronize

        def traced_to(tensor, *args, **kwargs):
            out = probe._orig_to(tensor, *args, **kwargs)
            if tensor.is_cuda or out.is_cuda:
                if tensor.device != out.device:
                    probe.copies.append((
                        tensor.numel() * tensor.element_size(),
                        str(tensor.device), str(out.device),
                    ))
                    if tensor.device.type == "cpu" or out.device.type == "cpu":
                        probe.host_staged += 1
            return out

        def traced_cpu(tensor, *args, **kwargs):
            out = probe._orig_cpu(tensor, *args, **kwargs)
            if tensor.is_cuda:
                probe.copies.append((
                    tensor.numel() * tensor.element_size(),
                    str(tensor.device), "cpu"))
                probe.host_staged += 1
            return out

        def traced_sync(device=None):
            start = time.perf_counter()
            probe._orig_sync(device)
            probe.syncs.append(time.perf_counter() - start)

        torch.Tensor.to = traced_to
        torch.Tensor.cpu = traced_cpu
        torch.cuda.synchronize = traced_sync
        return self

    def __exit__(self, *exc):
        torch.Tensor.to = self._orig_to
        torch.Tensor.cpu = self._orig_cpu
        torch.cuda.synchronize = self._orig_sync
        return False

    def report(self):
        sizes = [b for b, _, _ in self.copies]
        pairs = Counter(f"{s}->{d}" for _, s, d in self.copies)
        return {
            "copy_calls": len(sizes),
            "copy_bytes_total": sum(sizes),
            "copy_bytes_mean": (sum(sizes) / len(sizes)) if sizes else 0,
            "copy_bytes_min": min(sizes) if sizes else 0,
            "copy_bytes_max": max(sizes) if sizes else 0,
            "copy_device_pairs": dict(pairs),
            "host_staged_copies": self.host_staged,
            "synchronize_calls": len(self.syncs),
            "synchronize_seconds_total": sum(self.syncs),
            "synchronize_seconds_mean": (
                sum(self.syncs) / len(self.syncs)) if self.syncs else 0,
        }


def make_capsules(profile, source_gpu, requests, tokens):
    """Capsules with the real per-layer K/V shapes, resident on the source."""
    _path, layers, kv_heads, head_dim, _r, _t = PROFILES[profile]
    capsules = []
    for i in range(requests):
        k = [torch.randn(tokens, kv_heads, head_dim, dtype=torch.bfloat16,
                         device=f"cuda:{source_gpu}") for _ in range(layers)]
        v = [torch.randn(tokens, kv_heads, head_dim, dtype=torch.bfloat16,
                         device=f"cuda:{source_gpu}") for _ in range(layers)]
        capsules.append(kvm.RequestKVCapsule(
            rid=f"r{i}", model_name=profile, origin_input_ids=[1] * tokens,
            output_ids=[], sampling_params=None, arrival_time=time.time(),
            slo=None, k=k, v=v, source_gpu=source_gpu))
    torch.cuda.synchronize(source_gpu)
    return capsules


def measure_kv(profile, source_gpu, target_gpu, requests, tokens, background):
    """Time `migrate_request_kv` exactly as the engine calls it."""
    capsules = make_capsules(profile, source_gpu, requests, tokens)
    nbytes = sum(c.nbytes for c in capsules)

    stop = None
    if background:
        stop = start_background_load(target_gpu)

    before = nvlink_counters(source_gpu)
    with Probe() as probe:
        t0 = time.perf_counter()
        moved, skipped, record = kvm.migrate_request_kv(
            capsules, target_gpu, tag=f"microbench/{profile}")
        wall = time.perf_counter() - t0
    after = nvlink_counters(source_gpu)

    if stop is not None:
        stop.set()

    out = {
        "path": "kv_migration_v6.migrate_request_kv",
        "profile": profile,
        "requests": requests,
        "tokens_per_request": tokens,
        "kv_bytes": nbytes,
        "bytes_moved_by_the_call": record["kv_bytes"],
        "requests_skipped_over_cap": record["requests_skipped_over_cap"],
        "wall_seconds": wall,
        "gbps": nbytes / wall / 1e9 if wall else None,
        "transfer_path_reported": record["transfer_path"],
        "nvlink_delta": delta(before, after),
        "background_load_on_target": bool(background),
    }
    out.update(probe.report())
    del capsules, moved, skipped
    torch.cuda.empty_cache()
    return out


def measure_weights(source_gpu, target_gpu, nbytes, background):
    """The weight path moving a comparable payload between the same GPUs."""
    # One tensor per "layer" so the shapes are not absurdly different from a
    # real state dict; the weight path chunks them itself.
    chunk = 64 * 1024 * 1024
    count = max(1, nbytes // chunk)
    rows = chunk // (4096 * 2)
    src = {f"w{i}": torch.randn(rows, 4096, dtype=torch.bfloat16,
                                device=f"cuda:{source_gpu}")
           for i in range(count)}
    dst = {k: torch.empty_like(t, device=f"cuda:{target_gpu}")
           for k, t in src.items()}
    total = sum(t.numel() * t.element_size() for t in src.values())
    torch.cuda.synchronize(source_gpu)

    stop = None
    if background:
        stop = start_background_load(target_gpu)

    pool = StreamPool([source_gpu, target_gpu], per_gpu=3)
    before = nvlink_counters(source_gpu)
    from concurrent.futures import ThreadPoolExecutor
    with Probe() as probe:
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=8) as threads:
            rec = copy_model_to_gpu_v4(
                None, dst, target_gpu, threads, [source_gpu, target_gpu], pool,
                policy="paper", source_state_dict=src,
                tag="microbench/weights")
        wall = time.perf_counter() - t0
    after = nvlink_counters(source_gpu)

    if stop is not None:
        stop.set()

    out = {
        "path": "parallel_loading_v4.copy_model_to_gpu_v4",
        "kv_bytes": total,
        "wall_seconds": wall,
        "gbps": total / wall / 1e9 if wall else None,
        "transfer_path_reported": rec["transfer_path"],
        "nvlink_delta": delta(before, after),
        "background_load_on_target": bool(background),
    }
    out.update(probe.report())
    del src, dst
    torch.cuda.empty_cache()
    return out


def start_background_load(gpu):
    """Keep the target GPU busy, the way four serving workers keep it busy.

    A device-wide `torch.cuda.synchronize(target)` waits for *everything*
    queued on that device, not just the copy that was issued. Whether that
    matters is a measurement, not an assumption -- so measure it.
    """
    import threading
    stop = threading.Event()

    def spin():
        a = torch.randn(4096, 4096, device=f"cuda:{gpu}", dtype=torch.bfloat16)
        b = torch.randn(4096, 4096, device=f"cuda:{gpu}", dtype=torch.bfloat16)
        while not stop.is_set():
            for _ in range(50):
                a = a @ b
        torch.cuda.synchronize(gpu)

    thread = threading.Thread(target=spin, daemon=True)
    thread.start()
    time.sleep(1.0)
    return stop


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profiles", nargs="+", default=["model_3", "model_4"])
    ap.add_argument("--source-gpu", type=int, default=0)
    ap.add_argument("--target-gpu", type=int, default=1)
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    if torch.cuda.device_count() < 2:
        raise SystemExit("needs two visible GPUs")

    peer = enable_peer_access([args.source_gpu, args.target_gpu])
    results = {
        "gpus": {
            "source": args.source_gpu, "target": args.target_gpu,
            "peer_access": peer,
            "can_access_peer": bool(torch.cuda.can_device_access_peer(
                args.source_gpu, args.target_gpu)),
        },
        "runs": [],
    }

    for profile in args.profiles:
        _path, _l, _h, _d, requests, tokens = PROFILES[profile]
        for rep in range(args.reps):
            for background in (False, True):
                row = measure_kv(profile, args.source_gpu, args.target_gpu,
                                 requests, tokens, background)
                row["rep"] = rep
                results["runs"].append(row)
                print(f"KV  {profile} bg={background} rep={rep}: "
                      f"{row['kv_bytes']/1e9:.2f} GB in {row['wall_seconds']:.3f}s "
                      f"= {row['gbps']:.3f} GB/s, {row['copy_calls']} copies "
                      f"(mean {row['copy_bytes_mean']/1024:.0f} KiB), "
                      f"{row['synchronize_calls']} syncs "
                      f"({row['synchronize_seconds_total']:.3f}s)")

    reference_bytes = max(r["kv_bytes"] for r in results["runs"])
    for rep in range(args.reps):
        for background in (False, True):
            row = measure_weights(args.source_gpu, args.target_gpu,
                                  reference_bytes, background)
            row["rep"] = rep
            results["runs"].append(row)
            print(f"WT  bg={background} rep={rep}: "
                  f"{row['kv_bytes']/1e9:.2f} GB in {row['wall_seconds']:.3f}s "
                  f"= {row['gbps']:.3f} GB/s, {row['copy_calls']} copies "
                  f"(mean {row['copy_bytes_mean']/1024/1024:.1f} MiB), "
                  f"{row['synchronize_calls']} syncs "
                  f"({row['synchronize_seconds_total']:.3f}s)")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2) + "\n")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
