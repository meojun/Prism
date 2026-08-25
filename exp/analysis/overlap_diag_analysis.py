#!/usr/bin/env python3
"""Three-arm comparison: Prototype vs Prism overlap-ON vs Prism overlap-OFF.

Controls are the existing validated 4-HET artifacts; only the OFF arm is new.
Every metric goes through the same implementations used for the baseline
(final_metrics.collect, and the Phase 2 residency/ITL code), so ON and OFF are
measured identically.
"""
import csv, json, sys
from pathlib import Path
ROOT = Path("/workspace/prism-exp")
sys.path.insert(0, str(ROOT / "exp/scripts"))
sys.path.insert(0, str(ROOT / "exp/analysis"))
from final_metrics import collect                      # noqa: E402
from model_state_overlap import load_reqs, availability  # noqa: E402
from residency_load import occupancy                    # noqa: E402

PAIRED = ROOT / "exp/results/4het-paired/raw"
DIAG = ROOT / "exp/results/4het-overlap-diagnostic/raw/prism-nooverlap"
CONDS = [("steady", 8, 1), ("steady", 8, 2), ("steady", 10, 1), ("steady", 10, 2),
         ("bursty", 6, 1), ("bursty", 6, 2), ("bursty", 8, 1), ("bursty", 8, 2)]
MODELS = {"model_3": "Llama-3.2-3B", "model_4": "Qwen2.5-3B",
          "model_5": "Llama-3.1-8B", "model_6": "Qwen2.5-7B"}
ARMS = ("prototype", "prism-on", "prism-off")


def rundir(arm, k, r, s):
    if arm == "prototype":
        return PAIRED / "prototype" / k / f"rate_{r}" / f"seed_{s}"
    if arm == "prism-on":
        return PAIRED / "prism" / k / f"rate_{r}" / f"seed_{s}"
    return DIAG / k / f"rate_{r}" / f"seed_{s}"


def pctl(v, q):
    v = sorted(x for x in v if x is not None)
    if not v:
        return None
    kk = (len(v) - 1) * q / 100
    lo, hi = int(kk), min(int(kk) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (kk - lo)


def main():
    rows, place, permodel = [], [], []
    for k, r, s in CONDS:
        for arm in ARMS:
            d = rundir(arm, k, r, s)
            if not list(d.glob("*_e2e_*rep.json")):
                continue
            m = collect(d)
            reqs = [x for x in load_reqs(d) if isinstance(x, dict) and x.get("success")]
            t1 = sum(1 for x in reqs if (x.get("ttft") or 0) > 1)
            t5 = sum(1 for x in reqs if (x.get("ttft") or 0) > 5)
            t10 = sum(1 for x in reqs if (x.get("ttft") or 0) > 10)
            rows.append({"workload": k, "rate": r, "seed": s, "arm": arm,
                         "goodput": m.get("goodput_req_s"),
                         "attainment": m.get("joint_slo_attainment"),
                         "throughput": m.get("achieved_throughput_req_s"),
                         "completed": m.get("completed"), "aborted": m.get("aborted"),
                         "ttft_p50": m.get("ttft_p50"), "ttft_p95": m.get("ttft_p95"),
                         "ttft_p99": m.get("ttft_p99"),
                         "tpot_p50": m.get("tpot_p50"), "tpot_p95": m.get("tpot_p95"),
                         "tpot_p99": m.get("tpot_p99"),
                         "e2e_p50": m.get("e2e_p50"), "e2e_p95": m.get("e2e_p95"),
                         "e2e_p99": m.get("e2e_p99"),
                         "migrations": m.get("migrations_executed"),
                         "weight_bytes": m.get("weight_bytes"),
                         "kv_bytes": m.get("kv_bytes"),
                         "ttft_gt1s": t1, "ttft_gt5s": t5, "ttft_gt10s": t10})
            o = occupancy(d)
            iv, ev = availability(d / "server-logs/server.log")
            unav = 0.0
            for mm, spans in iv.items():
                for i in range(len(spans) - 1):
                    g = spans[i + 1][0] - spans[i][1]
                    if g > 0.001:
                        unav += g
            if o:
                place.append({"workload": k, "rate": r, "seed": s, "arm": arm,
                              "gpu0": o["gpu0"], "gpu1": o["gpu1"],
                              "total_residency": o["gpu0"] + o["gpu1"],
                              "t3plus_pct": o["t3plus_pct"],
                              "activations": sum(1 for e in ev if e[3] == "up"),
                              "deactivations": sum(1 for e in ev if e[3] == "down"),
                              "model_unavailable_s": round(unav, 2)})
            by = {}
            for x in reqs:
                mm = x.get("model")
                if mm in MODELS:
                    by.setdefault(mm, {"tpot": [], "itl": []})
                    by[mm]["tpot"].append(x.get("tpot"))
                    by[mm]["itl"].extend(x.get("itl") or [])
            for mm, dd in by.items():
                permodel.append({"workload": k, "rate": r, "seed": s, "arm": arm,
                                 "model": mm, "model_name": MODELS[mm],
                                 "n": len(dd["tpot"]),
                                 "tpot_p50": pctl(dd["tpot"], 50),
                                 "tpot_p95": pctl(dd["tpot"], 95),
                                 "tpot_p99": pctl(dd["tpot"], 99),
                                 "itl_mean": (sum(dd["itl"]) / len(dd["itl"])
                                              if dd["itl"] else None),
                                 "itl_p50": pctl(dd["itl"], 50),
                                 "itl_p95": pctl(dd["itl"], 95),
                                 "itl_p99": pctl(dd["itl"], 99)})
    out = ROOT / "exp/results/4het-overlap-diagnostic/analysis"
    out.mkdir(parents=True, exist_ok=True)
    for name, data in (("runs", rows), ("placement", place), ("per_model", permodel)):
        (out / f"{name}.json").write_text(json.dumps(data, indent=1))
        if data:
            with (out / f"{name}.csv").open("w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(data[0]))
                w.writeheader(); w.writerows(data)

    def g(k, r, s, arm, field, src=rows):
        for x in src:
            if (x["workload"], x["rate"], x["seed"], x["arm"]) == (k, r, s, arm):
                return x.get(field)
        return None

    print("=== Goodput / attainment / throughput, per seed ===")
    print(f"{'cond':>12}{'seed':>5} | " + "".join(f"{a:>26}" for a in ARMS))
    print(f"{'':>12}{'':>5} | " + "".join(f"{'gp':>9}{'attain':>9}{'thr':>8}" for _ in ARMS))
    for k, r, s in CONDS:
        line = f"{k+str(r):>12}{s:>5} | "
        for a in ARMS:
            gp, at, th = g(k, r, s, a, "goodput"), g(k, r, s, a, "attainment"), g(k, r, s, a, "throughput")
            line += (f"{gp:>9.4f}{at:>9.4f}{th:>8.2f}" if gp is not None
                     else f"{'--':>9}{'--':>9}{'--':>8}")
        print(line)

    print("\n=== Placement: time with >=3 models on one GPU ===")
    print(f"{'cond':>12}{'seed':>5} | " + "".join(f"{a:>30}" for a in ARMS))
    print(f"{'':>12}{'':>5} | " + "".join(f"{'t3+%':>7}{'total':>7}{'mig':>5}{'act':>5}{'unav_s':>6}" for _ in ARMS))
    for k, r, s in CONDS:
        line = f"{k+str(r):>12}{s:>5} | "
        for a in ARMS:
            p = next((x for x in place if (x["workload"], x["rate"], x["seed"], x["arm"]) == (k, r, s, a)), None)
            mg = g(k, r, s, a, "migrations")
            line += (f"{p['t3plus_pct']:>6.1f}%{p['total_residency']:>7.2f}"
                     f"{mg if mg is not None else 0:>5}{p['activations']:>5}"
                     f"{p['model_unavailable_s']:>6.0f}" if p else f"{'--':>31}")
        print(line)
    print(f"\nwrote {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
