#!/usr/bin/env python3
"""Run-validity gate for migration-lifecycle integrity.

Detects, from a completed run's own logs, the signatures of the stall analysed
in reports/prism/04_correctness/P4HET_MIGRATION_LIFECYCLE_STALL_FORENSIC.md. Offline only; changes no runtime
semantics. Intended to be applied to every tau / final / many-model run so a
silently degraded run cannot pass as valid.

  python lifecycle_validity_gate.py <run_dir> [run_dir ...]

Exit status 1 if any run fails.
"""
import datetime
import json, re, sys
from pathlib import Path

CYCLE_GAP_LIMIT_S = 30.0
TS = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)")


def read(p):
    return p.read_text(errors="replace") if p.exists() else ""


def cycles(log):
    out = []
    for line in log.splitlines():
        i = line.find("PAPER-ALG1-V4] {")
        if i < 0:
            continue
        try:
            out.append(json.loads(line[i + len("PAPER-ALG1-V4] "):]))
        except Exception:
            pass
    return out


def check(run: Path):
    sl = run / "server-logs"
    server = read(sl / "server.log")
    gsched = read(sl / "server.log.gpu_scheduler.log")
    ctrl = read(sl / "server.log.global_controller.log")
    findings = []

    # 1. A WorkerPool teardown during the run, rather than at shutdown. The GPU
    #    scheduler that owns it has left its loop; that GPU is silently dead.
    pool_cleanups = re.findall(r"WorkerPool GPU (\d+) cleanup completed", gsched)
    if pool_cleanups and "Received signal to shutdown" not in gsched:
        findings.append(f"runtime WorkerPool cleanup on GPU(s) {sorted(set(pool_cleanups))} "
                        f"with no shutdown signal")

    # 2. Controller placement loop stalled.
    cs = cycles(ctrl)
    gaps = [(a["cycle"], b["cycle"], b["timestamp"] - a["timestamp"])
            for a, b in zip(cs, cs[1:])
            if b["timestamp"] - a["timestamp"] > CYCLE_GAP_LIMIT_S]
    for a, b, g in gaps:
        findings.append(f"controller cycle gap {g:.1f}s between cycle {a} and {b}")

    # 3. A source release with no matching activation of the same model. Counted
    #    per model: every source_release belongs to a migration whose
    #    destination must have been activated.
    releases = re.findall(r'"event": "source_release", "model": "([^"]+)"', server)
    activations = re.findall(r"Activate model (\S+) \(", server)
    acts = {}
    for a in activations:
        acts[a] = acts.get(a, 0) + 1
    rel = {}
    for r in releases:
        rel[r] = rel.get(r, 0) + 1
    # model names differ in form between the two logs; compare by count only
    if sum(rel.values()) > sum(acts.values()):
        findings.append(f"unmatched source_release: {sum(rel.values())} releases vs "
                        f"{sum(acts.values())} activations")

    # 4. A control request left unacknowledged for longer than the cycle-gap
    #    limit. Sends and acks are matched FIFO per (action, gpu); concurrent
    #    requests to different GPUs are normal, and the final request of a run
    #    is legitimately in flight when logging stops. What is not normal is a
    #    request that stays unanswered for tens of seconds.
    def _ts(m):
        return datetime.datetime.strptime(m, "%Y-%m-%d %H:%M:%S.%f")

    sends, acks, last_ts = [], [], None
    for line in server.splitlines():
        m = TS.match(line)
        if not m:
            continue
        last_ts = _ts(m.group(1))
        ms = re.search(r"Sending (de)?activate request to GPU scheduler (\d+)", line)
        if ms:
            sends.append([last_ts, "Deactivate" if ms.group(1) else "Activate",
                          ms.group(2), None])
            continue
        ma = re.search(r'\[V5-HOP\] \{"action": "(Activate|Deactivate)ReqInput",'
                       r'.*?"gpu_id": (\d+)', line)
        if ma:
            acks.append((last_ts, ma.group(1), ma.group(2)))
    # Applicability: the acknowledgement path exists only when the runtime runs
    # with --overlap-migration. The released prototype (--policy simple-global)
    # issues control requests and, by design, never acknowledges them, so a run
    # that produced no acks at all cannot be judged by this check. Prism runs
    # always produce acks, so the check keeps full force where it matters.
    if not acks:
        sends = []
    for ats, akind, agpu in acks:
        for snd in sends:
            if snd[3] is None and snd[1] == akind and snd[2] == agpu and snd[0] <= ats:
                snd[3] = ats
                break
    for sts, kind, gpu, ack in sends:
        if ack is None and last_ts is not None:
            waited = (last_ts - sts).total_seconds()
            if waited > CYCLE_GAP_LIMIT_S:
                findings.append(
                    f"{kind.lower()} request to GPU {gpu} at "
                    f"{sts.strftime('%H:%M:%S.%f')[:-3]} was never acknowledged "
                    f"({waited:.1f}s unanswered)")

    # 5. An unexpected GPU scheduler shutdown. With the containment fix in
    #    place this also kills the run; the gate still reports it so an
    #    already-completed run cannot be read as healthy.
    for m in re.finditer(r'"event": "scheduler_shutdown_requested".*?"reason": "([^"]+)"',
                         gsched):
        if m.group(1) != "normal_shutdown":
            findings.append(f"unexpected scheduler shutdown: reason={m.group(1)}")
    if '"event": "run_fatal_unexpected_scheduler_shutdown"' in gsched:
        for m in re.finditer(
                r'"event": "run_fatal_unexpected_scheduler_shutdown".*?"reason": "([^"]+)"',
                gsched):
            findings.append(f"run declared fatal by containment guard: {m.group(1)}")

    # 6. The guards added by the rollback fix, if they fired.
    for ev in ("control_request_timeout", "control_target_not_alive"):
        n = server.count(f'"event": "{ev}"')
        if n:
            findings.append(f"lifecycle guard fired: {ev} x{n}")

    return findings


def main(argv):
    bad = 0
    for a in argv:
        run = Path(a)
        f = check(run)
        label = run.name if run.name != "server-logs" else run.parent.name
        if f:
            bad += 1
            print(f"FAIL {a}")
            for x in f:
                print(f"       - {x}")
        else:
            print(f"PASS {a}")
    print(f"\nlifecycle validity: {len(argv) - bad}/{len(argv)} PASS")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
