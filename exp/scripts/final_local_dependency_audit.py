#!/usr/bin/env python3
"""Find what this baseline needs that is not in the repository.

The failure mode this guards against is a baseline that runs here and nowhere
else, because something it depends on exists only on this machine: a path under
/workspace, a variable exported in a shell, a package installed by hand, a
model cache, a dataset. This walks the harness looking for those, checks
whether each one is actually present, and classifies it:

  in_repo          version-controlled; nothing to do
  reproducible     not in the repo, but a documented script rebuilds it
  machine_local    exists only here; must be documented or moved
  secret           must never be in the repo, name recorded in .env.example

It reports rather than repairs. Moving something into the repository is a
decision with consequences -- prompt text licensing, file size, secrets -- so
this produces the list and a person decides.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Absolute paths appearing in the harness are the main source of hidden state.
ABS_PATH = re.compile(r"(?<![\w/])(/(?:workspace|root|dev/shm|opt|venv)[\w./+-]*)")

SECRET_NAMES = ("HF_TOKEN", "PRISM_NTFY_TOPIC", "GITHUB_TOKEN", "GH_TOKEN",
                "HUGGING_FACE_HUB_TOKEN", "OPENAI_API_KEY")

# What each machine-local path is for, and how the next server gets it. Written
# from the setup surface that already exists, not invented here.
KNOWN = {
    "/workspace/prism-exp": (
        "reproducible",
        "the bootstrap install root; bootstrap.sh creates it, or set PRISM_ROOT "
        "to wherever the repository is checked out"),
    "/workspace/prism-exp/prism-venv": (
        "reproducible", "the pinned virtualenv; bootstrap.sh builds it from "
        "setup/pins.env and setup/requirements.lock.txt"),
    "/workspace/.hf_home": (
        "reproducible", "Hugging Face cache holding the six model snapshots; "
        "bootstrap.sh downloads them, HF_HOME points here"),
    "/workspace/datasets": (
        "reproducible", "ShareGPT source data; SETUP.md documents the download "
        "and exp/scripts/build_sharegpt_trace.py rebuilds the derived pickles"),
    "/workspace/.env": (
        "secret", "HF_TOKEN and PRISM_NTFY_TOPIC live here, outside the "
        "repository; see .env.example for the names"),
    "/root/.git-credentials": (
        "secret", "the GitHub push credential; never in the repository"),
    "/dev/shm": (
        "machine_local", "kvcached-v0 shares KV pages through POSIX shared "
        "memory; a crashed server leaves ipc_*_root segments that the next "
        "run must not inherit -- preflight checks for them"),
    "/workspace/prism-backups": (
        "machine_local", "off-repo artifact backup directory; set "
        "PRISM_BACKUP_DIR to relocate it"),
}


def classify(path, root):
    p = Path(path)
    for known, (kind, note) in KNOWN.items():
        if path == known or path.startswith(known + "/"):
            return kind, note
    try:
        p.relative_to(root)
        return "in_repo", "inside the repository"
    except ValueError:
        pass
    return "machine_local", "not classified; check before relying on it"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()
    root = args.root.resolve()

    found = {}
    scanned = 0
    for f in sorted((root / "exp/scripts").rglob("*")):
        if not f.is_file() or f.suffix not in (".sh", ".py"):
            continue
        scanned += 1
        try:
            text = f.read_text(errors="replace")
        except Exception:                               # noqa: BLE001
            continue
        for hit in ABS_PATH.findall(text):
            hit = hit.rstrip(".,;:'\")")
            found.setdefault(hit, set()).add(str(f.relative_to(root)))

    entries = []
    for path, users in sorted(found.items()):
        kind, note = classify(path, root)
        entries.append({
            "path": path, "kind": kind, "note": note,
            "exists_here": os.path.exists(path),
            "referenced_by": sorted(users)[:8],
            "reference_count": len(users),
        })

    env_needed = sorted({
        m for f in (root / "exp/scripts").rglob("*")
        if f.is_file() and f.suffix in (".sh", ".py")
        for m in re.findall(r"\b(PRISM_[A-Z0-9_]+|KVPR_[A-Z0-9_]+|HF_[A-Z_]+|"
                            r"FLASHINFER_[A-Z_]+|DATASETS|SHAREGPT_[A-Z]+)\b",
                            f.read_text(errors="replace"))
    })

    def has(cmd):
        return subprocess.run(["bash", "-lc", f"command -v {cmd}"],
                              capture_output=True).returncode == 0

    services = {
        "redis_server_on_path": has("redis-server"),
        "redis_reachable": subprocess.run(
            ["bash", "-lc", "redis-cli ping 2>/dev/null | grep -q PONG"],
            capture_output=True).returncode == 0,
        "tmux": has("tmux"),
        "nvidia_smi": has("nvidia-smi"),
        "git": has("git"),
        "curl": has("curl"),
    }

    stale_shm = sorted(p.name for p in Path("/dev/shm").glob("ipc_*")) \
        if Path("/dev/shm").is_dir() else []

    doc = {
        "scanned_harness_files": scanned,
        "absolute_path_dependencies": entries,
        "counts": {k: sum(1 for e in entries if e["kind"] == k)
                   for k in ("in_repo", "reproducible", "machine_local", "secret")},
        "environment_variables_referenced": env_needed,
        "secret_variable_names": list(SECRET_NAMES),
        "services": services,
        "stale_shared_memory_segments": stale_shm,
        "fd_limit_soft": subprocess.run(["bash", "-lc", "ulimit -Sn"],
                                        capture_output=True, text=True).stdout.strip(),
        "fd_limit_hard": subprocess.run(["bash", "-lc", "ulimit -Hn"],
                                        capture_output=True, text=True).stdout.strip(),
        "unclassified": [e["path"] for e in entries
                         if e["note"].startswith("not classified")],
    }
    args.out.write_text(json.dumps(doc, indent=2) + "\n")

    lines = ["# Machine-local dependencies", "",
             "What this baseline needs that git does not carry. Generated by "
             "`exp/scripts/final_local_dependency_audit.py`; do not edit by hand.",
             "", f"Harness files scanned: {scanned}", "",
             "| path | kind | present | used by | note |",
             "| --- | --- | --- | --- | --- |"]
    for e in entries:
        if e["kind"] == "in_repo":
            continue
        lines.append(f"| `{e['path']}` | {e['kind']} | "
                     f"{'yes' if e['exists_here'] else 'NO'} | "
                     f"{e['reference_count']} file(s) | {e['note']} |")
    lines += ["", "## Services", ""]
    for k, v in services.items():
        lines.append(f"- {k}: {'yes' if v else 'no'}")
    lines += ["", "## Environment variables referenced by the harness", "",
              ", ".join(f"`{v}`" for v in env_needed) or "none", "",
              "Secret values are never in the repository. `.env.example` "
              "records their names.", ""]
    if doc["unclassified"]:
        lines += ["## Unclassified -- resolve before handoff", ""]
        lines += [f"- `{p}`" for p in doc["unclassified"]] + [""]
    args.report.write_text("\n".join(lines))

    print(json.dumps(doc["counts"]))
    print(f"unclassified: {len(doc['unclassified'])}")
    # Unclassified machine-local paths are exactly the hidden state this is for.
    return 1 if doc["unclassified"] else 0


if __name__ == "__main__":
    sys.exit(main())
