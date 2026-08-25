#!/usr/bin/env python3
"""Where the 4-HET paired evaluation is, and how much longer it has."""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "exp/results/4het-paired"
PROGRESS = OUT / "P4HET_PROGRESS.jsonl"
SHARED_STOP = ROOT / "exp/results/final-evaluation/STOP"

RATES = (2, 4, 6, 8, 10)
SEEDS = (1, 2)
KINDS = ("bursty", "steady")
ARMS = ("prototype", "prism")
# Wall-clock prior: a run is server start (~2.5 min for 4 models) + a 420 s
# trace + drain, and the drain grows with the rate.
PRIOR = {2: 560, 4: 600, 6: 640, 8: 680, 10: 720}


def plan():
    return [(k, r, s, a) for k in KINDS for r in RATES for s in SEEDS for a in ARMS]


def main():
    runs = []
    if PROGRESS.is_file():
        for line in PROGRESS.read_text().splitlines():
            if line.strip():
                try:
                    runs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    done = [r for r in runs if r.get("ok")]
    failed = [r for r in runs if not r.get("ok")]
    full = plan()
    remaining = full[len(runs):]

    scale, basis = 1.0, "prior wall-clock estimate"
    if done:
        num = sum(r["seconds"] for r in done)
        den = sum(PRIOR[r["rate"]] for r in done)
        if den:
            scale = num / den
            basis = f"this server's {len(done)} completed run(s)"
    eta = sum(PRIOR[r] * scale for (_, r, _, _) in remaining)

    print(f"4-HET paired evaluation -- {len(done)}/40 complete"
          + (f", {len(failed)} failed" if failed else ""))
    if SHARED_STOP.is_file():
        print(f"  STOP in force: {SHARED_STOP.read_text().strip()[:120]}")
    if done:
        print(f"  mean wall      : {sum(r['seconds'] for r in done)/len(done)/60:.1f} min/run")
    print(f"  estimate basis : {basis} (scale {scale:.2f})")
    if remaining:
        k, r, s, a = remaining[0]
        print(f"  next           : {a} {k} r{r} s{s}")
        print(f"  remaining      : {len(remaining)} runs, ~{eta/3600:.1f} h "
              f"(~{time.strftime('%H:%M UTC', time.gmtime(time.time()+eta))})")
    else:
        print("  remaining      : none -- sweep finished")

    if runs:
        print("\n  completed:")
        for r in runs:
            n = r.get("numbers") or {}
            g = n.get("joint_slo_goodput_req_s")
            g = f"{g:.4f}" if isinstance(g, (int, float)) else "?"
            print(f"    [{r['idx']:2d}/40] {r['arm']:9s} {r['workload']:6s} "
                  f"r{r['rate']:<2d} s{r['seed']}  {r['seconds']/60:5.1f} min  "
                  f"{'OK  ' if r.get('ok') else 'FAIL'}  "
                  f"completed={n.get('completed')}/{n.get('offered_requests')}  "
                  f"goodput={g}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
