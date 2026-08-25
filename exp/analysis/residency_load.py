#!/usr/bin/env python3
"""Time-weighted co-residency per GPU over the ACTUAL request window.

Prototype's placement is static (4 activations, 0 deactivations), so its event
span is degenerate; the window must come from the requests, not the log events.
"""
import json, sys
from pathlib import Path
sys.path.insert(0, "/workspace/prism-exp/exp/analysis")
from model_state_overlap import load_reqs, availability   # noqa: E402
RAW = Path("/workspace/prism-exp/exp/results/4het-paired/raw")


def occupancy(run):
    reqs = [r for r in load_reqs(run) if isinstance(r, dict) and r.get("success")]
    if not reqs:
        return None
    t0 = min(r["arrival_time"] for r in reqs)
    t1 = max(r["finish_time"] for r in reqs)
    _iv, ev = availability(run / "server-logs/server.log")
    ev = sorted(ev)
    live = {0: set(), 1: set()}
    for t, m, g, k in ev:            # apply everything before the window
        if t > t0:
            break
        (live[g].add if k == "up" else live[g].discard)(m)
    acc = {0: 0.0, 1: 0.0}
    t3 = 0.0
    prev = t0
    for t, m, g, k in ev:
        if t <= t0:
            continue
        t = min(t, t1)
        dt = max(0.0, t - prev)
        for gg in (0, 1):
            acc[gg] += len(live[gg]) * dt
            if len(live[gg]) >= 3:
                t3 += dt
        prev = t
        if t >= t1:
            break
        (live[g].add if k == "up" else live[g].discard)(m)
    dt = max(0.0, t1 - prev)
    for gg in (0, 1):
        acc[gg] += len(live[gg]) * dt
        if len(live[gg]) >= 3:
            t3 += dt
    span = t1 - t0
    return {"span": span, "gpu0": acc[0] / span, "gpu1": acc[1] / span,
            "t3plus_s": t3, "t3plus_pct": 100 * t3 / span}


def main():
    print("Time-weighted models resident per GPU, over the request window\n")
    print(f"{'cond':>10}{'arm':>11}{'span_s':>8}{'GPU0':>7}{'GPU1':>7}{'total':>7}"
          f"{'t>=3 on one GPU':>17}")
    rows = []
    for kind in ("bursty", "steady"):
        for rate in (2, 4, 6, 8, 10):
            for arm in ("prototype", "prism"):
                accs = []
                for seed in (1, 2):
                    run = RAW / arm / kind / f"rate_{rate}" / f"seed_{seed}"
                    if not list(run.glob("*_e2e_*rep.json")):
                        continue
                    o = occupancy(run)
                    if o:
                        accs.append(o)
                if not accs:
                    continue
                f = lambda k: sum(a[k] for a in accs) / len(accs)
                rows.append(dict(workload=kind, rate=rate, arm=arm,
                                 gpu0=f("gpu0"), gpu1=f("gpu1"),
                                 t3plus_pct=f("t3plus_pct"), span=f("span")))
                print(f"{kind+str(rate):>10}{arm:>11}{f('span'):>8.0f}{f('gpu0'):>7.2f}"
                      f"{f('gpu1'):>7.2f}{f('gpu0')+f('gpu1'):>7.2f}"
                      f"{f('t3plus_pct'):>16.1f}%")
            print()
    Path("/workspace/prism-exp/exp/analysis/residency_load.json").write_text(
        json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
