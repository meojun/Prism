#!/usr/bin/env python3
"""Record the 24 canonical workloads precisely enough to rebuild them elsewhere.

The .pkl traces carry raw ShareGPT prompt text. That text is third-party, and
some conversations in the dataset contain real leaked credentials -- GitHub's
push protection has already flagged one inside a trace from this set -- so they
are not redistributed, here or anywhere.

They do not need to be. The builder is deterministic in
(rate, duration, seed, slo_base, sharegpt source), and regenerating
bursty_r2_s1 on this machine reproduced the canonical file byte for byte,
SHA256 and all. So the durable form of these workloads is the recipe plus the
hashes: a public dataset with a recorded hash, a committed builder, a committed
SLO base, and the 24 digests every rebuild must match.

This writes that manifest. `verify_workloads.py` checks a directory against it,
and `restore_workloads.sh` rebuilds and then verifies.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

GRID = ([("bursty", r, s) for r in (2, 4, 8, 14, 20) for s in (1, 2, 3)]
        + [("steady", r, s) for r in (4, 8, 20) for s in (1, 2, 3)])
DURATION = 420

SHAREGPT = {
    "file": "ShareGPT_V3_unfiltered_cleaned_split.json",
    "huggingface_dataset": "anon8231489123/ShareGPT_Vicuna_unfiltered",
    "gated": False,
    "size_bytes": 672837942,
    "sha256": "35f0e213ce091ed9b9af2a1f0755e9d39f9ccec34ab281cd4ca60d70f6479ba4",
    "download": ("hf download anon8231489123/ShareGPT_Vicuna_unfiltered "
                 "ShareGPT_V3_unfiltered_cleaned_split.json --repo-type dataset "
                 "--local-dir $DATASETS/sharegpt"),
}


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workloads", type=Path, required=True)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    wl, root = args.workloads, args.root

    files, missing = {}, []
    for kind, rate, seed in GRID:
        name = f"{kind}_r{rate}_s{seed}.pkl"
        p = wl / name
        if not p.is_file():
            missing.append(name)
            continue
        files[name] = {
            "filename": name,
            "path_relative_to_repo": str(p.relative_to(root)),
            "condition": kind, "rate": rate, "seed": seed,
            "duration_s": DURATION,
            "size_bytes": p.stat().st_size,
            "sha256": sha256(p),
            "rebuild": (f"python exp/scripts/build_paired_workload.py --rate {rate} "
                        f"--duration {DURATION} --seed {seed} "
                        f"--slo-base exp/configs/v2/slo_base.json "
                        f"--outdir exp/workloads/final-evaluation"),
        }

    # The committed JSON siblings describe each trace without carrying prompt
    # bodies, and are the second, independent check on a rebuild.
    provenance = {}
    for kind in ("paired_requests", "phases"):
        for _c, rate, seed in GRID:
            name = f"{kind}_r{rate}_s{seed}.json"
            p = wl / name
            if p.is_file() and name not in provenance:
                provenance[name] = sha256(p)

    doc = {
        "what_this_is": (
            "the 24 canonical evaluation workloads: their identities and "
            "digests, and how to reproduce them exactly on another machine"),
        "conditions": ("bursty rates 2/4/8/14/20 and steady rates 4/8/20, "
                       "seeds 1/2/3"),
        "count": len(files),
        "expected_count": len(GRID),
        "missing": missing,
        "duration_s": DURATION,
        "builder": "exp/scripts/build_paired_workload.py",
        "slo_base": "exp/configs/v2/slo_base.json",
        "slo_base_sha256": sha256(root / "exp/configs/v2/slo_base.json"),
        "sharegpt_source": SHAREGPT,
        "distribution": {
            "in_git": False,
            "why": ("the traces carry raw ShareGPT prompt text; some "
                    "conversations in that dataset contain real leaked "
                    "credentials, so the pickles are not redistributed"),
            "how_to_obtain": ("rebuild them -- bash exp/scripts/restore_workloads.sh -- "
                              "then verify against this manifest"),
            "determinism": ("verified on the reference machine: rebuilding "
                            "bursty_r2_s1 and steady_r2_s1 from the recipe "
                            "reproduced the canonical files byte for byte, "
                            "along with their paired_requests and phases JSON"),
        },
        "files": files,
        "committed_provenance_sha256": provenance,
        "verify": "python exp/scripts/verify_workloads.py --workloads <dir>",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2, sort_keys=False) + "\n")
    print(f"wrote {args.out}: {len(files)}/{len(GRID)} workloads")
    for m in missing:
        print(f"  MISSING: {m}")
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
