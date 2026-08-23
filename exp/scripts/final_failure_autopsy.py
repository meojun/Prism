#!/usr/bin/env python3
"""When a run fails while nobody is watching, diagnose it before stopping.

Writes a causal chain from the run's own logs, classifies the failure against
the ones already fixed, and proposes a minimal change -- proposes only. Nothing
here edits runtime code, adds a timeout, or retries.
"""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

KNOWN = [
    ("residency lifecycle (fixed 7053904)",
     r"src=None.*held=\d", "a migration read from host memory while the model was GPU-resident"),
    ("stale staged request (fixed 444a216 / B)",
     r'"reason": "staged-never-admitted"', "a staged request survived a deactivation"),
    ("slot-renamed admission (fixed 444a216 / A)",
     r"backend_admit.*order_ok.*false", "an admission carried the wrong model"),
    ("activation memory wait (fixed 70ad7b8, 56636e1)",
     r"Waiting for enough memory to load the model", "an activation blocked its event loop"),
    ("flashinfer workspace (fixed in env.sh)",
     r"batch_prefill_tmp_v", "the prefill scratch buffer was too small"),
]


def read(p):
    try:
        return Path(p).read_text(errors="replace")
    except OSError:
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    logs = args.run / "server-logs"
    server, stdout = read(logs / "server.log"), read(logs / "stdout.log")
    scheduler = "".join(read(p) for p in sorted(logs.glob("*gpu_scheduler*.log")))
    controller = read(logs / "server.log.global_controller.log")

    events = []
    for line in scheduler.splitlines():
        if "[PAPER-ALG2-RUNTIME] " not in line:
            continue
        try:
            events.append(json.loads(line.split("[PAPER-ALG2-RUNTIME] ", 1)[1]))
        except (IndexError, json.JSONDecodeError):
            pass
    violations = [e for e in events if not e.get("order_ok")]

    frontier = {}
    for e in events:
        g, ev, seq = e.get("gpu_id"), e.get("event"), e.get("alg2_seq")
        if seq is None:
            continue
        frontier.setdefault(g, {}).setdefault(ev, []).append(seq)
    frontier = {g: {ev: {"last": max(s), "n": len(s)} for ev, s in d.items()}
                for g, d in frontier.items()}

    symptoms = {
        "cuda_oom": len(re.findall(r"torch\.OutOfMemoryError|CUDA out of memory|cuMemCreate", server + stdout)),
        "fatal_cuda_nccl": len(re.findall(r"NCCL error|ncclUnhandledCudaError|CUDA error:", server)),
        "alg2_order_violations": len(violations),
        "activation_waits": server.count("Waiting for enough memory to load the model"),
        "decode_retractions": server.count("Decode out of memory happened"),
        "killed": "Killed" in stdout,
        "watchdog": read(args.run / "monitor" / "FAIL").strip() or None,
        "pipeline_rc": read(args.run / "pipeline.rc").strip() or None,
    }

    lifecycle = []
    for v in violations[:3]:
        rid = (v.get("actual_rids") or [None])[0]
        if not rid:
            continue
        trail = [e for e in events
                 if rid in (e.get("actual_rids") or []) or e.get("expected_rid") == rid]
        lifecycle.append({
            "rid": rid,
            "trail": [{"t": e.get("event_time"), "gpu": e.get("gpu_id"),
                       "event": e.get("event"), "seq": e.get("alg2_seq"),
                       "model": e.get("actual_model"),
                       "expected_model": e.get("expected_model"),
                       "order_ok": e.get("order_ok")} for e in trail],
        })

    blob = server + stdout + scheduler
    matches = [{"class": name, "evidence": why}
               for name, pattern, why in KNOWN if re.search(pattern, blob)]

    verdict = ("recurrence of a class already fixed" if matches
               else "new lifecycle class, not seen before")

    doc = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "run": str(args.run), "label": args.label,
        "symptoms": symptoms,
        "alg2_frontier_by_gpu": frontier,
        "violations": violations[:5],
        "failing_request_lifecycles": lifecycle,
        "classification": verdict,
        "matched_known_classes": matches,
        "causal_chain": _chain(symptoms, violations, lifecycle, frontier),
        "proposed_minimal_fix": _proposal(symptoms, violations, matches),
        "policy": ("Diagnosis only. No runtime change, no timeout, no bypass, "
                   "no retry was made; the pipeline is left stopped."),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2, default=str) + "\n")
    print(json.dumps({k: doc[k] for k in
                      ("classification", "symptoms", "causal_chain")},
                     indent=2, default=str)[:2000])


def _chain(sym, violations, lifecycle, frontier):
    if sym["cuda_oom"]:
        return "a CUDA allocation failed; see symptoms.cuda_oom and the server log"
    if violations:
        v = violations[0]
        rid = (v.get("actual_rids") or ["?"])[0]
        return (f"{rid} reached {v.get('event')} on GPU{v.get('gpu_id')} as "
                f"sequence {v.get('alg2_seq')} under model {v.get('actual_model')} "
                f"while the ledger held it under {v.get('expected_model')}; the "
                f"gate failed closed and that GPU's frontier stopped there")
    if sym["activation_waits"]:
        return ("an activation blocked in load_gpu_model's memory wait inside "
                "its scheduler's event loop, so that GPU stopped serving")
    if sym["watchdog"]:
        return f"the watchdog ended the run: {sym['watchdog']}"
    return "no single cause is evident from the logs; see symptoms"


def _proposal(sym, violations, matches):
    if matches:
        return ("This matches a class already fixed: " +
                "; ".join(m["class"] for m in matches) +
                ". Check whether the fix covers this path before changing anything.")
    if violations:
        return ("Trace the failing request's lifecycle above end to end and "
                "establish which structure held it at each transition before "
                "proposing a change.")
    return "Establish the causal chain before proposing a change."


if __name__ == "__main__":
    main()
