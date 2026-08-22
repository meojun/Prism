#!/usr/bin/env python3
"""D3 gate: does Algorithm 2 still hold its ordering while models migrate?

D2 showed migration correct with Algorithm 2 off; the steady8 D1 run showed
Algorithm 2 correct with migration off. This checks the interaction: with both
on, the global schedule must still be the order in which requests are admitted
to prefill, across model processes on the same GPU, while models are moving
between GPUs underneath.

The runtime already fails closed -- an out-of-order backend admission or
prefill start raises and sets the scheduler's shutdown event. This script
verifies that from the evidence rather than from the absence of a crash, and
covers the rest of the interaction list: outstanding accounting, stale
sequence tokens, admission across a migration, deadlock, request loss,
migration-induced aborts, and shutdown state.
"""

import argparse
import json
import re
from pathlib import Path

FATAL_PATTERNS = [
    "cuMemCreate", "OutOfMemoryError", "CUDA out of memory",
    "CUDA error: out of memory", "failed in CUDA driver",
    "NCCL error", "ncclUnhandledCudaError", "Segmentation fault",
]
VIOLATION_PATTERNS = [
    "Algorithm-2 backend admission order violation",
    "Algorithm-2 prefill start order violation",
    "order violation",
]


def read(path):
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def marked(text, marker):
    out = []
    for line in text.splitlines():
        if marker in line:
            try:
                out.append(json.loads(line.split(marker, 1)[1]))
            except (IndexError, json.JSONDecodeError):
                pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    run = args.run
    logs = run / "server-logs"
    scheduler_text = "".join(
        read(p) for p in sorted(logs.glob("*gpu_scheduler*.log")))
    server_text = read(logs / "server.log")
    stdout_text = read(logs / "stdout.log")

    checks = []

    def record(name, ok, detail):
        checks.append({"check": name, "pass": bool(ok), "detail": detail})

    # ---- 1. Algorithm 2 actually ran -------------------------------------
    events = marked(scheduler_text, "[PAPER-ALG2-RUNTIME] ")
    alg2_lines = scheduler_text.count("[PAPER-ALG2]")
    record(
        "algorithm2_ran",
        bool(events) or alg2_lines > 0,
        {"runtime_events": len(events), "alg2_log_lines": alg2_lines},
    )

    # ---- 2. every ordering decision was in order -------------------------
    violations = [e for e in events if not e.get("order_ok")]
    record(
        "no_alg2_order_violation",
        not violations,
        {"events": len(events), "violations": violations[:5]},
    )

    raised = [line for line in (scheduler_text + server_text).splitlines()
              if any(p in line for p in VIOLATION_PATTERNS)]
    record(
        "runtime_raised_no_ordering_error",
        not raised,
        {"count": len(raised), "lines": raised[:3]},
    )

    # ---- 3. sequence tokens are monotonic and gapless per GPU ------------
    per_gpu = {}
    for event in events:
        seq = event.get("alg2_seq")
        if seq is None:
            continue
        per_gpu.setdefault(event.get("gpu_id"), {}).setdefault(
            event.get("event"), []).append(seq)

    stale, gaps = [], []
    for gpu, by_event in per_gpu.items():
        for event_name, seqs in by_event.items():
            if event_name not in ("backend_admit", "prefill_start"):
                continue
            if any(b <= a for a, b in zip(seqs, seqs[1:])):
                stale.append({"gpu": gpu, "event": event_name,
                              "not_increasing": True})
            missing = [b for a, b in zip(seqs, seqs[1:]) if b != a + 1]
            if missing:
                gaps.append({"gpu": gpu, "event": event_name,
                             "non_consecutive": len(missing)})
    record(
        "sequence_tokens_are_monotonic",
        not stale,
        {"per_gpu": {g: {e: len(s) for e, s in d.items()}
                     for g, d in per_gpu.items()}, "stale": stale},
    )
    record(
        "sequence_tokens_have_no_gaps",
        not gaps,
        {"gaps": gaps},
    )

    # ---- 4. admission stayed ordered across every migration --------------
    controller = read(logs / "server.log.global_controller.log")
    windows = []
    for line in controller.splitlines():
        if "[PAPER-ALG1-V4] " not in line:
            continue
        try:
            rec = json.loads(line.split("[PAPER-ALG1-V4] ", 1)[1])
        except (IndexError, json.JSONDecodeError):
            continue
        if rec.get("migration_decision") == "MIGRATE":
            windows.append(rec["timestamp"])

    during = []
    for start in windows:
        end = start + 60.0        # a migration's whole timeline fits inside this
        during.extend([
            e for e in events
            if e.get("event_time") and start <= e["event_time"] <= end
        ])
    record(
        "admission_ordered_across_migrations",
        all(e.get("order_ok") for e in during),
        {"migrations": len(windows),
         "admission_events_during_migrations": len(during),
         "violations": [e for e in during if not e.get("order_ok")][:5]},
    )

    # ---- 5. outstanding work was fully retired ---------------------------
    outstanding = {}
    for event in events:
        gpu = event.get("gpu_id")
        name = event.get("event")
        if name == "dispatch":
            outstanding[gpu] = outstanding.get(gpu, 0) + 1
        elif name in ("prefill_complete", "complete"):
            outstanding[gpu] = outstanding.get(gpu, 0) - 1
    reported = re.findall(r"outstanding at shutdown[^\n]*", scheduler_text)
    record(
        "outstanding_work_retired",
        all(v <= 0 for v in outstanding.values()) if outstanding else True,
        {"net_by_gpu": outstanding, "shutdown_lines": reported[:4]},
    )

    # ---- 6. the run neither deadlocked nor lost anything ------------------
    rc = read(run / "pipeline.rc").strip()
    status = {}
    try:
        status = json.loads(read(run / "monitor" / "status.json"))
    except json.JSONDecodeError:
        pass
    record("pipeline_rc_zero", rc == "0", {"pipeline_rc": rc or None})
    record(
        "no_deadlock",
        status.get("state") == "COMPLETE",
        {"watchdog_state": status.get("state"),
         "failure_reason": status.get("failure_reason")},
    )

    results = sorted(run.glob("*_e2e_*rep.json"))
    summary = {}
    if results:
        try:
            summary = json.loads(read(results[-1]))
        except json.JSONDecodeError:
            pass
    completed = summary.get("completed")
    aborted = summary.get("aborted")
    offered = None
    arrivals = re.findall(r"^Request .* arrives", read(logs / "bench.log"),
                          re.MULTILINE)
    if arrivals:
        offered = len(arrivals)
    accounted = (completed or 0) + (aborted or 0)
    record(
        "no_request_loss",
        offered is None or accounted == offered,
        {"offered": offered, "completed": completed, "aborted": aborted,
         "accounted": accounted},
    )

    induced = server_text.count("migration-induced abort")
    record("no_migration_induced_abort", induced == 0, {"count": induced})

    fatal = [line for line in (server_text + stdout_text).splitlines()
             if any(p in line for p in FATAL_PATTERNS)]
    record("no_fatal_cuda_or_nccl", not fatal,
           {"count": len(fatal), "lines": [f[:200] for f in fatal[:3]]})

    verdict = "PASS" if all(c["pass"] for c in checks) else "FAIL"
    report = {"run": str(run), "verdict": verdict, "checks": checks,
              "benchmark_summary": {
                  k: summary.get(k) for k in
                  ("completed", "aborted", "request_throughput",
                   "mean_ttft_ms", "p99_ttft_ms", "mean_tpot_ms",
                   "p99_tpot_ms") if k in summary}}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    for check in checks:
        print(f"  {'PASS' if check['pass'] else 'FAIL'}  {check['check']}")
        if not check["pass"]:
            print(f"        {json.dumps(check['detail'])[:500]}")
    print(f"\nVERDICT: {verdict}  ({args.out})")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
