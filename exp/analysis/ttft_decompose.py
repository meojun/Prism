#!/usr/bin/env python3
"""Decompose TTFT into server-side segments, per run, both arms.

Segment boundaries are the server's own timestamps, recorded per request by
the benchmark client (benchmark.py:162-189):

    arrival -> gpu_scheduler_queue -> gpu_scheduler_dispatch
            -> out_queue -> prefill_finish   (= TTFT endpoint)

    intake    = gpu_scheduler_queue   - arrival
    admission = gpu_scheduler_dispatch - gpu_scheduler_queue   (Algorithm 2 wait)
    fetch     = out_queue             - gpu_scheduler_dispatch
    prefill   = prefill_finish        - out_queue

Some Prism requests carry NO scheduler timestamps (both 0). Those cannot be
split at the scheduler boundary, so they are reported separately with

    prequeue  = out_queue - arrival

and are never silently folded into the other buckets.
"""
import json
import sys
from pathlib import Path

ROOT = Path("/workspace/prism-exp")
RAW = ROOT / "exp/results/4het-paired/raw"
RATES = (2, 4, 6, 8, 10)
SEEDS = (1, 2)
KINDS = ("bursty", "steady")
ARMS = ("prototype", "prism")


def load(run):
    for f in sorted((run / "requests").glob("*_output_requests.json")):
        try:
            d = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        if isinstance(d, list) and d:
            return d
    return []


def segs(r):
    """Return (has_sched, dict of segments) for one completed request."""
    a = r.get("arrival_time") or 0.0
    q = r.get("gpu_scheduler_queue_time") or 0.0
    d = r.get("gpu_scheduler_dispatch_time") or 0.0
    o = r.get("out_queue_time") or 0.0
    p = r.get("prefill_finish_time") or 0.0
    if not (a and o and p):
        return None, None
    if q and d:
        return True, {"intake": q - a, "admission": d - q,
                      "fetch": o - d, "prefill": p - o, "ttft": p - a}
    return False, {"prequeue": o - a, "prefill": p - o, "ttft": p - a}


def analyse(run):
    reqs = [r for r in load(run) if isinstance(r, dict)]
    ok = [r for r in reqs if r.get("success")]
    out = {"n_requests": len(reqs), "n_completed": len(ok),
           "n_with_sched_ts": 0, "n_without_sched_ts": 0,
           "tail_1s": 0, "tail_5s": 0, "tail_10s": 0,
           "tail_1s_without_sched_ts": 0, "tail_5s_without_sched_ts": 0,
           "tail_10s_without_sched_ts": 0,
           "sum": {k: 0.0 for k in ("intake", "admission", "fetch", "prefill",
                                    "prequeue", "ttft")},
           "tail_sum": {k: 0.0 for k in ("intake", "admission", "fetch",
                                         "prefill", "prequeue", "ttft")},
           "worst": None}
    worst_t = -1
    for r in ok:
        has, s = segs(r)
        if s is None:
            continue
        out["n_with_sched_ts" if has else "n_without_sched_ts"] += 1
        for k, v in s.items():
            out["sum"][k] += v
        t = s["ttft"]
        for thr, key in ((1, "tail_1s"), (5, "tail_5s"), (10, "tail_10s")):
            if t > thr:
                out[key] += 1
                if not has:
                    out[key + "_without_sched_ts"] += 1
        if t > 1:
            for k, v in s.items():
                out["tail_sum"][k] += v
        if t > worst_t:
            worst_t = t
            out["worst"] = {"ttft": t, "model": r.get("model"),
                            "has_sched_ts": has, **{k: round(v, 4) for k, v in s.items()}}
    return out


def main():
    rows = []
    for kind in KINDS:
        for rate in RATES:
            for seed in SEEDS:
                for arm in ARMS:
                    run = RAW / arm / kind / f"rate_{rate}" / f"seed_{seed}"
                    if not list(run.glob("*_e2e_*rep.json")):
                        continue
                    a = analyse(run)
                    a.update(arm=arm, workload=kind, rate=rate, seed=seed)
                    rows.append(a)
    Path(ROOT / "exp/analysis/ttft_decompose.json").write_text(
        json.dumps(rows, indent=1))

    print("TTFT decomposition -- mean seconds per completed request\n")
    hdr = (f"{'cond':>12} {'arm':<10}{'TTFT':>8}{'intake':>8}{'admis':>9}"
           f"{'fetch':>8}{'prefill':>8}{'prequeue*':>10}  {'noTS':>6}"
           f"{'>1s':>6}{'>5s':>6}{'>10s':>6}")
    for kind in KINDS:
        print(f"\n### {kind}")
        print(hdr)
        for rate in RATES:
            for arm in ARMS:
                rs = [r for r in rows if r["workload"] == kind
                      and r["rate"] == rate and r["arm"] == arm]
                if not rs:
                    continue
                n = sum(r["n_with_sched_ts"] + r["n_without_sched_ts"] for r in rs)
                if not n:
                    continue
                g = lambda k: sum(r["sum"][k] for r in rs) / n
                nots = sum(r["n_without_sched_ts"] for r in rs)
                print(f"{kind+str(rate):>12} {arm:<10}"
                      f"{g('ttft'):8.3f}{g('intake'):8.4f}{g('admission'):9.4f}"
                      f"{g('fetch'):8.4f}{g('prefill'):8.4f}{g('prequeue'):10.4f}  "
                      f"{nots:6d}{sum(r['tail_1s'] for r in rs):6d}"
                      f"{sum(r['tail_5s'] for r in rs):6d}{sum(r['tail_10s'] for r in rs):6d}")
    print("\n* prequeue = arrival->out_queue for requests with NO scheduler "
          "timestamps; those cannot be split at the scheduler boundary.")
    print("  All segment means are divided by ALL completed requests, so the "
          "columns sum to TTFT.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
