#!/usr/bin/env python3
"""Split decode behaviour by how many models shared the model's GPU at the time.

Placement timeline comes from the ALG1 cycles' own `current_placement` field
(one sample per cycle, ~5 s apart). A decode interval is attributed to the
placement state in force at its start. Within-run comparison only.
"""
import bisect, csv, json, sys
from pathlib import Path
ROOT = Path("/workspace/prism-exp")
sys.path.insert(0, str(ROOT / "exp/analysis"))
from model_state_overlap import load_reqs   # noqa: E402
RAW = ROOT / "exp/results/4het-paired/raw/prism"
OUT = ROOT / "exp/analysis/alg1"
CONDS = [("steady", 8, 1), ("steady", 8, 2), ("steady", 10, 1), ("steady", 10, 2)]
NAME = {"model_3": "Llama-3.2-3B", "model_4": "Qwen2.5-3B",
        "model_5": "Llama-3.1-8B", "model_6": "Qwen2.5-7B"}


def pct(v, q):
    v = sorted(v)
    if not v:
        return None
    k = (len(v) - 1) * q / 100
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def timeline(cycles):
    ts, states = [], []
    for c in sorted(cycles, key=lambda x: x["timestamp"]):
        p = json.loads(c["current_placement"])
        if not p:
            continue
        cnt = {}
        for m, g in p.items():
            cnt[g] = cnt.get(g, 0) + 1
        ts.append(c["timestamp"])
        states.append({m: cnt[g] for m, g in p.items()})
    return ts, states


def main():
    allc = json.loads((OUT / "alg1_cycles.json").read_text())
    rows = []
    for kind, rate, seed in CONDS:
        cyc = [c for c in allc if (c["workload"], c["rate"], c["seed"]) == (kind, rate, seed)]
        if not cyc:
            continue
        ts, states = timeline(cyc)
        if not ts:
            continue
        run = RAW / kind / f"rate_{rate}" / f"seed_{seed}"
        buckets = {}
        for r in load_reqs(run):
            if not (isinstance(r, dict) and r.get("success")):
                continue
            m = r.get("model")
            dts = r.get("decode_timestamps") or []
            itl = r.get("itl") or []
            if m not in NAME or len(dts) < 2 or not itl:
                continue
            for i, gap in enumerate(itl):
                t = dts[i]
                j = bisect.bisect_right(ts, t) - 1
                if j < 0:
                    continue
                n = states[j].get(m)
                if n is None:
                    continue
                key = (m, n)
                b = buckets.setdefault(key, {"itl": [], "tpot": [], "reqs": set()})
                b["itl"].append(gap)
            j = bisect.bisect_right(ts, dts[0]) - 1
            if j >= 0 and states[j].get(m):
                buckets.setdefault((m, states[j][m]),
                                   {"itl": [], "tpot": [], "reqs": set()})["tpot"].append(r.get("tpot"))
        for (m, n), b in sorted(buckets.items()):
            if len(b["itl"]) < 50:
                continue
            rows.append({"workload": kind, "rate": rate, "seed": seed,
                         "model": m, "model_name": NAME[m], "models_on_gpu": n,
                         "n_decode_steps": len(b["itl"]), "n_requests": len(b["tpot"]),
                         "itl_p50": pct(b["itl"], 50), "itl_p95": pct(b["itl"], 95),
                         "itl_p99": pct(b["itl"], 99),
                         "itl_mean": sum(b["itl"]) / len(b["itl"]),
                         "tpot_p50": pct([x for x in b["tpot"] if x is not None], 50),
                         "tpot_p95": pct([x for x in b["tpot"] if x is not None], 95),
                         "tpot_p99": pct([x for x in b["tpot"] if x is not None], 99)})
    with (OUT / "qwen7b_by_placement.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    (OUT / "by_placement.json").write_text(json.dumps(rows, indent=1))

    print("Decode behaviour split by models co-resident on the request's own GPU\n")
    print(f"{'cond':>12}{'model':>14}{'n_gpu':>6}{'steps':>8}{'itl_p50':>9}"
          f"{'itl_p95':>9}{'itl_p99':>9}{'tpot_p50':>10}")
    for m in ("model_6", "model_5", "model_4", "model_3"):
        for kind, rate, seed in CONDS:
            rs = [r for r in rows if r["model"] == m and (r["workload"], r["rate"], r["seed"]) == (kind, rate, seed)]
            for r in sorted(rs, key=lambda x: x["models_on_gpu"]):
                t50 = f"{r['tpot_p50']:.4f}" if r["tpot_p50"] else "--"
                print(f"{kind+str(rate)+'s'+str(seed):>12}{NAME[m]:>14}{r['models_on_gpu']:>6}"
                      f"{r['n_decode_steps']:>8}{r['itl_p50']:>9.4f}{r['itl_p95']:>9.4f}"
                      f"{r['itl_p99']:>9.4f}{t50:>10}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
