#!/usr/bin/env python3
"""D2/D3 migration correctness gate.

Reads one stage directory and decides PASS/FAIL against the preregistered
criteria: no crash, no CUDA OOM, no migration-induced abort, no residency
metadata loss, and -- the specific defect this gate exists for -- no migration
that cold-loads from host memory while the model is actually GPU-resident.

Every check reports the evidence it used, so a PASS is auditable and a FAIL
names the file and line that produced it.
"""

import argparse
import json
import re
from pathlib import Path

OOM_PATTERNS = [
    "cuMemCreate", "out of memory", "OutOfMemoryError",
    "CUDA out of memory", "CUDA error: out of memory",
]
CRASH_PATTERNS = [
    "Killed", "Segmentation fault", "core dumped",
    "terminated by signal", "NCCL error", "CUDA error:",
]
RELEASE_RE = re.compile(
    r"\[PAPER-LOAD-V4\] release (?P<model>\S+) gpu (?P<gpu>\d+)"
    r"(?: engine=(?P<engine>\S+))?"
    r" held=(?P<held>\S+)"
    r"(?: held_engine=(?P<held_engine>\S+))?"
    r"(?: dropped=(?P<dropped>\S+))?"
)
TAG_RE = re.compile(r"^(?P<model>.+)\|engine=(?P<engine>[^|]+)\|src=(?P<src>\S+)$")


def read(path):
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def jsonl(path):
    out = []
    for line in read(path).splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def marked(path, marker):
    out = []
    for line in read(path).splitlines():
        if marker in line:
            try:
                out.append(json.loads(line.split(marker, 1)[1]))
            except (IndexError, json.JSONDecodeError):
                pass
    return out


def hits(text, patterns, source):
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        for pattern in patterns:
            if pattern in line:
                found.append({"file": source, "line": number,
                              "pattern": pattern, "text": line[:400]})
                break
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    run = args.run
    logs = run / "server-logs"
    stdout = logs / "stdout.log"
    server = logs / "server.log"
    service = logs / "server.log.model_service.log"
    controller = logs / "server.log.global_controller.log"
    scheduler = logs / "server.log.gpu_scheduler.log"

    checks = []

    def record(name, ok, detail):
        checks.append({"check": name, "pass": bool(ok), "detail": detail})

    # ---- 1. the run itself finished ---------------------------------------
    rc = read(run / "pipeline.rc").strip()
    record("pipeline_rc_zero", rc == "0", {"pipeline_rc": rc or None})

    status_path = run / "monitor" / "status.json"
    status = {}
    if status_path.exists():
        try:
            status = json.loads(read(status_path))
        except json.JSONDecodeError:
            status = {}
    fail_marker = read(run / "monitor" / "FAIL").strip()
    record(
        "watchdog_complete",
        status.get("state") == "COMPLETE" and not fail_marker,
        {"state": status.get("state"), "failure_reason": fail_marker or None},
    )

    # ---- 2. no OOM, no crash ----------------------------------------------
    oom = (hits(read(stdout), OOM_PATTERNS, "server-logs/stdout.log")
           + hits(read(server), OOM_PATTERNS, "server-logs/server.log"))
    record("no_cuda_oom", not oom, {"count": len(oom), "hits": oom[:5]})

    crash = (hits(read(stdout), CRASH_PATTERNS, "server-logs/stdout.log")
             + hits(read(server), CRASH_PATTERNS, "server-logs/server.log"))
    record("no_server_crash", not crash, {"count": len(crash), "hits": crash[:5]})

    # ---- 3. every migration used the GPU source it actually has ------------
    decisions = [
        r for r in marked(controller, "[PAPER-ALG1-V4] ")
        if r.get("migration_decision") == "MIGRATE"
    ]
    decisions.sort(key=lambda r: r["timestamp"])
    weights = jsonl(run / "weight_transfers.jsonl")
    migrations, cold = [], []
    for index, decision in enumerate(decisions, 1):
        candidate = decision["candidate"]
        model_path = candidate.get("model_path") or candidate.get("model")
        source_gpu, target_gpu = candidate["from"], candidate["to"]
        after = [
            w for w in weights
            if w.get("target_gpu") == target_gpu
            and w.get("start_time", 0) >= decision["timestamp"]
        ]
        transfer = min(after, key=lambda w: w["start_time"]) if after else None
        tag = TAG_RE.match(str(transfer.get("tag", ""))) if transfer else None
        src = tag.group("src") if tag else None
        row = {
            "migration_id": index,
            "model": candidate.get("model"),
            "from": source_gpu,
            "to": target_gpu,
            "weight_src": src,
            "weight_source": (transfer or {}).get("source"),
            "transfer_path": (transfer or {}).get("transfer_path"),
            "payload_bytes": (transfer or {}).get("payload_bytes"),
            "seconds": (transfer or {}).get("seconds"),
            "payload_gbps": (transfer or {}).get("payload_gbps"),
            "tag": (transfer or {}).get("tag"),
        }
        migrations.append(row)
        # The model was GPU-resident on `from` when the decision was taken, so
        # the transfer must read it from there rather than from host memory.
        if src != str(source_gpu):
            cold.append(row)
    record(
        "migration_source_is_the_resident_gpu",
        not cold,
        {"migrations": len(migrations), "wrong_source": cold},
    )
    record(
        "migration_weights_move_over_p2p",
        all(row["transfer_path"] == "gpu-to-gpu-p2p" for row in migrations)
        if migrations else False,
        {"paths": sorted({row["transfer_path"] for row in migrations})},
    )

    # ---- 4. residency records survive the source release -------------------
    releases = []
    for number, line in enumerate(read(service).splitlines(), 1):
        match = RELEASE_RE.search(line)
        if match:
            releases.append({"line": number, **match.groupdict()})
    unversioned = [r for r in releases if r["dropped"] is None]
    corrupt = [
        r for r in releases
        if r["dropped"] == "True" and r["held"] not in ("None", r["gpu"])
    ]
    record(
        "release_is_owner_aware",
        not unversioned,
        {"releases": len(releases),
         "releases_without_ownership_evidence": len(unversioned)},
    )
    record(
        # Fail closed: without ownership evidence in the log this cannot be
        # decided, and an undecidable check is not a pass.
        "no_residency_metadata_loss",
        not corrupt and not unversioned,
        {"releases": len(releases),
         "undecidable_releases": len(unversioned),
         "preserved_after_migration": sum(
             1 for r in releases
             if r["dropped"] == "False" and r["held"] not in (None, "None")),
         "corrupt": corrupt},
    )

    # ---- 5. the benchmark completed its requests ---------------------------
    results = sorted(run.glob("*_e2e_*rep.json"))
    summary = {}
    if results:
        try:
            summary = json.loads(read(results[-1]))
        except json.JSONDecodeError:
            summary = {}
    record(
        "benchmark_result_written",
        bool(summary),
        {"result_file": results[-1].name if results else None},
    )

    aborts = read(server).count("migration-induced abort")
    record("no_migration_induced_abort", aborts == 0, {"count": aborts})

    scheduler_text = read(scheduler)
    outstanding = re.findall(r"outstanding at shutdown[^\n]*", scheduler_text)
    record(
        "shutdown_state_reported_clean",
        all("=0" in line.replace(" ", "") for line in outstanding)
        if outstanding else True,
        {"lines": outstanding[:4] or "no shutdown outstanding report in this arm"},
    )

    verdict = "PASS" if all(c["pass"] for c in checks) else "FAIL"
    report = {
        "run": str(run),
        "verdict": verdict,
        "checks": checks,
        "migrations": migrations,
        "releases": releases,
        "benchmark_summary": {
            k: summary.get(k) for k in (
                "completed", "unfinished", "failed", "rejected", "aborted",
                "achieved_throughput", "goodput", "joint_slo_attainment",
            ) if k in summary
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    for check in checks:
        print(f"  {'PASS' if check['pass'] else 'FAIL'}  {check['check']}")
        if not check["pass"]:
            print(f"        {json.dumps(check['detail'])[:600]}")
    print(f"\nVERDICT: {verdict}  ({args.out})")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
