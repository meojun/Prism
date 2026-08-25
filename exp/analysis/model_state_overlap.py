#!/usr/bin/env python3
"""Do tail requests wait while their model is not servable anywhere?

Model availability is reconstructed from the server's own log:

  "[ts GPU=g Worker=w TP0] Activate model model_X (path) time cost: T"
        -> model_X is servable on GPU g from ts onward
           (activation itself occupied [ts-T, ts])
  "[ts GPU=g Worker w (model_X) TP=0] Deactivate time cost: T"
        -> model_X stops being servable on GPU g at ts

A model is UNAVAILABLE at time t when no GPU holds it activated at t.
For every completed request we take its pre-engine window [arrival, out_queue]
and measure how much of it overlaps an unavailable period. That is an overlap
in time, not a correlation across runs.
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/workspace/prism-exp")
RAW = ROOT / "exp/results/4het-paired/raw"
RATES = (2, 4, 6, 8, 10)
SEEDS = (1, 2)
KINDS = ("bursty", "steady")

ACT = re.compile(r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+) GPU=(\d+) Worker[= ](\d+).*?\]"
                 r" Activate model (model_\d+).*?time cost: ([\d.]+)s")
DEACT = re.compile(r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+) GPU=(\d+) Worker (\d+) "
                   r"\((model_\d+)\).*?\] Deactivate time cost: ([\d.]+)s")


def ts(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S.%f").replace(
        tzinfo=timezone.utc).timestamp()


def availability(log):
    """model -> sorted list of (start, end) intervals when it was servable."""
    text = log.read_text(errors="replace")
    ev = []
    for m in ACT.finditer(text):
        ev.append((ts(m.group(1)), m.group(4), int(m.group(2)), "up"))
    for m in DEACT.finditer(text):
        ev.append((ts(m.group(1)), m.group(4), int(m.group(2)), "down"))
    ev.sort()
    live, iv, open_at = {}, {}, {}
    for t, model, gpu, kind in ev:
        s = live.setdefault(model, set())
        before = bool(s)
        if kind == "up":
            s.add(gpu)
        else:
            s.discard(gpu)
        after = bool(s)
        if not before and after:
            open_at[model] = t
        elif before and not after:
            iv.setdefault(model, []).append((open_at.get(model, t), t))
            open_at.pop(model, None)
    for model, t0 in open_at.items():
        iv.setdefault(model, []).append((t0, float("inf")))
    return iv, ev


def unavailable_overlap(iv, model, a, b):
    """seconds of [a,b] during which `model` was servable nowhere."""
    if b <= a:
        return 0.0
    spans = iv.get(model, [])
    covered = 0.0
    for s, e in spans:
        lo, hi = max(a, s), min(b, e)
        if hi > lo:
            covered += hi - lo
    return max(0.0, (b - a) - covered)


def load_reqs(run):
    for f in sorted((run / "requests").glob("*_output_requests.json")):
        try:
            d = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        if isinstance(d, list) and d:
            return d
    return []


def analyse(run):
    log = run / "server-logs/server.log"
    if not log.is_file():
        return None
    iv, ev = availability(log)
    reqs = [r for r in load_reqs(run) if isinstance(r, dict) and r.get("success")]
    res = {"n": 0, "activations": sum(1 for e in ev if e[3] == "up"),
           "deactivations": sum(1 for e in ev if e[3] == "down"),
           "buckets": {}}
    for label, lo, hi in (("<=1s", 0, 1), ("1-5s", 1, 5), ("5-10s", 5, 10),
                          (">10s", 10, 1e9)):
        res["buckets"][label] = {"n": 0, "wait_sum": 0.0, "unavail_sum": 0.0,
                                 "n_any_unavail": 0}
    for r in reqs:
        a = r.get("arrival_time") or 0
        o = r.get("out_queue_time") or 0
        t = r.get("ttft")
        m = r.get("model")
        if not (a and o and t is not None and m):
            continue
        res["n"] += 1
        wait = max(0.0, o - a)
        un = unavailable_overlap(iv, m, a, o)
        for label, lo, hi in (("<=1s", 0, 1), ("1-5s", 1, 5), ("5-10s", 5, 10),
                              (">10s", 10, 1e9)):
            if lo < t <= hi or (lo == 0 and t <= hi):
                b = res["buckets"][label]
                b["n"] += 1
                b["wait_sum"] += wait
                b["unavail_sum"] += un
                if un > 0.001:
                    b["n_any_unavail"] += 1
                break
    return res


def main():
    arm = sys.argv[1] if len(sys.argv) > 1 else "prism"
    rows = []
    for kind in KINDS:
        for rate in RATES:
            for seed in SEEDS:
                run = RAW / arm / kind / f"rate_{rate}" / f"seed_{seed}"
                if not list(run.glob("*_e2e_*rep.json")):
                    continue
                a = analyse(run)
                if a:
                    a.update(arm=arm, workload=kind, rate=rate, seed=seed)
                    rows.append(a)
    (ROOT / f"exp/analysis/model_state_overlap_{arm}.json").write_text(
        json.dumps(rows, indent=1))
    print(f"\n{arm}: pre-engine wait [arrival -> out_queue] vs time the model "
          f"was servable NOWHERE\n")
    print(f"{'cond':>12} {'act':>4}{'deact':>6} | "
          + "".join(f"{b:>26}" for b in ("1-5s", "5-10s", ">10s")))
    print(f"{'':>12} {'':>4}{'':>6} | "
          + "".join(f"{'n':>6}{'wait':>7}{'unavail':>8}{'%':>5}" for _ in range(3)))
    for kind in KINDS:
        for rate in RATES:
            rs = [r for r in rows if r["workload"] == kind and r["rate"] == rate]
            if not rs:
                continue
            line = (f"{kind+str(rate):>12} {sum(r['activations'] for r in rs):>4}"
                    f"{sum(r['deactivations'] for r in rs):>6} | ")
            for label in ("1-5s", "5-10s", ">10s"):
                n = sum(r["buckets"][label]["n"] for r in rs)
                w = sum(r["buckets"][label]["wait_sum"] for r in rs)
                u = sum(r["buckets"][label]["unavail_sum"] for r in rs)
                line += (f"{n:>6}{w/n if n else 0:>7.2f}{u/n if n else 0:>8.2f}"
                         f"{100*u/w if w else 0:>4.0f}%")
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
