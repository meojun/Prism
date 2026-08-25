#!/usr/bin/env python3
"""Per-model fairness/starvation (E) and SLO failure decomposition (F)."""
import json, sys
from pathlib import Path
sys.path.insert(0, "/workspace/prism-exp/exp/analysis")
from model_state_overlap import load_reqs   # noqa: E402

RAW = Path("/workspace/prism-exp/exp/results/4het-paired/raw")
MODELS = ("model_3", "model_4", "model_5", "model_6")
NAMES = {"model_3": "Llama-3.2-3B", "model_4": "Qwen2.5-3B",
         "model_5": "Llama-3.1-8B", "model_6": "Qwen2.5-7B"}


def pct(v, q):
    v = sorted(x for x in v if x is not None)
    if not v:
        return None
    k = (len(v) - 1) * q / 100
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def collect(arm, kind, rate):
    per = {m: {"n": 0, "pass": 0, "ttft_only": 0, "tpot_only": 0, "both_fail": 0,
               "t1": 0, "t5": 0, "t10": 0, "ttft": [], "tpot": []}
           for m in MODELS}
    dur = 0.0
    for seed in (1, 2):
        run = RAW / arm / kind / f"rate_{rate}" / f"seed_{seed}"
        if not list(run.glob("*_e2e_*rep.json")):
            continue
        reqs = [r for r in load_reqs(run) if isinstance(r, dict)]
        ok = [r for r in reqs if r.get("success")]
        if ok:
            dur += (max(r["finish_time"] for r in ok if r.get("finish_time"))
                    - min(r["arrival_time"] for r in ok if r.get("arrival_time")))
        for r in ok:
            m = r.get("model")
            if m not in per:
                continue
            d = per[m]
            d["n"] += 1
            t, p = r.get("ttft"), r.get("tpot")
            st, sp = r.get("slo_ttft"), r.get("slo_tpot")
            d["ttft"].append(t)
            d["tpot"].append(p)
            a = st is not None and t is not None and t <= st
            b = sp is not None and p is not None and p <= sp
            d["pass" if (a and b) else
              "tpot_only" if (a and not b) else
              "ttft_only" if (b and not a) else "both_fail"] += 1
            for thr, k in ((1, "t1"), (5, "t5"), (10, "t10")):
                if t and t > thr:
                    d[k] += 1
    return per, dur / 2 if dur else 1.0


def main():
    rows = []
    for kind in ("bursty", "steady"):
        for rate in (2, 4, 6, 8, 10):
            for arm in ("prototype", "prism"):
                per, dur = collect(arm, kind, rate)
                for m in MODELS:
                    d = per[m]
                    if not d["n"]:
                        continue
                    rows.append({
                        "arm": arm, "workload": kind, "rate": rate, "model": m,
                        "model_name": NAMES[m], "n": d["n"],
                        "goodput_req_s": round(d["pass"] / dur, 4),
                        "attainment": round(d["pass"] / d["n"], 4),
                        "pass": d["pass"], "ttft_only_fail": d["ttft_only"],
                        "tpot_only_fail": d["tpot_only"], "both_fail": d["both_fail"],
                        "ttft_p50": pct(d["ttft"], 50), "ttft_p95": pct(d["ttft"], 95),
                        "ttft_p99": pct(d["ttft"], 99),
                        "tpot_p50": pct(d["tpot"], 50), "tpot_p95": pct(d["tpot"], 95),
                        "tpot_p99": pct(d["tpot"], 99),
                        "gt1s": d["t1"], "gt5s": d["t5"], "gt10s": d["t10"]})
    Path("/workspace/prism-exp/exp/analysis/permodel_slo.json").write_text(
        json.dumps(rows, indent=1))

    print("\n=== F. SLO failure decomposition (all models pooled) ===")
    print(f"{'cond':>10} {'arm':<10}{'n':>7}{'PASS':>8}{'TTFT-only':>11}"
          f"{'TPOT-only':>11}{'both':>8}   {'TTFT share of failures':>22}")
    for kind in ("bursty", "steady"):
        for rate in (2, 4, 6, 8, 10):
            for arm in ("prototype", "prism"):
                rs = [r for r in rows if r["arm"] == arm and r["workload"] == kind
                      and r["rate"] == rate]
                if not rs:
                    continue
                n = sum(r["n"] for r in rs); p = sum(r["pass"] for r in rs)
                to = sum(r["ttft_only_fail"] for r in rs)
                po = sum(r["tpot_only_fail"] for r in rs)
                bf = sum(r["both_fail"] for r in rs)
                fail = n - p
                share = 100 * (to + bf) / fail if fail else 0
                print(f"{kind+str(rate):>10} {arm:<10}{n:>7}{p:>8}{to:>11}{po:>11}"
                      f"{bf:>8}   {share:>21.0f}%")

    print("\n=== E. Per-model, Prism, steady r8 (starvation check) ===")
    print(f"{'model':>22}{'n':>7}{'attain':>8}{'ttft_p50':>10}{'ttft_p95':>10}"
          f"{'ttft_p99':>10}{'>1s':>6}{'>5s':>6}{'>10s':>6}")
    for arm in ("prototype", "prism"):
        print(f"  -- {arm}")
        for r in rows:
            if r["arm"] == arm and r["workload"] == "steady" and r["rate"] == 8:
                print(f"{r['model_name']:>22}{r['n']:>7}{r['attainment']:>8.3f}"
                      f"{r['ttft_p50']:>10.3f}{r['ttft_p95']:>10.3f}"
                      f"{r['ttft_p99']:>10.3f}{r['gt1s']:>6}{r['gt5s']:>6}{r['gt10s']:>6}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
