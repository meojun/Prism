#!/usr/bin/env python3
"""TASK 2 -- why is Prism's steady decode 5-15% slower?

Uses per-request itl / decode_timestamps / tpot. The long-gap threshold is not
picked by hand: for each (workload, rate, model) we take the PROTOTYPE arm's
own ITL p99 as the reference for "normal decode spacing under this load", and
count Prism gaps above it. That makes the threshold data-derived and arm-fair
(the control defines normal, the treatment is measured against it).
"""
import csv, json, sys
from pathlib import Path
sys.path.insert(0, "/workspace/prism-exp/exp/analysis")
from model_state_overlap import load_reqs, availability   # noqa: E402

RAW = Path("/workspace/prism-exp/exp/results/4het-paired/raw")
MODELS = ("model_3", "model_4", "model_5", "model_6")
NAME = {"model_3": "Llama-3.2-3B", "model_4": "Qwen2.5-3B",
        "model_5": "Llama-3.1-8B", "model_6": "Qwen2.5-7B"}


def pct(v, q):
    v = sorted(x for x in v if x is not None)
    if not v:
        return None
    k = (len(v) - 1) * q / 100
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def gather(arm, kind, rate):
    per = {m: {"tpot": [], "itl": [], "gaps": [], "ndec": 0, "dur": 0.0,
               "reqs": []} for m in MODELS}
    for seed in (1, 2):
        run = RAW / arm / kind / f"rate_{rate}" / f"seed_{seed}"
        if not list(run.glob("*_e2e_*rep.json")):
            continue
        for r in load_reqs(run):
            if not (isinstance(r, dict) and r.get("success")):
                continue
            m = r.get("model")
            if m not in per:
                continue
            d = per[m]
            if r.get("tpot") is not None:
                d["tpot"].append(r["tpot"])
            itl = r.get("itl") or []
            d["itl"].extend(itl)
            d["ndec"] += len(itl)
            dts = r.get("decode_timestamps") or []
            if len(dts) >= 2:
                d["dur"] += dts[-1] - dts[0]
            d["reqs"].append(r)
    return per


def main():
    rows = []
    for kind in ("bursty", "steady"):
        for rate in (2, 4, 6, 8, 10):
            g = {a: gather(a, kind, rate) for a in ("prototype", "prism")}
            for m in MODELS:
                ref = pct(g["prototype"][m]["itl"], 99)   # data-derived threshold
                for arm in ("prototype", "prism"):
                    d = g[arm][m]
                    if not d["itl"]:
                        continue
                    long_gaps = [x for x in d["itl"] if ref and x > ref]
                    rows.append({
                        "workload": kind, "rate": rate, "arm": arm,
                        "model": m, "model_name": NAME[m],
                        "n_requests": len(d["reqs"]), "n_decode_steps": d["ndec"],
                        "tpot_p50": pct(d["tpot"], 50), "tpot_p95": pct(d["tpot"], 95),
                        "tpot_p99": pct(d["tpot"], 99),
                        "itl_mean": sum(d["itl"]) / len(d["itl"]),
                        "itl_p50": pct(d["itl"], 50), "itl_p95": pct(d["itl"], 95),
                        "itl_p99": pct(d["itl"], 99), "itl_max": max(d["itl"]),
                        "longgap_threshold_s": ref,
                        "longgap_count": len(long_gaps),
                        "longgap_pct_of_steps": round(100 * len(long_gaps) / d["ndec"], 3)
                        if d["ndec"] else None,
                        "longgap_time_s": round(sum(long_gaps), 2),
                        "decode_time_s": round(d["dur"], 1),
                        "longgap_share_of_decode_pct":
                            round(100 * sum(long_gaps) / d["dur"], 2) if d["dur"] else None,
                    })
    out = Path("/workspace/prism-exp/exp/analysis")
    (out / "tpot_itl_forensics.json").write_text(json.dumps(rows, indent=1))
    with (out / "tpot_itl_forensics.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)

    print("\n=== Is the TPOT deficit median-wide or tail-driven?  (steady) ===")
    print(f"{'rate':>5}{'model':>13} | {'itl_p50 P/M':>18}{'ratio':>7} | "
          f"{'itl_p99 P/M':>20}{'ratio':>7} | {'Prism longgap':>14}{'%decode':>9}")
    for rate in (2, 6, 8, 10):
        for m in MODELS:
            p = next((r for r in rows if r["workload"] == "steady" and r["rate"] == rate
                      and r["model"] == m and r["arm"] == "prototype"), None)
            q = next((r for r in rows if r["workload"] == "steady" and r["rate"] == rate
                      and r["model"] == m and r["arm"] == "prism"), None)
            if not (p and q):
                continue
            print(f"{rate:>5}{NAME[m]:>13} | {p['itl_p50']:>9.4f}{q['itl_p50']:>9.4f}"
                  f"{q['itl_p50']/p['itl_p50']:>7.2f} | {p['itl_p99']:>10.4f}"
                  f"{q['itl_p99']:>10.4f}{q['itl_p99']/p['itl_p99']:>7.2f} | "
                  f"{q['longgap_count']:>14}{q['longgap_share_of_decode_pct']:>8.1f}%")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
