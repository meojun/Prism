#!/usr/bin/env python3
"""TASK 1 -- how much of the steady gap is real latency, how much is the cliff?

Sensitivity only. The primary SLO (scale 1.0) is unchanged and is reported
alongside; nothing here replaces it. Each request already carries its own
slo_ttft / slo_tpot (base x5 / base x3); we multiply those by a factor and
recount. No re-run, no threshold change in the experiment itself.
"""
import csv, json, sys
from pathlib import Path
sys.path.insert(0, "/workspace/prism-exp/exp/analysis")
from model_state_overlap import load_reqs   # noqa: E402

RAW = Path("/workspace/prism-exp/exp/results/4het-paired/raw")
SCALES = (0.8, 1.0, 1.2, 1.5, 2.0)


def run_reqs(arm, kind, rate):
    out, dur = [], 0.0
    for seed in (1, 2):
        run = RAW / arm / kind / f"rate_{rate}" / f"seed_{seed}"
        if not list(run.glob("*_e2e_*rep.json")):
            continue
        rs = [r for r in load_reqs(run) if isinstance(r, dict) and r.get("success")]
        if rs:
            dur += (max(r["finish_time"] for r in rs)
                    - min(r["arrival_time"] for r in rs))
        out += rs
    return out, (dur / 2 if dur else 1.0)


def score(reqs, dur, ttft_s, tpot_s):
    p = to = po = bf = 0
    for r in reqs:
        t, q = r.get("ttft"), r.get("tpot")
        st, sq = r.get("slo_ttft"), r.get("slo_tpot")
        if st is None or sq is None:
            continue
        a = t is not None and t <= st * ttft_s
        b = q is not None and q <= sq * tpot_s
        if a and b:
            p += 1
        elif a:
            po += 1
        elif b:
            to += 1
        else:
            bf += 1
    n = p + to + po + bf
    return {"n": n, "pass": p, "goodput_req_s": round(p / dur, 4),
            "attainment": round(p / n, 4) if n else None,
            "ttft_only_fail_pct": round(100 * to / n, 2) if n else None,
            "tpot_only_fail_pct": round(100 * po / n, 2) if n else None,
            "both_fail_pct": round(100 * bf / n, 2) if n else None}


def main():
    rows = []
    for kind in ("bursty", "steady"):
        for rate in (2, 4, 6, 8, 10):
            data = {a: run_reqs(a, kind, rate) for a in ("prototype", "prism")}
            for ts in SCALES:
                for ps in SCALES:
                    if ts != 1.0 and ps != 1.0:
                        continue          # 1-D sweeps along each axis
                    rec = {"workload": kind, "rate": rate,
                           "ttft_scale": ts, "tpot_scale": ps}
                    for arm in ("prototype", "prism"):
                        reqs, dur = data[arm]
                        s = score(reqs, dur, ts, ps)
                        for k, v in s.items():
                            rec[f"{arm}_{k}"] = v
                    pg, mg = rec["prototype_goodput_req_s"], rec["prism_goodput_req_s"]
                    rec["delta_pct_goodput"] = (round(100 * (mg - pg) / pg, 2)
                                                if pg else None)
                    rows.append(rec)
    out = Path("/workspace/prism-exp/exp/analysis")
    (out / "slo_sensitivity.json").write_text(json.dumps(rows, indent=1))
    with (out / "slo_sensitivity.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)

    def show(kind, axis):
        print(f"\n### {kind} -- scaling {axis} only (goodput Δ%, Prism vs Prototype)")
        print(f"{'rate':>5} " + "".join(f"{s:>9.1f}x" for s in SCALES))
        for rate in (2, 4, 6, 8, 10):
            line = f"{rate:>5} "
            for s in SCALES:
                r = next((x for x in rows if x["workload"] == kind
                          and x["rate"] == rate
                          and (x["ttft_scale"] == s and x["tpot_scale"] == 1.0
                               if axis == "TTFT" else
                               x["tpot_scale"] == s and x["ttft_scale"] == 1.0)), None)
                line += f"{r['delta_pct_goodput']:>9.1f}%" if r else f"{'--':>10}"
            print(line)
    for kind in ("steady", "bursty"):
        show(kind, "TPOT")
        show(kind, "TTFT")

    print("\n### steady r6/r8/r10 detail -- TPOT axis")
    print(f"{'rate':>5}{'scale':>7} {'proto gp':>9}{'prism gp':>9}{'Δ%':>8} "
          f"{'prism attain':>13}{'TTFTonly%':>10}{'TPOTonly%':>10}{'both%':>7}")
    for rate in (6, 8, 10):
        for s in SCALES:
            r = next(x for x in rows if x["workload"] == "steady"
                     and x["rate"] == rate and x["tpot_scale"] == s
                     and x["ttft_scale"] == 1.0)
            print(f"{rate:>5}{s:>7.1f} {r['prototype_goodput_req_s']:>9.3f}"
                  f"{r['prism_goodput_req_s']:>9.3f}{r['delta_pct_goodput']:>8.1f}"
                  f"{r['prism_attainment']:>13.3f}{r['prism_ttft_only_fail_pct']:>10.1f}"
                  f"{r['prism_tpot_only_fail_pct']:>10.1f}{r['prism_both_fail_pct']:>7.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
