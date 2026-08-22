#!/usr/bin/env python3
"""Where the raw benchmark artifacts live, for the ones Git does not carry."""
import argparse, json
from datetime import datetime, timezone
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--eval-dir", type=Path, required=True)
ap.add_argument("--out", type=Path, required=True)
a = ap.parse_args()

runs = []
for result in sorted(a.eval_dir.rglob("*_e2e_*rep.json")):
    run = result.parent
    size = sum(f.stat().st_size for f in run.rglob("*") if f.is_file())
    runs.append({"run": str(run), "result_json": result.name,
                 "bytes_on_disk": size,
                 "rc": (run / "pipeline.rc").read_text().strip()
                 if (run / "pipeline.rc").exists() else None})
a.out.write_text(json.dumps({
    "generated_utc": datetime.now(timezone.utc).isoformat(),
    "note": ("Raw server logs, request dumps and traces are kept on this "
             "machine and are gitignored; the reports, freezes and hashes are "
             "in Git. These paths say where the raw evidence is."),
    "host_root": str(a.eval_dir), "runs": runs,
    "total_bytes": sum(r["bytes_on_disk"] for r in runs),
}, indent=2) + "\n")
print(f"{len(runs)} runs recorded")
