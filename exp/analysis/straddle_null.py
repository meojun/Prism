#!/usr/bin/env python3
"""Is the tail/transition overlap more than a long window would give by chance?

A longer wait mechanically has more opportunity to contain a transition. The
null model makes that explicit: transitions of a given model arrive at rate
lambda_m = (transitions of that model) / (run span). For a request that waited
W seconds, the chance its window contains at least one is 1 - exp(-lambda_m*W).
Summing that over the requests in a bucket gives the number of straddles the
window lengths ALONE would produce. Observed / expected is the excess.
"""
import json, math, sys
from pathlib import Path
sys.path.insert(0, "/workspace/prism-exp/exp/analysis")
from model_state_overlap import availability, load_reqs   # noqa: E402

RAW = Path("/workspace/prism-exp/exp/results/4het-paired/raw")
BUCKETS = (("<=1s", 0, 1), ("1-5s", 1, 5), ("5-10s", 5, 10), (">10s", 10, 1e9))


def main():
    arm = "prism"
    print(f"\n{arm}: observed vs chance-expected straddles "
          f"(window length held responsible)\n")
    print(f"{'cond':>10} | " + "".join(f"{b[0]:>26}" for b in BUCKETS[1:]))
    print(f"{'':>10} | " + "".join(f"{'obs':>7}{'exp':>8}{'excess':>11}"
                                   for _ in BUCKETS[1:]))
    out = {}
    for kind in ("bursty", "steady"):
        for rate in (2, 4, 6, 8, 10):
            tot = {b[0]: [0, 0.0] for b in BUCKETS}
            for seed in (1, 2):
                run = RAW / arm / kind / f"rate_{rate}" / f"seed_{seed}"
                if not list(run.glob("*_e2e_*rep.json")):
                    continue
                _iv, ev = availability(run / "server-logs/server.log")
                by_model = {}
                for t, m, g, k in ev:
                    by_model.setdefault(m, []).append(t)
                reqs = [r for r in load_reqs(run)
                        if isinstance(r, dict) and r.get("success")
                        and r.get("arrival_time") and r.get("out_queue_time")]
                if not reqs:
                    continue
                span = (max(r["out_queue_time"] for r in reqs)
                        - min(r["arrival_time"] for r in reqs))
                lam = {m: len(v) / span for m, v in by_model.items() if span > 0}
                for r in reqs:
                    a, o, t, m = (r["arrival_time"], r["out_queue_time"],
                                  r.get("ttft"), r.get("model"))
                    if t is None or m not in lam:
                        continue
                    W = max(0.0, o - a)
                    k = sum(1 for x in by_model[m] if a <= x <= o)
                    p = 1 - math.exp(-lam[m] * W)
                    for label, lo, hi in BUCKETS:
                        if (lo == 0 and t <= hi) or (lo < t <= hi):
                            tot[label][0] += 1 if k else 0
                            tot[label][1] += p
                            break
            out[f"{kind}{rate}"] = {b: tot[b] for b in tot}
            line = f"{kind+str(rate):>10} | "
            for b, _, _ in BUCKETS[1:]:
                obs, exp = tot[b]
                ex = obs / exp if exp > 0.5 else float("nan")
                line += (f"{obs:>7}{exp:>8.1f}"
                         + (f"{ex:>10.1f}x" if ex == ex else f"{'--':>11}"))
            print(line)
    Path("/workspace/prism-exp/exp/analysis/straddle_null.json").write_text(
        json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
