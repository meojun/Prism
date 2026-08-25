#!/usr/bin/env python3
"""Check the 20 traces against the frozen manifest. Any mismatch is fatal."""
import argparse
import hashlib
import json
import sys
from pathlib import Path


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workloads", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    args = ap.parse_args()
    if not args.manifest.is_file():
        print(f"FATAL: no frozen manifest at {args.manifest}", file=sys.stderr)
        return 1
    want = json.loads(args.manifest.read_text())["files"]
    bad, absent, ok = [], [], 0
    for name, meta in sorted(want.items()):
        p = args.workloads / name
        if not p.is_file():
            absent.append(name)
        elif sha256_file(p) != meta["sha256"]:
            bad.append(name)
        else:
            ok += 1
            print(f"  OK    {name}")
    for n in absent:
        print(f"  ABSENT {n}", file=sys.stderr)
    for n in bad:
        print(f"  DIGEST MISMATCH {n}", file=sys.stderr)
    print(f"\n{ok}/{len(want)} workloads verified -> "
          f"{'PASS' if ok == len(want) else 'FAIL'}")
    return 0 if ok == len(want) else 1


if __name__ == "__main__":
    sys.exit(main())
