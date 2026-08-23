#!/usr/bin/env python3
"""Check a workload directory against the canonical manifest, file by file.

Exits non-zero unless all 24 traces are present and every SHA256 matches. This
is what proves the next server is running the same work as this one, and the
evaluation must not start without it.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workloads", type=Path, required=True)
    ap.add_argument("--manifest", type=Path,
                    default=Path("exp/final-handoff/workloads_manifest.json"))
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    m = json.loads(args.manifest.read_text())
    results, missing, mismatched = [], [], []
    for name, spec in sorted(m["files"].items()):
        p = args.workloads / name
        if not p.is_file():
            missing.append(name)
            results.append({"file": name, "verdict": "MISSING"})
            continue
        got = sha256(p)
        ok = got == spec["sha256"]
        if not ok:
            mismatched.append(name)
        results.append({"file": name, "verdict": "OK" if ok else "MISMATCH",
                        "expected": spec["sha256"], "got": got,
                        "size_bytes": p.stat().st_size})

    verdict = "PASS" if not missing and not mismatched else "FAIL"
    doc = {"verdict": verdict, "workload_dir": str(args.workloads),
           "checked": len(results), "expected": m["expected_count"],
           "missing": missing, "mismatched": mismatched, "files": results}
    if args.out:
        args.out.write_text(json.dumps(doc, indent=2) + "\n")

    for r in results:
        mark = "OK  " if r["verdict"] == "OK" else r["verdict"]
        print(f"  {mark}  {r['file']}")
    print(f"\n{len(results) - len(missing) - len(mismatched)}/{m['expected_count']} "
          f"workloads verified -> {verdict}")
    if missing:
        print(f"  missing: {', '.join(missing)}")
    if mismatched:
        print(f"  MISMATCHED (not the canonical workload): {', '.join(mismatched)}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
