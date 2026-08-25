#!/usr/bin/env python3
"""Freeze the 20 canonical 4-model traces: hashes and what is inside them.

Written once, before any benchmark runs. Afterwards p4het_verify_workloads.py
checks every file against this and the sweep refuses to start on a mismatch,
so both arms provably consume the same bytes.
"""
import argparse
import hashlib
import json
import pickle
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from final_freeze_canonical import fingerprint  # noqa: E402

# Defaults are the canonical 20-trace matrix. The env overrides exist so a
# separate calibration set can be frozen with the same code and the same
# manifest schema; unset, behaviour is unchanged.
import os as _os
RATES = tuple(int(x) for x in _os.environ.get("FREEZE_RATES", "2,4,6,8,10").split(","))
SEEDS = tuple(int(x) for x in _os.environ.get("FREEZE_SEEDS", "1,2").split(","))
KINDS = tuple(_os.environ.get("FREEZE_KINDS", "bursty,steady").split(","))


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def describe(path):
    _prefix, reqs = pickle.loads(Path(path).read_bytes())
    arrivals = sorted(r.arrival_time for r in reqs)
    per_model = Counter(r.model for r in reqs)
    prompt = Counter()
    output = Counter()
    for r in reqs:
        prompt[r.model] += int(r.prompt_len)
        output[r.model] += int(r.output_len)
    span = round(arrivals[-1] - arrivals[0], 6) if arrivals else 0.0
    # Peak short-window offered rate, which is the thing "bursty" is for.
    peak = 0.0
    if arrivals and span > 0:
        w = 10.0
        j = 0
        for i, t in enumerate(arrivals):
            while arrivals[j] < t - w:
                j += 1
            peak = max(peak, (i - j + 1) / w)
    return {
        "sha256": sha256_file(path),
        "requests": len(reqs),
        "models": sorted(per_model),
        "per_model_requests": dict(sorted(per_model.items())),
        "per_model_prompt_tokens": dict(sorted(prompt.items())),
        "per_model_output_tokens": dict(sorted(output.items())),
        "prompt_tokens": sum(prompt.values()),
        "output_tokens": sum(output.values()),
        "arrival_span_s": span,
        "achieved_mean_arrival_rate_req_s": round(len(reqs) / span, 6) if span else None,
        "peak_10s_arrival_rate_req_s": round(peak, 6),
        "sequence_fingerprint": fingerprint(reqs),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workloads", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    files, missing = {}, []
    for kind in KINDS:
        for rate in RATES:
            for seed in SEEDS:
                name = f"{kind}_r{rate}_s{seed}.pkl"
                p = args.workloads / name
                if not p.is_file():
                    missing.append(name)
                    continue
                d = describe(p)
                d.update(workload=kind, rate=rate, seed=seed)
                files[name] = d
    if missing:
        print(f"FATAL: missing {len(missing)} traces: {missing}", file=sys.stderr)
        return 1

    # A bursty/steady pair must differ in arrival timing and nothing else.
    pair_problems = []
    for rate in RATES:
        for seed in SEEDS:
            b = files[f"bursty_r{rate}_s{seed}.pkl"]
            s = files[f"steady_r{rate}_s{seed}.pkl"]
            if b["per_model_requests"] != s["per_model_requests"]:
                pair_problems.append(f"r{rate}_s{seed}: per-model counts differ")
            if b["prompt_tokens"] != s["prompt_tokens"] or \
               b["output_tokens"] != s["output_tokens"]:
                pair_problems.append(f"r{rate}_s{seed}: token totals differ")
            if b["sha256"] == s["sha256"]:
                pair_problems.append(f"r{rate}_s{seed}: bursty and steady are identical")

    cfg = ROOT / "exp/configs/v4het"
    out = {
        "what_this_is": "the 20 canonical 4-model traces both arms run, frozen "
                        "before any benchmark; a mismatch stops the sweep",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "generator": "exp/scripts/build_paired_workload.py",
        "duration_s": 420,
        "model_set": json.loads((cfg / "model_revisions.json").read_text()),
        "slo_base": str(cfg / "slo_base.json"),
        "matrix": {"kinds": list(KINDS), "rates": list(RATES), "seeds": list(SEEDS)},
        "files": files,
        "pair_consistency": pair_problems or "bursty and steady differ in "
                                             "arrival timing only, as designed",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, default=str))
    for name in sorted(files):
        d = files[name]
        print(f"  {name:22s} {d['requests']:6d} reqs  span={d['arrival_span_s']:7.1f}s  "
              f"mean={d['achieved_mean_arrival_rate_req_s']:6.2f}/s  "
              f"peak10s={d['peak_10s_arrival_rate_req_s']:6.2f}/s  {d['sha256'][:12]}")
    if pair_problems:
        print("\nPAIR PROBLEMS:", *pair_problems, sep="\n  ", file=sys.stderr)
        return 1
    print(f"\n{len(files)}/20 traces frozen -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
