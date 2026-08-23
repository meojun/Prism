#!/bin/bash
# Archive the raw evidence before the server is released.
#
# The per-request dumps and server logs are deliberately not in git, so if this
# machine goes away without an archive, the evidence behind every number goes
# with it. This produces one compressed archive per stage, records its sha256,
# and writes ARCHIVE_MANIFEST.json so the index can point at it.
#
# Heavy: it reads several GB. Run it only when no benchmark is running.
set -uo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
EVAL="$ROOT/exp/results/final-evaluation"
DEST=${PRISM_BACKUP_DIR:-/workspace/prism-backups}
PY=${PRISM_PY:-/workspace/prism-exp/prism-venv/bin/python}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$DEST"
cd "$ROOT"

if pgrep -f "sglang.launch_multi_model_server" >/dev/null 2>&1; then
  echo "FATAL: a server is running; archiving now would compete with it" >&2
  exit 1
fi

entries=""
for stage in 02-tau-calibration 04b-prototype-fresh 05-final-c; do
  [ -d "$EVAL/$stage" ] || continue
  out="$DEST/prism-final-${stage}-${STAMP}.tar.zst"
  echo "archiving $stage -> $out"
  if command -v zstd >/dev/null 2>&1; then
    tar -C "$EVAL" -cf - "$stage" | zstd -q -T0 -3 -o "$out" || { echo "FATAL: archive failed for $stage" >&2; exit 1; }
  else
    out="${out%.zst}.gz"
    tar -C "$EVAL" -czf "$out" "$stage" || { echo "FATAL: archive failed for $stage" >&2; exit 1; }
  fi
  entries="$entries $out"
done

$PY - "$EVAL/ARCHIVE_MANIFEST.json" "$STAMP" $entries <<'PY'
import hashlib, json, sys, datetime, os
out, stamp, *paths = sys.argv[1:]
def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()
archives = [{"archive": p, "sha256": sha(p), "size_bytes": os.path.getsize(p)}
            for p in paths]
json.dump({"created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "stamp": stamp,
           "note": ("raw per-request dumps and server logs, which are not in "
                    "git; verify with sha256sum before trusting a copy"),
           "archives": archives,
           "archive": archives[0]["archive"] if archives else None,
           "sha256": archives[0]["sha256"] if archives else None,
           "size_bytes": sum(a["size_bytes"] for a in archives)},
          open(out, "w"), indent=2)
print(f"wrote {out}: {len(archives)} archive(s), "
      f"{sum(a['size_bytes'] for a in archives)/1e9:.2f} GB")
PY
