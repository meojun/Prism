#!/bin/bash
# Preserve the evidence that cannot be regenerated, somewhere it will survive
# this server.
#
# This instance has no host volume -- vast-capabilities reports
# workspace_is_volume=false -- so /workspace dies with the server and is not a
# backup. The only durable store reachable from here is the git remote, so the
# archive is sized to belong there: server logs, scheduler traces, monitor
# heartbeats, per-run verification and the derived metrics for every run.
#
# The per-request dumps (*_output_requests.json, ~950 MB) are deliberately
# excluded. They carry ShareGPT prompt text -- the same third-party content,
# including real leaked credentials, that keeps the workload pickles out of
# this repository. Every number derived from them is preserved in the
# calibration manifest and in each run's own metrics.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
EVAL="$ROOT/exp/results/final-evaluation"
OUT="$ROOT/exp/final-handoff/evidence"
STAMP=${1:-$(date -u +%Y%m%dT%H%M%SZ)}
cd "$ROOT"
mkdir -p "$OUT"

if pgrep -f 'sglang[.]launch_multi_model_server' | grep -qv pgrep 2>/dev/null; then
  echo "FATAL: a server is running; archiving now would compete with it" >&2
  exit 1
fi

ARCHIVE="$OUT/prism-final-evidence-${STAMP}.tar.zst"
echo "[archive] building $ARCHIVE"
tar -C "$EVAL" --exclude="*_output_requests.json" -cf - \
  02-tau-calibration 04b-prototype-fresh 01-ci-profile 00-preflight \
  03-readiness 03b-fairness 2>/dev/null \
  | zstd -q -T0 -9 -o "$ARCHIVE" || { echo "FATAL: archive failed" >&2; exit 1; }

python3 - "$ARCHIVE" "$ROOT/exp/final-handoff/evidence_manifest.json" "$EVAL" <<'PY'
import hashlib, json, os, subprocess, sys, datetime
archive, out, ev = sys.argv[1:4]

def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()

listing = subprocess.run(["bash", "-lc", f"zstd -dc '{archive}' | tar -tf - | head -400"],
                         capture_output=True, text=True).stdout.splitlines()
excluded = subprocess.run(
    ["bash", "-lc", f"find '{ev}' -name '*_output_requests.json' | wc -l"],
    capture_output=True, text=True).stdout.strip()
doc = {
    "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "archive": os.path.relpath(archive, os.path.dirname(os.path.dirname(os.path.dirname(archive)))),
    "archive_absolute": archive,
    "sha256": sha(archive),
    "size_bytes": os.path.getsize(archive),
    "durable_location": ("committed to this git repository and pushed to the "
                         "remote; this instance has no host volume, so nothing "
                         "under /workspace survives its release"),
    "contains": ["calibration: 12 runs, all artifacts except per-request dumps",
                 "prototype: bursty r2 s1 (PASS) and the preserved attempts",
                 "c_i profile and its provenance",
                 "preflight, readiness and fairness stage records",
                 "per-run VERIFICATION.json and ALG2_INTERACTION.json",
                 "server, scheduler, controller and model-service logs",
                 "monitor heartbeats and kill audits",
                 "weight and KV migration traces"],
    "excluded": {
        "what": "*_output_requests.json",
        "count": int(excluded or 0),
        "size": "~950 MB",
        "why": ("they carry raw ShareGPT prompt text -- third-party content "
                "that includes real leaked credentials -- which is why the "
                "workload pickles are not distributed either"),
        "impact": ("none for resuming: every number derived from them is in "
                   "exp/final-handoff/calibration_manifest.json and in each "
                   "run's own metrics. They are not recoverable after this "
                   "server is released."),
    },
    "verify": f"sha256sum {os.path.basename(archive)}",
    "extract": f"zstd -dc {os.path.basename(archive)} | tar -xf - -C <dir>",
    "entries_sample": listing[:40],
}
open(out, "w").write(json.dumps(doc, indent=2) + "\n")
print(f"[archive] {doc['size_bytes']/1e6:.1f} MB, sha256 {doc['sha256'][:16]}...")
print(f"[archive] manifest: {out}")
PY
