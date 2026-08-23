#!/usr/bin/env python3
"""The client file-descriptor gates.

cal-0p00035-s42 stopped with both GPUs at 0 % and no ownership invariant
violated. The cause was the benchmark client: 1,971 of its 1,972 send failures
were `OSError: [Errno 24] Too many open files`, so those requests never reached
the server at all. A tmux child inherits a soft nofile limit of 1024 against a
hard limit of 524288, and only the server's own launch raised it -- the client
ran in a shell that never did.

Every run in that pipeline shows thousands of these errors and every run of the
previous pipeline shows none, so the affected numbers measure the client's
limit, not tau. These gates make that impossible to miss: refuse to start under
a low limit, and invalidate any run whose client failed to connect even once.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
GATE = HERE.parent / "scripts" / "check_client_fd.py"
sys.path.insert(0, str(GATE.parent))

from check_client_fd import postrun, preflight, PATTERNS  # noqa: E402

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


def make_run(bench_text, limits=None):
    d = Path(tempfile.mkdtemp())
    (d / "server-logs").mkdir()
    (d / "server-logs" / "bench.log").write_text(bench_text)
    if limits is not None:
        (d / "client_fd_limits.json").write_text(json.dumps(limits))
    return d


# ------------------------------------------------------------------ postrun
def test_clean_run_is_valid():
    print("a clean client log is valid")
    d = make_run("Request model_1#1 arrives\nCompleted requests: 100\n",
                 {"proc_soft_nofile": 65535, "proc_hard_nofile": 524288})
    r = postrun(d, 65535)
    check("valid", r["valid"] is True)
    check("no failures counted", r["total"] == 0)
    check("the client's own limit was checked",
          r["client_process_limit_ok"] is True)


def test_one_fd_error_invalidates():
    print("a single 'Too many open files' invalidates the run")
    d = make_run("ok\nOSError: [Errno 24] Too many open files\nok\n",
                 {"proc_soft_nofile": 65535})
    r = postrun(d, 65535)
    check("invalid", r["valid"] is False)
    check("classified as fd exhaustion",
          r.get("verdict_class") == "INVALID_CLIENT_FD_EXHAUSTION")
    check("counted once each for the two patterns it matches",
          r["client_failures"]["too_many_open_files"] == 1
          and r["client_failures"]["errno_24"] == 1)
    check("the reason names the client", "client" in r["reason"])


def test_every_connection_failure_kind_is_caught():
    print("each client-side connection failure kind is caught")
    samples = {
        "too_many_open_files": "Too many open files",
        "errno_24": "[Errno 24]",
        "server_disconnected": "error: Server disconnected",
        "connection_reset": "Connection reset by peer",
        "connection_refused": "Cannot connect to host 127.0.0.1:42100",
        "client_os_error": "aiohttp.ClientOSError: boom",
    }
    for name, text in samples.items():
        r = postrun(make_run(f"fine\n{text}\nfine\n"), 65535)
        check(f"{name} invalidates", r["valid"] is False
              and r["client_failures"][name] >= 1)


def test_low_client_process_limit_invalidates_even_without_errors():
    print("a low limit on the client process invalidates a quiet run")
    d = make_run("Completed requests: 10\n", {"proc_soft_nofile": 1024})
    r = postrun(d, 65535)
    check("invalid", r["valid"] is False)
    check("limit flagged", r["client_process_limit_ok"] is False)
    check("reason names the limit", "nofile" in r["reason"])


def test_missing_limits_file_is_not_silently_valid():
    print("a run with no recorded limits still reports what it knows")
    r = postrun(make_run("Completed requests: 10\n"), 65535)
    check("no limits recorded", r["recorded_limits"] is None)
    check("limit check is unknown, not a false pass",
          r["client_process_limit_ok"] is None)


# --------------------------------------------------------- the real evidence
def test_the_real_seed42_run_is_invalid():
    """The preserved evidence, not the live directory: the contaminated runs
    were moved aside as .invalid1 when they were re-run clean."""
    print("the preserved seed_42 evidence is rejected")
    run = (HERE.parent / "results" / "final-evaluation" / "02-tau-calibration"
           / "raw" / "tau_0p00035" / "seed_42.invalid1")
    if not (run / "server-logs" / "bench.log").exists():
        check("seed_42 artifacts present", False)
        return
    r = postrun(run, 65535)
    check("seed_42 is invalid", r["valid"] is False)
    check("thousands of fd failures counted",
          r["client_failures"]["too_many_open_files"] > 1000)


def test_the_accepted_seed0_run_is_also_invalid():
    """The point accepted as PASS earlier is contaminated by the same fault."""
    print("the previously accepted seed_0 run is rejected too")
    run = (HERE.parent / "results" / "final-evaluation" / "02-tau-calibration"
           / "raw" / "tau_0p00035" / "seed_0.invalid1")
    if not (run / "server-logs" / "bench.log").exists():
        check("seed_0 artifacts present", False)
        return
    r = postrun(run, 65535)
    check("seed_0 is invalid", r["valid"] is False)
    check("its fd failures are counted",
          r["client_failures"]["too_many_open_files"] > 1000)


def test_the_clean_reruns_pass():
    """And the runs made after the repair must pass, or the gate is useless."""
    print("the repaired re-runs pass the gate")
    base = (HERE.parent / "results" / "final-evaluation" / "02-tau-calibration"
            / "raw" / "tau_0p00035")
    for name in ("seed_0", "seed_42"):
        run = base / name
        if not (run / "server-logs" / "bench.log").exists():
            check(f"{name} present", False)
            continue
        r = postrun(run, 65535)
        check(f"{name}: no descriptor exhaustion", r["fd_exhaustion"] == 0)
        check(f"{name}: no connection failures", r["connection_failures"] == 0)
        check(f"{name}: valid", r["valid"] is True)


def test_a_historical_clean_run_still_passes():
    """The gate must not reject runs that were genuinely healthy."""
    print("a historical run with no client failures still passes")
    base = HERE.parent / "results" / "final-overlap-pipeline"
    logs = sorted(base.rglob("server-logs/bench.log"))[:6]
    clean = [p.parent.parent for p in logs
             if "Too many open files" not in p.read_text(errors="ignore")]
    if not clean:
        check("a historical run without fd exhaustion exists", False)
        return
    r = postrun(clean[0], 65535)
    check("it has no descriptor exhaustion", r["fd_exhaustion"] == 0)
    # It is not otherwise clean: the previous pipeline's runs carry hundreds of
    # dropped connections. The gate must tell the two faults apart rather than
    # blaming the descriptor limit for both.
    check("its failures are classified as connection loss, not fd exhaustion",
          r["connection_failures"] > 0
          and r.get("verdict_class") == "INVALID_CLIENT_CONNECTION_FAILURES")


# ----------------------------------------------------------------- preflight
def test_preflight_measures_the_launch_path():
    print("preflight measures the shell the client actually runs in")
    r = preflight(65535)
    check("it reports the tmux child, not just the login shell",
          "tmux_child" in r and "login_shell" in r)
    check("it reports what a raise achieves there",
          "tmux_child_after_raise" in r)
    check("the verdict is on the raised limit",
          r["pass"] == (r["tmux_child_after_raise"]["soft"] == "unlimited"
                        or int(r["tmux_child_after_raise"]["soft"]) >= 65535))


def test_preflight_would_fail_an_unraisable_environment():
    print("preflight fails when the limit cannot be reached")
    r = preflight(10 ** 9)          # far above any hard limit here
    check("does not pass", r["pass"] is False)
    check("says why", "reason" in r)


def test_cli_exit_codes():
    print("the CLI exits non-zero on an invalid run")
    d = make_run("Too many open files\n")
    p = subprocess.run([sys.executable, str(GATE), "--mode", "postrun",
                        "--run", str(d)], capture_output=True, text=True)
    check("rc=1 for an invalid run", p.returncode == 1)
    d2 = make_run("all good\n", {"proc_soft_nofile": 65535})
    p2 = subprocess.run([sys.executable, str(GATE), "--mode", "postrun",
                         "--run", str(d2)], capture_output=True, text=True)
    check("rc=0 for a valid run", p2.returncode == 0)


def main():
    for fn in (
        test_clean_run_is_valid,
        test_one_fd_error_invalidates,
        test_every_connection_failure_kind_is_caught,
        test_low_client_process_limit_invalidates_even_without_errors,
        test_missing_limits_file_is_not_silently_valid,
        test_the_real_seed42_run_is_invalid,
        test_the_accepted_seed0_run_is_also_invalid,
        test_the_clean_reruns_pass,
        test_a_historical_clean_run_still_passes,
        test_preflight_measures_the_launch_path,
        test_preflight_would_fail_an_unraisable_environment,
        test_cli_exit_codes,
    ):
        fn()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for n in FAIL:
        print("  FAILED:", n)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
