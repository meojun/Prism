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
from datetime import datetime, timezone
from pathlib import Path

# Allocation failures only. NOT "Decode out of memory happened.
# #retracted_reqs: N" -- that is upstream SGLang's designed decode-retraction
# backpressure (scheduler.py sends a BatchRetractDecodeReq and lowers
# new_token_ratio); nothing failed to allocate and no request is lost. It is
# reported below as a load metric instead of gating the run.
OOM_PATTERNS = [
    "cuMemCreate", "OutOfMemoryError", "CUDA out of memory",
    "CUDA error: out of memory", "failed in CUDA driver",
]
RETRACTION_MARKER = "Decode out of memory happened"
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

    retractions = read(server).count(RETRACTION_MARKER)
    retracted = sum(
        int(m) for m in re.findall(
            RETRACTION_MARKER + r"[^\n]*?#retracted_reqs: (\d+)", read(server))
    )
    # Reported, not gated: retraction is a designed response to a full KV pool,
    # so it measures how hard the pools were squeezed rather than whether
    # anything broke.
    checks.append({
        "check": "decode_retraction (reported, not gating)",
        "pass": True,
        "detail": {"retraction_events": retractions,
                   "requests_retracted": retracted},
    })

    crash = (hits(read(stdout), CRASH_PATTERNS, "server-logs/stdout.log")
             + hits(read(server), CRASH_PATTERNS, "server-logs/server.log"))
    record("no_server_crash", not crash, {"count": len(crash), "hits": crash[:5]})

    # ---- 3. every migration used the GPU source it actually has ------------
    # Match each decision to *its own* model's transfer. Matching on target
    # GPU alone silently pairs a migration with an unrelated activation that
    # happened to land on the same GPU -- which is how a run whose migration
    # never ran at all can be reported as having used the wrong source.
    model_paths = {}
    # The launcher command line survives in different places depending on how
    # the run ended: stdout keeps it only when the server was killed, and the
    # watchdog's pid snapshots keep it whenever the server was alive. Search
    # all of them rather than depending on one.
    for candidate_cfg in (run / "scheduler_proof.txt", run / "STAGE_CMD.sh",
                          run / "pipeline.log",
                          run / "monitor" / "status.json",
                          run / "monitor" / "heartbeat.jsonl",
                          logs / "stdout.log"):
        text = read(candidate_cfg)
        found = re.search(
            r"(?:--model-config-file[= ]+|model_config_file=)(\S+)", text)
        if not found:
            continue
        try:
            entries = json.loads(read(Path(found.group(1).strip("'\""))))
        except (json.JSONDecodeError, OSError):
            continue
        model_paths = {e["model_name"]: e["model_path"] for e in entries}
        break

    # The controller runs idle-instance eviction *before* Algorithm 1 in the
    # same cycle, and the action list it builds afterwards carries only the
    # deactivation. Measured in run 6: cycle 20 logged
    #   ACTION: deactivate model_4:1 on GPU 1. Reason: idle instance eviction
    #   PLANNING: migrate model model_4 from GPU 1 to GPU 0
    # and executed the deactivation alone. Such a decision is superseded, not
    # a migration that failed to move its weights, so it is reported rather
    # than gated -- while a decision that vanished for no stated reason still
    # fails. Note this also means the policy's `migrations_emitted` audit
    # counts decisions, not executed migrations.
    evicted_in_cycle, pending_evictions = {}, set()
    for line in read(controller).splitlines():
        evicted = re.search(
            r"ACTION: deactivate (\S+?):\d+ on GPU \d+\. "
            r"Reason: idle instance eviction", line)
        if evicted:
            pending_evictions.add(evicted.group(1))
            continue
        if "[PAPER-ALG1-V4] " in line:
            try:
                cycle = json.loads(line.split("[PAPER-ALG1-V4] ", 1)[1])["cycle"]
            except (IndexError, json.JSONDecodeError, KeyError):
                continue
            evicted_in_cycle[cycle] = set(pending_evictions)
            pending_evictions = set()

    # Releases, with their wall-clock stamps, so a host load can be checked
    # against what residency actually existed at the time.
    release_log = []
    for line in read(service).splitlines():
        stamped = re.match(
            r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)", line)
        found = RELEASE_RE.search(line)
        if stamped and found:
            try:
                when = datetime.strptime(
                    stamped.group(1), "%Y-%m-%d %H:%M:%S.%f"
                ).replace(tzinfo=timezone.utc).timestamp()
            except ValueError:
                continue
            release_log.append({"t": when, **found.groupdict()})

    decisions = [
        r for r in marked(controller, "[PAPER-ALG1-V4] ")
        if r.get("migration_decision") == "MIGRATE"
    ]
    decisions.sort(key=lambda r: r["timestamp"])
    weights = jsonl(run / "weight_transfers.jsonl")
    migrations, cold, missing, superseded, deactivated = [], [], [], [], []
    for index, decision in enumerate(decisions, 1):
        candidate = decision["candidate"]
        model_path = candidate.get("model_path") or candidate.get("model")
        source_gpu, target_gpu = candidate["from"], candidate["to"]
        wanted_path = model_paths.get(candidate.get("model"))
        after = [
            w for w in weights
            if w.get("target_gpu") == target_gpu
            and w.get("start_time", 0) >= decision["timestamp"]
            and (wanted_path is None
                 or str(w.get("tag", "")).startswith(wanted_path + "|"))
        ]
        transfer = min(after, key=lambda w: w["start_time"]) if after else None
        tag = TAG_RE.match(str(transfer.get("tag", ""))) if transfer else None
        src = tag.group("src") if tag else None
        row = {
            "migration_id": index,
            "model": candidate.get("model"),
            "model_path": wanted_path,
            "matched_by_model_path": wanted_path is not None,
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
        if transfer is None:
            if candidate.get("model") in evicted_in_cycle.get(
                    decision.get("cycle"), set()):
                row["superseded_by"] = "idle instance eviction in the same cycle"
                superseded.append(row)
            else:
                missing.append(row)
        elif src != str(source_gpu):
            # A host load is only a defect if the model was still GPU-resident
            # when it happened -- that is the original D2 failure, where a
            # completed migration deleted its own residency record and the
            # reverse leg cold-loaded 6.79 GB on top of a GPU that held the
            # model. If the owning engine released the model first
            # (`dropped=True`), there is no GPU copy left to read and a host
            # load is the only thing the loader can do.
            prior = [r for r in release_log
                     if wanted_path and r["model"] == wanted_path
                     and r["t"] < transfer.get("start_time", 0)]
            last_release = prior[-1] if prior else None
            if last_release and last_release.get("dropped") == "True":
                row["host_load_reason"] = (
                    f"model released from gpu {last_release['gpu']} at "
                    f"{last_release['t']:.1f}, "
                    f"{transfer['start_time'] - last_release['t']:.1f}s before "
                    "this load: no GPU copy remained")
                deactivated.append(row)
            else:
                row["residency_at_load"] = (
                    "none recorded" if last_release is None
                    else f"held gpu {last_release['held']} "
                         f"dropped={last_release['dropped']}")
                cold.append(row)
    record(
        "migration_source_is_the_resident_gpu",
        not cold,
        {"migrations": len(migrations), "wrong_source": cold},
    )
    record(
        "every_decision_produced_a_transfer",
        not missing,
        {"decisions_without_a_weight_transfer": missing},
    )
    checks.append({
        "check": "host_loads_after_deactivation (reported, not gating)",
        "pass": True,
        "detail": {"count": len(deactivated), "migrations": deactivated},
    })
    checks.append({
        "check": "decisions_superseded_by_idle_eviction (reported, not gating)",
        "pass": True,
        "detail": {"count": len(superseded), "decisions": superseded},
    })
    # A gate that a run with zero migrations could pass would not test
    # migration at all, so the substance of the arm is required explicitly.
    p2p = [r for r in migrations
           if r["transfer_path"] == "gpu-to-gpu-p2p"
           and r["weight_src"] == str(r["from"])]
    record(
        "at_least_one_gpu_to_gpu_migration",
        bool(p2p),
        {"gpu_to_gpu_migrations": len(p2p)},
    )

    moves = {}
    reverse = []
    executed = [row for row in migrations if row["transfer_path"]]
    for row in executed:
        pair = (row["model"], row["from"], row["to"])
        back = (row["model"], row["to"], row["from"])
        if back in moves:
            reverse.append({"first": moves[back], "reverse": row["migration_id"],
                            "model": row["model"],
                            "path": f'{row["to"]} -> {row["from"]} -> {row["to"]}'})
        moves[pair] = row["migration_id"]
    record(
        "reverse_migration_completed",
        bool(reverse),
        {"reverse_pairs": reverse,
         "legs_over_p2p": sum(
             1 for row in executed
             if row["transfer_path"] == "gpu-to-gpu-p2p")},
    )

    kv = jsonl(run / "kv_transfers.jsonl")
    moved = [r for r in kv if (r.get("requests_moved") or 0) > 0]
    record(
        "kv_transfer_healthy",
        bool(moved)
        and all(r.get("transfer_path") == "gpu-to-gpu-p2p" for r in moved)
        and all((r.get("requests_skipped_over_cap") or 0) == 0 for r in moved),
        {"transfers_with_requests": len(moved),
         "paths": sorted({str(r.get("transfer_path")) for r in kv}),
         "requests_moved": sum(r.get("requests_moved") or 0 for r in kv),
         "tokens_moved": sum(r.get("tokens_moved") or 0 for r in kv),
         "skipped_over_cap": sum(r.get("requests_skipped_over_cap") or 0 for r in kv)},
    )

    audit = {}
    for line in read(controller).splitlines():
        if "[PAPER-ALG1-V4] " in line:
            try:
                audit = json.loads(line.split("[PAPER-ALG1-V4] ", 1)[1]).get(
                    "audit_totals", audit)
            except (IndexError, json.JSONDecodeError):
                pass
    record(
        # The memory gate must refuse reckless targets without refusing the
        # workload's migrations wholesale.
        "migrations_not_all_blocked_by_memory",
        (audit.get("migrations_emitted", 0) or 0) > 0,
        {k: audit.get(k) for k in (
            "migrations_emitted", "rejected_by_memory", "suppressed_by_tau",
            "deferred_by_cooldown", "rejected_last_model_on_gpu")},
    )

    # Judged over the transfers that happened; a decision with no transfer at
    # all is reported by `every_decision_produced_a_transfer` instead, so one
    # dead migration is not counted as two separate defects.
    deactivated_ids = {row["migration_id"] for row in deactivated}
    transferred = [row for row in migrations
                   if row["transfer_path"]
                   and row["migration_id"] not in deactivated_ids]
    record(
        "migration_weights_move_over_p2p",
        bool(transferred)
        and all(row["transfer_path"] == "gpu-to-gpu-p2p" for row in transferred),
        {"paths": sorted({str(row["transfer_path"]) for row in migrations}),
         "with_a_live_gpu_source": len(transferred),
         "host_loads_after_deactivation": len(deactivated),
         "decisions": len(migrations)},
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
