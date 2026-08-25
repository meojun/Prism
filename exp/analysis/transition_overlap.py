#!/usr/bin/env python3
"""Do tail requests wait ACROSS a state transition of their own model?

For every completed request we count how many activate/deactivate events for
that request's own model fall inside its pre-engine window [arrival,out_queue],
and compare tail buckets against the non-tail baseline in the same run. If the
tail is caused by residency churn, tails should straddle transitions while
non-tails do not.
"""
import json, re, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, "/workspace/prism-exp/exp/analysis")
from model_state_overlap import availability, load_reqs   # noqa: E402

ROOT = Path("/workspace/prism-exp")
RAW = ROOT / "exp/results/4het-paired/raw"
BUCKETS = (("<=1s", 0, 1), ("1-5s", 1, 5), ("5-10s", 5, 10), (">10s", 10, 1e9))


def run_stats(run):
    log = run / "server-logs/server.log"
    if not log.is_file():
        return None
    _iv, ev = availability(log)
    by_model = {}
    for t, m, g, kind in ev:
        by_model.setdefault(m, []).append(t)
    for m in by_model:
        by_model[m].sort()
    out = {b[0]: {"n": 0, "n_straddle": 0, "trans": 0, "wait": 0.0, "nots": 0}
           for b in BUCKETS}
    for r in load_reqs(run):
        if not (isinstance(r, dict) and r.get("success")):
            continue
        a, o, t, m = (r.get("arrival_time") or 0, r.get("out_queue_time") or 0,
                      r.get("ttft"), r.get("model"))
        if not (a and o and t is not None and m):
            continue
        k = sum(1 for x in by_model.get(m, []) if a <= x <= o)
        for label, lo, hi in BUCKETS:
            if (lo == 0 and t <= hi) or (lo < t <= hi):
                d = out[label]
                d["n"] += 1
                d["trans"] += k
                d["wait"] += max(0.0, o - a)
                if k:
                    d["n_straddle"] += 1
                if not (r.get("gpu_scheduler_queue_time") or 0):
                    d["nots"] += 1
                break
    return out


def main():
    arm = sys.argv[1] if len(sys.argv) > 1 else "prism"
    agg = {}
    for kind in ("bursty", "steady"):
        for rate in (2, 4, 6, 8, 10):
            tot = {b[0]: {"n": 0, "n_straddle": 0, "trans": 0, "wait": 0.0,
                          "nots": 0} for b in BUCKETS}
            for seed in (1, 2):
                run = RAW / arm / kind / f"rate_{rate}" / f"seed_{seed}"
                if not list(run.glob("*_e2e_*rep.json")):
                    continue
                s = run_stats(run)
                if not s:
                    continue
                for b in tot:
                    for k in tot[b]:
                        tot[b][k] += s[b][k]
            agg[f"{kind}{rate}"] = tot
    (ROOT / f"exp/analysis/transition_overlap_{arm}.json").write_text(
        json.dumps(agg, indent=1))
    print(f"\n{arm}: % of requests whose [arrival->out_queue] window contains "
          f"a state transition of their OWN model\n")
    print(f"{'cond':>10} | " + "".join(f"{b[0]:>22}" for b in BUCKETS))
    print(f"{'':>10} | " + "".join(f"{'n':>7}{'straddle%':>10}{'noTS%':>5}"
                                   for _ in BUCKETS))
    for cond, tot in agg.items():
        line = f"{cond:>10} | "
        for b, _, _ in BUCKETS:
            d = tot[b]
            n = d["n"]
            line += (f"{n:>7}{100*d['n_straddle']/n if n else 0:>9.0f}%"
                     f"{100*d['nots']/n if n else 0:>4.0f}%")
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
