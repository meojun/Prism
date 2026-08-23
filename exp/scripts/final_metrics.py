#!/usr/bin/env python3
"""Everything one benchmark run is asked to report, read from its own artifacts.

Shared by tau selection and the final aggregation so both read a run the same
way. Migration counts come from the weight-transfer records, not the policy's
`migrations_emitted` audit -- that counter counts decisions, and a decision can
be superseded before it becomes an action.
"""

import json
import re
from pathlib import Path


def _read(path):
    try:
        return Path(path).read_text(errors="replace")
    except OSError:
        return ""


def _jsonl(path):
    out = []
    for line in _read(path).splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def _marked(text, marker):
    out = []
    for line in text.splitlines():
        if marker in line:
            try:
                out.append(json.loads(line.split(marker, 1)[1]))
            except (IndexError, json.JSONDecodeError):
                pass
    return out


def _pct(values, q):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    k = (len(values) - 1) * q / 100.0
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def collect(run_dir):
    run = Path(run_dir)
    logs = run / "server-logs"
    result_files = sorted(run.glob("*_e2e_*rep.json"))
    summary = {}
    if result_files:
        try:
            summary = json.loads(_read(result_files[-1]))
        except json.JSONDecodeError:
            summary = {}

    requests = []
    for f in sorted((run / "requests").glob("*_output_requests.json")):
        try:
            requests = json.loads(_read(f))
            break
        except json.JSONDecodeError:
            continue
    if isinstance(requests, dict):
        requests = requests.get("requests", [])

    ttft, tpot, e2e = [], [], []
    met_ttft = met_tpot = met_both = 0
    completed = aborted = client_error = 0
    error_kinds = {}
    for r in requests:
        if not isinstance(r, dict):
            continue
        ok = bool(r.get("success"))
        err = str(r.get("error", "") or "")
        if ok:
            completed += 1
        elif "exceed" in err.lower():
            aborted += 1
        else:
            # Not a server failure: the benchmark client raising while it
            # writes a record down. Counted and classified separately so it is
            # never read as a dropped request.
            client_error += 1
            kind = err.strip().splitlines()[-1][:120] if err.strip() else "unknown"
            error_kinds[kind] = error_kinds.get(kind, 0) + 1
        t = r.get("ttft")
        p = r.get("tpot")
        lat = r.get("latency")
        if t is not None:
            ttft.append(t)
        if p is not None:
            tpot.append(p)
        if lat is not None:
            e2e.append(lat)
        slo_t, slo_p = r.get("slo_ttft"), r.get("slo_tpot")
        a = slo_t is not None and t is not None and t <= slo_t
        b = slo_p is not None and p is not None and p <= slo_p
        met_ttft += bool(a)
        met_tpot += bool(b)
        met_both += bool(a and b)

    offered = len(requests) or (summary.get("completed", 0) + summary.get("aborted", 0))
    duration = None
    if summary.get("completed") and summary.get("request_throughput"):
        duration = summary["completed"] / summary["request_throughput"]

    weights = _jsonl(run / "weight_transfers.jsonl")
    kv = _jsonl(run / "kv_transfers.jsonl")
    migrations = [w for w in weights if w.get("source") == "gpu"]
    controller = _read(logs / "server.log.global_controller.log")
    alg1 = _marked(controller, "[PAPER-ALG1-V4] ")
    audit = alg1[-1].get("audit_totals", {}) if alg1 else {}
    decisions = sum(1 for r in alg1 if r.get("migration_decision") == "MIGRATE")

    scheduler = "".join(_read(p) for p in sorted(logs.glob("*gpu_scheduler*.log")))
    runtime = _marked(scheduler, "[PAPER-ALG2-RUNTIME] ")
    violations = sum(1 for e in runtime if not e.get("order_ok"))

    server = _read(logs / "server.log")
    timeline = _marked(server, "[PAPER-MIGRATION-TIMELINE] ")
    downtime = []
    quiesce = {}
    for rec in timeline:
        if rec.get("event") == "source_quiesce":
            quiesce[rec.get("model")] = rec.get("time")
        elif rec.get("event") == "first_request_on_target":
            start = quiesce.pop(rec.get("model"), None)
            if start and rec.get("time"):
                downtime.append(rec["time"] - start)

    weight_bytes = sum(w.get("payload_bytes", 0) for w in migrations)
    kv_bytes = sum(k.get("kv_bytes", 0) for k in kv)
    weight_seconds = sum(w.get("seconds", 0) for w in migrations)
    kv_seconds = sum(k.get("seconds", 0) for k in kv)

    goodput = (met_both / duration) if duration and met_both else None

    return {
        "run": str(run),
        "offered_requests": offered,
        "completed": completed,
        "aborted": aborted,
        "client_errors": client_error,
        "client_error_kinds": error_kinds,
        "benchmark_reported_completed": summary.get("completed"),
        "benchmark_reported_aborted": summary.get("aborted"),
        "accounted": completed + aborted + client_error,
        "duration_s": duration,
        "achieved_throughput_req_s": summary.get("request_throughput"),
        "offered_throughput_req_s": (offered / duration) if duration else None,
        "goodput_req_s": goodput,
        "ttft_slo_attainment": (met_ttft / len(requests)) if requests else None,
        "tpot_slo_attainment": (met_tpot / len(requests)) if requests else None,
        "joint_slo_attainment": (met_both / len(requests)) if requests else None,
        "ttft_mean": (sum(ttft) / len(ttft)) if ttft else None,
        "tpot_mean": (sum(tpot) / len(tpot)) if tpot else None,
        "e2e_mean": (sum(e2e) / len(e2e)) if e2e else None,
        "ttft_p50": _pct(ttft, 50), "ttft_p95": _pct(ttft, 95), "ttft_p99": _pct(ttft, 99),
        "tpot_p50": _pct(tpot, 50), "tpot_p95": _pct(tpot, 95), "tpot_p99": _pct(tpot, 99),
        "e2e_p50": _pct(e2e, 50), "e2e_p95": _pct(e2e, 95), "e2e_p99": _pct(e2e, 99),
        "migration_decisions": decisions,
        "migrations_executed": len(migrations),
        "migrations_p2p": sum(1 for w in migrations
                              if w.get("transfer_path") == "gpu-to-gpu-p2p"),
        "migration_host_fallbacks": sum(1 for w in migrations
                                        if w.get("transfer_path") != "gpu-to-gpu-p2p"),
        "kv_transfers": len(kv),
        "weight_bytes": weight_bytes,
        "kv_bytes": kv_bytes,
        "weight_seconds": weight_seconds,
        "kv_seconds": kv_seconds,
        "weight_gbps": (weight_bytes / weight_seconds / 1e9) if weight_seconds else None,
        "kv_gbps": (kv_bytes / kv_seconds / 1e9) if kv_seconds else None,
        "exposed_downtime_mean_s": (sum(downtime) / len(downtime)) if downtime else None,
        "exposed_downtime_p95_s": _pct(downtime, 95),
        "exposed_downtime_n": len(downtime),
        "alg2_order_violations": violations,
        "alg2_runtime_events": len(runtime),
        "scheduler_cycles": audit.get("cycles"),
        "placement_decisions": audit.get("placement_decisions"),
        "tau_suppressed": audit.get("suppressed_by_tau"),
        "rejected_by_memory": audit.get("rejected_by_memory"),
        "deferred_by_cooldown": audit.get("deferred_by_cooldown"),
        "pipeline_rc": _read(run / "pipeline.rc").strip() or None,
    }
