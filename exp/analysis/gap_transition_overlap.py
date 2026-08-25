#!/usr/bin/env python3
"""Do Prism's long decode gaps sit on model state transitions?

Each long ITL gap is an interval [t_i, t_{i+1}] in absolute time from
decode_timestamps. We ask whether a transition of that request's own model
falls inside it, and compare against the chance rate implied by the gap's own
length (lambda = transitions/span), so a longer gap is not credited for free.
"""
import json, math, sys
from pathlib import Path
sys.path.insert(0, "/workspace/prism-exp/exp/analysis")
from model_state_overlap import load_reqs, availability   # noqa: E402

RAW = Path("/workspace/prism-exp/exp/results/4het-paired/raw")


def pct(v, q):
    v = sorted(v)
    if not v:
        return None
    k = (len(v) - 1) * q / 100
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def main():
    print("Long decode gaps vs state transitions of the same model (Prism)\n")
    print(f"{'cond':>10}{'gaps':>8}{'onTrans':>9}{'obs%':>7}{'exp%':>7}{'excess':>8}"
          f"{'gapTime_s':>11}{'onTransTime':>12}{'share':>7}")
    out = []
    for kind in ("bursty", "steady"):
        for rate in (2, 6, 8, 10):
            # threshold from the prototype arm's own ITL p99 in this condition
            ref_itl = []
            for seed in (1, 2):
                run = RAW / "prototype" / kind / f"rate_{rate}" / f"seed_{seed}"
                for r in load_reqs(run):
                    if isinstance(r, dict) and r.get("success"):
                        ref_itl.extend(r.get("itl") or [])
            ref = pct(ref_itl, 99)
            if not ref:
                continue
            ngap = onT = 0
            gt = ot = 0.0
            expn = 0.0
            for seed in (1, 2):
                run = RAW / "prism" / kind / f"rate_{rate}" / f"seed_{seed}"
                if not list(run.glob("*_e2e_*rep.json")):
                    continue
                iv, ev = availability(run / "server-logs/server.log")
                by = {}
                for t, m, g, k in ev:
                    by.setdefault(m, []).append(t)
                for m in by:
                    by[m].sort()
                span = (max(e[0] for e in ev) - min(e[0] for e in ev)) if ev else 1
                for r in load_reqs(run):
                    if not (isinstance(r, dict) and r.get("success")):
                        continue
                    m = r.get("model")
                    dts = r.get("decode_timestamps") or []
                    if m not in by or len(dts) < 2:
                        continue
                    lam = len(by[m]) / span if span else 0
                    for i in range(len(dts) - 1):
                        d = dts[i + 1] - dts[i]
                        if d <= ref:
                            continue
                        ngap += 1
                        gt += d
                        expn += 1 - math.exp(-lam * d)
                        if any(dts[i] <= x <= dts[i + 1] for x in by[m]):
                            onT += 1
                            ot += d
            if not ngap:
                continue
            obs = 100 * onT / ngap
            exp = 100 * expn / ngap
            out.append(dict(cond=f"{kind}{rate}", gaps=ngap, on_trans=onT,
                            obs_pct=obs, exp_pct=exp, gap_time=gt, on_trans_time=ot))
            print(f"{kind+str(rate):>10}{ngap:>8}{onT:>9}{obs:>6.1f}%{exp:>6.1f}%"
                  f"{(obs/exp if exp else 0):>7.1f}x{gt:>11.0f}{ot:>12.0f}"
                  f"{100*ot/gt if gt else 0:>6.0f}%")
    Path("/workspace/prism-exp/exp/analysis/gap_transition_overlap.json").write_text(
        json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
