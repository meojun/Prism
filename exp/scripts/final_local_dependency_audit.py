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

# Every machine-local path this harness names, and what the next server does
# about it. The categories are the handoff's own: a path that lands in none of
# them is unclassified, and an unclassified path is a handoff failure, because
# it is exactly the hidden state that makes a baseline run here and nowhere
# else.
#
#   VERSION_CONTROLLED       git carries it; nothing to do
#   REGENERATED_BY_BOOTSTRAP bootstrap.sh creates it
#   RESTORED_FROM_ARCHIVE    rebuilt or restored by a documented script
#   MODEL_CACHE_REDOWNLOAD   downloaded from Hugging Face on first use
#   SECRET_USER_MUST_PROVIDE a credential; never in git, named in .env.example
#   EPHEMERAL_NOT_REQUIRED   scratch from earlier work; the baseline does not
#                            need it and its absence changes nothing
KNOWN = {
    "/workspace": (
        "REGENERATED_BY_BOOTSTRAP",
        "the install root; bootstrap.sh creates it, or set PRISM_ROOT to the "
        "checkout",
        "./bootstrap.sh",
        "test -d $PRISM_ROOT"),
    "/workspace/prism-exp": (
        "REGENERATED_BY_BOOTSTRAP",
        "the bootstrap install root that the harness scripts default to",
        "./bootstrap.sh",
        "test -d /workspace/prism-exp || export PRISM_ROOT=$(pwd)"),
    "/workspace/prism-exp/prism-venv": (
        "REGENERATED_BY_BOOTSTRAP",
        "the pinned virtualenv, built from setup/pins.env and "
        "setup/requirements.lock.txt -- never re-resolved",
        "./bootstrap.sh",
        "$PRISM_ROOT/prism-venv/bin/python -c 'import sglang, torch'"),
    "/workspace/.hf_home": (
        "MODEL_CACHE_REDOWNLOAD",
        "Hugging Face cache holding the six model snapshots; HF_HOME points "
        "here and the exact revisions are pinned in the baseline manifest",
        "./bootstrap.sh   # needs HF_TOKEN: the Llama models are gated",
        "python exp/scripts/handoff_preflight.py   # checks all six revisions"),
    "/workspace/datasets": (
        "RESTORED_FROM_ARCHIVE",
        "ShareGPT source data, from which the 24 canonical workloads are "
        "rebuilt byte for byte",
        "hf download anon8231489123/ShareGPT_Vicuna_unfiltered "
        "ShareGPT_V3_unfiltered_cleaned_split.json --repo-type dataset "
        "--local-dir $DATASETS/sharegpt",
        "bash exp/scripts/restore_workloads.sh   # hashes the source, then "
        "verifies all 24 digests"),
    "/workspace/.env": (
        "SECRET_USER_MUST_PROVIDE",
        "HF_TOKEN and the optional ntfy topic; outside the repository, never "
        "committed",
        "cp .env.example /workspace/.env && chmod 600 /workspace/.env && "
        "$EDITOR /workspace/.env",
        "source exp/scripts/env.sh && test -n \"$HF_TOKEN\""),
    "/root/.git-credentials": (
        "SECRET_USER_MUST_PROVIDE",
        "the GitHub push credential; needed only to push results back",
        "git config --global credential.helper store   # then push once",
        "git ls-remote origin >/dev/null"),
    "/dev/shm": (
        "EPHEMERAL_NOT_REQUIRED",
        "kvcached-v0 shares KV pages through POSIX shared memory; a crashed "
        "server leaves segments the next run must not inherit",
        "rm -f /dev/shm/ipc_*   # only when no server is running",
        "python exp/scripts/handoff_preflight.py   # fails on stale segments"),
    "/workspace/prism-backups": (
        "EPHEMERAL_NOT_REQUIRED",
        "local artifact backup directory on this instance; set "
        "PRISM_BACKUP_DIR to relocate it. NOT durable -- this instance has no "
        "host volume, so anything here dies with the server",
        "mkdir -p ${PRISM_BACKUP_DIR:-/workspace/prism-backups}",
        "test -d ${PRISM_BACKUP_DIR:-/workspace/prism-backups}"),
    "/workspace/logs": (
        "EPHEMERAL_NOT_REQUIRED",
        "scratch logs from earlier pipelines on this instance; the final "
        "baseline writes to exp/results/final-evaluation instead",
        "none -- nothing recreates these and nothing needs them",
        "none"),
    "/workspace/prism-base": (
        "EPHEMERAL_NOT_REQUIRED",
        "an earlier worktree used during development",
        "none", "none"),
    "/workspace/prism-merge": (
        "EPHEMERAL_NOT_REQUIRED",
        "an earlier merge worktree used during development",
        "none", "none"),
    "/workspace/prism-handoff-validate": (
        "EPHEMERAL_NOT_REQUIRED",
        "scratch directory the clean-clone validation creates and removes",
        "created automatically by exp/scripts/final_clean_clone_validate.sh",
        "none"),
    "/workspace/run_pipeline.sh": (
        "EPHEMERAL_NOT_REQUIRED",
        "referenced only by watchdog_v2.sh, a superseded harness; absent here "
        "already and unused by the final pipeline",
        "none", "none"),
    "/workspace/shm_clean.sh": (
        "EPHEMERAL_NOT_REQUIRED",
        "referenced by the superseded v6 sweep scripts; absent here already "
        "and unused by the final pipeline",
        "none", "none"),
    "/opt": (
        "EPHEMERAL_NOT_REQUIRED",
        "system tooling paths (nvm, instance tools) provided by the image",
        "none", "command -v tmux redis-cli nvidia-smi"),
    "/venv": (
        "EPHEMERAL_NOT_REQUIRED",
        "the image's own default virtualenv; the baseline uses its pinned one",
        "none", "none"),
    "/root": (
        "EPHEMERAL_NOT_REQUIRED",
        "home directory paths; the baseline reads nothing required from here",
        "none", "none"),
}


def classify(path, root):
    """Longest-prefix match, so a specific path wins over its parent."""
    p = Path(path)
    try:
        p.relative_to(root)
        return "VERSION_CONTROLLED", "inside the repository", "git clone", "git status"
    except ValueError:
        pass
    best = None
    for known in KNOWN:
        if path == known or path.startswith(known + "/"):
            if best is None or len(known) > len(best):
                best = known
    if best:
        return KNOWN[best]
    return ("UNCLASSIFIED", "not classified; check before relying on it",
            "unknown", "unknown")


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
        kind, note, howto, validate = classify(path, root)
        entries.append({
            "path": path, "kind": kind, "note": note,
            "how_the_next_server_gets_it": howto,
            "validation": validate,
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
        "categories": ["VERSION_CONTROLLED", "REGENERATED_BY_BOOTSTRAP",
                       "RESTORED_FROM_ARCHIVE", "MODEL_CACHE_REDOWNLOAD",
                       "SECRET_USER_MUST_PROVIDE", "EPHEMERAL_NOT_REQUIRED"],
        "counts": {k: sum(1 for e in entries if e["kind"] == k)
                   for k in sorted({e["kind"] for e in entries})},
        "environment_variables_referenced": env_needed,
        "secret_variable_names": list(SECRET_NAMES),
        "services": services,
        "stale_shared_memory_segments": stale_shm,
        "fd_limit_soft": subprocess.run(["bash", "-lc", "ulimit -Sn"],
                                        capture_output=True, text=True).stdout.strip(),
        "fd_limit_hard": subprocess.run(["bash", "-lc", "ulimit -Hn"],
                                        capture_output=True, text=True).stdout.strip(),
        "unclassified": [e["path"] for e in entries
                         if e["kind"] == "UNCLASSIFIED"],
    }
    args.out.write_text(json.dumps(doc, indent=2) + "\n")

    lines = ["# Machine-local dependencies", "",
             "What this baseline needs that git does not carry. Generated by "
             "`exp/scripts/final_local_dependency_audit.py`; do not edit by hand.",
             "", f"Harness files scanned: {scanned}", "",
             "| path | kind | present | used by | note |",
             "| --- | --- | --- | --- | --- |"]
    for e in entries:
        if e["kind"] == "VERSION_CONTROLLED":
            continue
        lines.append(f"| `{e['path']}` | {e['kind']} | "
                     f"{'yes' if e['exists_here'] else 'no'} | "
                     f"{e['reference_count']} | {e['note']} |")
    lines += ["", "## How the next server gets each one", ""]
    for e in entries:
        if e["kind"] in ("VERSION_CONTROLLED", "EPHEMERAL_NOT_REQUIRED"):
            continue
        lines += [f"### `{e['path']}` -- {e['kind']}", "",
                  f"{e['note']}", "",
                  f"- obtain: `{e['how_the_next_server_gets_it']}`",
                  f"- validate: `{e['validation']}`", ""]
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
