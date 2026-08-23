#!/usr/bin/env python3
"""Where each request stopped, per rid, across the whole lifecycle.

Two stall classes have appeared in this campaign. One was an orphaned Algorithm 2
sequence and is fixed by construction. The other leaves the ledger clean, the
tokens clean and the engines idle, while a large number of requests simply never
receive a response -- 1,204 on seed_42, 1,986 on D3 B1 -- and no record spans the
server's view and the client's.

This walks every boundary a request crosses and reports, for each rid the client
never got, the last one it reached:

  dispatch            GPU scheduler issued the sequence and enqueued it
  backend_admit       the engine took it and the admission token granted it
  prefill_start       prefill began
  prefill_complete    prefill finished
  engine_output       the engine produced a final output
  stream_output_send  handed to the detokenizer
  detokenizer_send    detokenized and passed on
  handler_recv        the request handler queued it for the response generator
  http_final_yield    the final chunk was yielded to the client
  client_receipt      the client recorded a final receipt

and classifies it into exactly one bucket.
"""

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

ORDER = ["dispatch", "backend_admit", "prefill_start", "prefill_complete",
         "engine_output", "stream_output_send", "detokenizer_send",
         "handler_recv", "http_final_yield", "client_receipt"]
RANK = {b: i for i, b in enumerate(ORDER)}


def read(p):
    try:
        return p.read_text(errors="ignore")
    except OSError:
        return ""


def collect(run):
    """rid -> {boundary: timestamp} across every log."""
    seen = defaultdict(dict)
    logs = run / "server-logs"

    # Algorithm 2 runtime events, from the GPU scheduler
    for line in read(logs / "server.log.gpu_scheduler.log").splitlines():
        i = line.find("[PAPER-ALG2-RUNTIME] ")
        if i < 0:
            continue
        try:
            d = json.loads(line[i + 21:])
        except json.JSONDecodeError:
            continue
        ev = d.get("event")
        if ev not in RANK:
            continue
        for rid in (d.get("actual_rids") or []):
            seen[rid].setdefault(ev, d.get("event_time"))

    # response-path observation, from the engine / detokenizer / handler
    for name in ("server.log", "stdout.log"):
        for line in read(logs / name).splitlines():
            i = line.find("[PAPER-RESP-OBS] ")
            if i < 0:
                continue
            try:
                d = json.loads(line[i + 17:])
            except json.JSONDecodeError:
                continue
            b, rid = d.get("boundary"), d.get("rid")
            if rid and b in RANK:
                seen[rid].setdefault(b, d.get("time"))
            elif rid and b == "stream_abandoned":
                seen[rid].setdefault("stream_abandoned", d.get("time"))

    # client-side final receipts
    for line in read(run / "client_receipts.jsonl").splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("rid"):
            seen[d["rid"]]["client_receipt"] = d.get("time")
            seen[d["rid"]]["_client_success"] = d.get("success")
            seen[d["rid"]]["_client_error"] = d.get("error")
    return seen


def waited_on(run):
    """rids the client was still waiting for when the run ended."""
    bench = read(run / "server-logs" / "bench.log")
    return sorted(set(re.findall(
        r"Waiting for task req_([a-z0-9_]+#\d+)_", bench)))


def classify(marks):
    """Exactly one bucket per rid."""
    reached = [b for b in ORDER if b in marks]
    last = reached[-1] if reached else None
    if last is None:
        return "never_in_any_server_lifecycle", None
    if marks.get("_client_success") is True:
        return "client_completed", last
    if last == "client_receipt":
        return "client_receipt_without_success", last
    if RANK[last] >= RANK["http_final_yield"]:
        return "yielded_but_client_never_accounted", last
    if RANK[last] >= RANK["engine_output"]:
        return "engine_done_but_output_path_broke", last
    return "never_reached_engine_completion", last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    run = Path(a.run)

    seen = collect(run)
    waiting = waited_on(run)
    buckets = defaultdict(list)
    lasts = defaultdict(int)
    for rid in waiting:
        b, last = classify(seen.get(rid, {}))
        buckets[b].append(rid)
        lasts[last or "none"] += 1

    report = {
        "run": str(run),
        "rids_observed_anywhere": len(seen),
        "rids_client_waited_on": len(waiting),
        "classification": {k: len(v) for k, v in sorted(buckets.items())},
        "last_boundary_histogram": dict(sorted(
            lasts.items(), key=lambda kv: -kv[1])),
        "examples": {k: v[:5] for k, v in sorted(buckets.items())},
        "sample_traces": {
            rid: {b: seen[rid][b] for b in ORDER if b in seen.get(rid, {})}
            for rid in waiting[:3]
        },
    }
    text = json.dumps(report, indent=2, sort_keys=True)
    print(text)
    if a.out:
        Path(a.out).write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
