#!/usr/bin/env python3
"""Client file-descriptor gates, before and after a benchmark run.

The tau=0.00035 seed-42 run failed because the benchmark client ran out of
descriptors: 1,971 of its 1,972 send failures were OSError Errno 24, and the
requests never reached the server at all. A tmux child inherits a soft nofile
limit of 1024 while the hard limit is 524288, and only the server's launch
raised its own. Every run in that pipeline shows thousands of these errors and
every run of the previous pipeline shows none, so the numbers measured the
client's limit rather than tau.

Two gates, so that cannot happen silently again:

  preflight  a shell launched the way runs are launched must be able to reach
             the required limit -- otherwise the experiment does not start
  postrun    one 'Too many open files' or connection failure in the client log
             invalidates the run
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REQUIRED = 65535

# Client-side failures that mean requests never reached the server, or that
# their responses were lost. Any of them makes the measurement meaningless.
# Two classes, reported separately because they mean different things.
#
# FD exhaustion is unambiguously this harness's fault: the client could not open
# a socket at all, so the request never reached the server.
FD_PATTERNS = [
    ("too_many_open_files", r"Too many open files"),
    ("errno_24", r"\[Errno 24\]"),
]
# Connection failures mean a request was sent but its response was lost. The
# historical pipeline's runs show hundreds of these with zero fd exhaustion, so
# they are NOT the same fault and the counts are kept apart -- but either one
# means the client did not collect what the server produced.
CONN_PATTERNS = [
    ("server_disconnected", r"Server disconnected"),
    ("connection_reset", r"Connection reset by peer"),
    ("connection_refused", r"Cannot connect to host|Connection refused"),
    ("client_os_error", r"ClientOSError|ClientConnectorError|ServerDisconnectedError"),
]
PATTERNS = FD_PATTERNS + CONN_PATTERNS


def preflight(required):
    """Can a shell started the way a run is started reach the limit?"""
    out = {"required": required}
    soft, hard = _shell_limits(["bash", "-lc", "ulimit -Sn; ulimit -Hn"])
    out["login_shell"] = {"soft": soft, "hard": hard}
    # The path that actually matters: a tmux child, which is where the
    # benchmark's shell lives.
    t_soft, t_hard = _shell_limits(
        ["bash", "-c",
         "tmux new-session -d -s _fdcheck 'sh -c \"ulimit -Sn; ulimit -Hn\" "
         "> /tmp/_fdcheck.txt 2>&1; sleep 1'; sleep 2; "
         "tmux kill-session -t _fdcheck 2>/dev/null; cat /tmp/_fdcheck.txt"])
    out["tmux_child"] = {"soft": t_soft, "hard": t_hard}
    # A raise inside such a child is what run_v4_case.sh now does.
    r_soft, _ = _shell_limits(
        ["bash", "-c",
         f"tmux new-session -d -s _fdraise 'sh -c \"ulimit -n {required} "
         f"2>/dev/null; ulimit -Sn; ulimit -Hn\" > /tmp/_fdraise.txt 2>&1; sleep 1'; "
         "sleep 2; tmux kill-session -t _fdraise 2>/dev/null; cat /tmp/_fdraise.txt"])
    out["tmux_child_after_raise"] = {"soft": r_soft}
    ok = _at_least(r_soft, required)
    out["pass"] = bool(ok)
    if not ok:
        out["reason"] = (
            f"a tmux-launched shell reaches only {r_soft} after raising to "
            f"{required}; the benchmark client would run under that limit")
    return out


def _shell_limits(cmd):
    try:
        txt = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=60).stdout.split()
    except Exception:
        return None, None
    vals = [v for v in txt if v == "unlimited" or v.isdigit()]
    return (vals[-2], vals[-1]) if len(vals) >= 2 else (
        (vals[0], None) if vals else (None, None))


def _at_least(value, required):
    if value is None:
        return False
    if value == "unlimited":
        return True
    try:
        return int(value) >= required
    except ValueError:
        return False


def postrun(run, required):
    """Any client-side connection failure invalidates the run."""
    run = Path(run)
    bench = run / "server-logs" / "bench.log"
    text = bench.read_text(errors="ignore") if bench.exists() else ""
    counts = {name: len(re.findall(pat, text)) for name, pat in PATTERNS}
    fd_total = sum(counts[n] for n, _ in FD_PATTERNS)
    conn_total = sum(counts[n] for n, _ in CONN_PATTERNS)
    total = fd_total + conn_total
    out = {"run": str(run), "client_failures": counts,
           "fd_exhaustion": fd_total, "connection_failures": conn_total,
           "total": total}

    limits = run / "client_fd_limits.json"
    if limits.exists():
        rec = json.loads(limits.read_text())
        out["recorded_limits"] = rec
        proc_soft = rec.get("proc_soft_nofile")
        out["client_process_limit_ok"] = _at_least(
            str(proc_soft) if proc_soft is not None else None, required)
    else:
        out["recorded_limits"] = None
        out["client_process_limit_ok"] = None

    out["valid"] = bool(total == 0 and out.get("client_process_limit_ok") is not False)
    if fd_total:
        out["verdict_class"] = "INVALID_CLIENT_FD_EXHAUSTION"
        out["reason"] = (
            f"{fd_total} client descriptor exhaustion failures; those requests "
            f"never reached the server, so the run measured the client")
    elif conn_total:
        out["verdict_class"] = "INVALID_CLIENT_CONNECTION_FAILURES"
        out["reason"] = (
            f"{conn_total} client connection failures with no descriptor "
            f"exhaustion; responses the server produced were not collected")
    elif out.get("client_process_limit_ok") is False:
        out["reason"] = "the benchmark process ran under an insufficient nofile limit"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["preflight", "postrun"], required=True)
    ap.add_argument("--run")
    ap.add_argument("--required", type=int, default=REQUIRED)
    ap.add_argument("--out")
    a = ap.parse_args()

    report = (preflight(a.required) if a.mode == "preflight"
              else postrun(a.run, a.required))
    text = json.dumps(report, indent=2, sort_keys=True)
    print(text)
    if a.out:
        Path(a.out).write_text(text + "\n")
    return 0 if report.get("pass", report.get("valid")) else 1


if __name__ == "__main__":
    sys.exit(main())
