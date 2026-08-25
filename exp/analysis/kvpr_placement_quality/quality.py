#!/usr/bin/env python3
"""4-HET-wide offline analysis of KVPR-optimal placement quality.

Reuses the Phase 7b forensic definitions verbatim (weighted demand, shared_kv,
KVPR, valid placement, large-large, objective margin, replay matching) by
importing forensic.py. Modification vs forensic.py: the condition set is widened
to all 20 OLD Prism conditions plus the 4 NEW diagnostic ones, composition
classes and demand-rank bookkeeping are added, and latency is joined by
residency interval. No runtime code is touched.
"""
import csv, itertools, json, statistics, sys
from collections import defaultdict, Counter
from pathlib import Path
ROOT = Path("/workspace/prism-exp")
OUT = ROOT / "exp/analysis/kvpr_placement_quality"
sys.path.insert(0, str(ROOT / "exp/analysis/estimator_correction/bad_placement_forensic"))
sys.path.insert(0, str(ROOT / "exp/analysis/planner_oscillation"))
from forensic import greedy, kvpr_of, colocated, BIG, TAU      # noqa: E402
from solve_rates import cycles, solve, SIZE, GPU_MEM, NGPU     # noqa: E402

MODELS = ("model_3", "model_4", "model_5", "model_6")
NAMES = {"model_3": "Llama-3.2-3B", "model_4": "Qwen2.5-3B",
         "model_5": "Llama-3.1-8B", "model_6": "Qwen2.5-7B"}
PRISM_OLD = ROOT / "exp/results/4het-paired/raw/prism"
PRISM_NEW = ROOT / "exp/results/4het-estimator-correction/raw/prism-estimator"
PROTO = ROOT / "exp/results/4het-paired/raw/prototype"
ALL_CONDS = [(k, r, s) for k in ("bursty", "steady") for r in (2, 4, 6, 8, 10) for s in (1, 2)]
NEW_CONDS = [("steady", 8, 1), ("steady", 8, 2), ("steady", 10, 1), ("steady", 10, 2)]


def ctrl(base, k, r, s):
    return base / k / f"rate_{r}" / f"seed_{s}" / "server-logs/server.log.global_controller.log"


def shape(place):
    c = Counter(place.values())
    return "+".join(str(c.get(g, 0)) for g in range(NGPU))


def pctl(v, q):
    v = sorted(x for x in v if x is not None)
    if not v:
        return None
    k = (len(v) - 1) * q / 100
    lo = int(k); hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def enumerate_valid(rates):
    """All assignments of 4 models to 2 GPUs with both GPUs non-empty."""
    out = []
    for bits in itertools.product(range(NGPU), repeat=len(MODELS)):
        a = dict(zip(MODELS, bits))
        if len(set(a.values())) < NGPU:
            continue
        pk, per = kvpr_of(a, rates)
        if per is None:
            continue
        out.append((pk, a, per))
    out.sort(key=lambda x: x[0])
    return out


def cycle_pass():
    rows, mismatch, insufficient = [], 0, 0
    arms = [("OLD", PRISM_OLD, ALL_CONDS), ("NEW", PRISM_NEW, NEW_CONDS)]
    for est, base, conds in arms:
        for k, r, s in conds:
            cond = f"{k}_r{r}_s{s}"
            for c in cycles(ctrl(base, k, r, s)):
                rates, resid = solve(c)
                cur = c.get("current_placement") or {}
                rec = c.get("placement_plan") or {}
                if rates is None or not cur or not rec or len(rec) < 4:
                    insufficient += 1
                    continue
                rp, _ = greedy(rates, cur)
                match = rp == rec
                if not match:
                    mismatch += 1
                cand = enumerate_valid(rates)
                if not cand:
                    insufficient += 1
                    continue
                gbest = cand[0][0]
                sep = [x for x in cand if x[1][BIG[0]] != x[1][BIG[1]]]
                col = [x for x in cand if x[1][BIG[0]] == x[1][BIG[1]]]
                bsep = sep[0][0] if sep else None
                bcol = col[0][0] if col else None
                rank1 = max(MODELS, key=lambda m: rates[m])
                rows.append({
                    "estimator": est, "workload": k, "rate": r, "seed": s, "cond": cond,
                    "cycle": c["cycle"], "timestamp": c["timestamp"],
                    "replay_match": match, "n_valid": len(cand),
                    "global_best_peak_kvpr": gbest,
                    "best_separated_peak_kvpr": bsep, "best_colocated_peak_kvpr": bcol,
                    "optimum_is_colocated": bool(bcol is not None and bcol <= gbest + 1e-15),
                    "separated_penalty": (bsep - gbest) / gbest if gbest else None,
                    "colocated_penalty": (bcol - gbest) / gbest if gbest else None,
                    "plan_colocated": colocated(rec), "residency_colocated": colocated(cur),
                    "plan_shape": shape(rec), "residency_shape": shape(cur),
                    "rank1_model": rank1,
                    **{f"wrate_{m}": rates[m] for m in MODELS},
                })
    return rows, mismatch, insufficient


def residency_intervals(rows, est, cond):
    rs = sorted([r for r in rows if r["estimator"] == est and r["cond"] == cond],
                key=lambda r: r["timestamp"])
    out, cur = [], None
    for a, b in zip(rs, rs[1:]):
        if a["residency_colocated"]:
            t0, t1 = a["timestamp"], b["timestamp"]
            if cur and abs(t0 - cur[1]) < 1e-6:
                cur = (cur[0], t1)
            else:
                if cur:
                    out.append(cur)
                cur = (t0, t1)
    if cur:
        out.append(cur)
    return out


def latency_pass(rows):
    """Within-run split of requests by whether they arrived during large-large."""
    res = []
    for est, base, conds in (("OLD", PRISM_OLD, ALL_CONDS), ("NEW", PRISM_NEW, NEW_CONDS)):
        for k, r, s in conds:
            cond = f"{k}_r{r}_s{s}"
            run = base / k / f"rate_{r}" / f"seed_{s}"
            dumps = list(run.glob("requests/*_output_requests.json"))
            if not dumps:
                continue
            iv = residency_intervals(rows, est, cond)
            reqs = [x for x in json.load(dumps[0].open()) if isinstance(x, dict) and x.get("success")]
            buckets = {True: defaultdict(list), False: defaultdict(list)}
            slo = {True: [0, 0], False: [0, 0]}
            for x in reqs:
                at = x.get("arrival_time")
                if at is None:
                    continue
                inside = any(a <= at <= b for a, b in iv)
                m = x.get("model")
                if m in MODELS:
                    buckets[inside][f"{m}_tpot"].append(x.get("tpot"))
                    for v in (x.get("itl") or []):
                        buckets[inside][f"{m}_itl"].append(v)
                buckets[inside]["tpot"].append(x.get("tpot"))
                buckets[inside]["ttft"].append(x.get("ttft"))
                st, sp = x.get("slo_ttft"), x.get("slo_tpot")
                t, p = x.get("tpot"), x.get("ttft")
                ok = (st is not None and x.get("ttft") is not None and x["ttft"] <= st
                      and sp is not None and t is not None and t <= sp)
                slo[inside][0] += 1; slo[inside][1] += int(ok)
            for inside in (True, False):
                b = buckets[inside]; n, ps = slo[inside]
                if n == 0:
                    continue
                res.append({
                    "estimator": est, "workload": k, "rate": r, "seed": s, "cond": cond,
                    "residency": "LARGE_COLOCATED" if inside else "LARGE_SEPARATED",
                    "coloc_seconds": round(sum(y - x for x, y in iv), 1),
                    "n_requests": n, "slo_pass": ps, "attainment": round(ps / n, 4),
                    "qwen7b_tpot_p50": pctl(b["model_6_tpot"], 50),
                    "qwen7b_tpot_p95": pctl(b["model_6_tpot"], 95),
                    "qwen7b_tpot_p99": pctl(b["model_6_tpot"], 99),
                    "qwen7b_itl_p99": pctl(b["model_6_itl"], 99),
                    "llama8b_tpot_p50": pctl(b["model_5_tpot"], 50),
                    "llama8b_tpot_p95": pctl(b["model_5_tpot"], 95),
                    "llama8b_itl_p99": pctl(b["model_5_itl"], 99),
                    "agg_tpot_p50": pctl(b["tpot"], 50),
                    "ttft_p95": pctl(b["ttft"], 95), "ttft_p99": pctl(b["ttft"], 99),
                })
    return res


def prototype_pass():
    """Prototype's static composition per condition, from its own controller log."""
    res = []
    for k, r, s in ALL_CONDS:
        run = PROTO / k / f"rate_{r}" / f"seed_{s}"
        log = run / "server-logs/server.log.global_controller.log"
        shapes = Counter(); colo = Counter(); n = 0
        if log.exists():
            for c in cycles(log):
                cur = c.get("current_placement") or {}
                if len(cur) < 4:
                    continue
                n += 1; shapes[shape(cur)] += 1; colo[colocated(cur)] += 1
        vf = run / "VERIFICATION.json"
        num = json.loads(vf.read_text())["numbers"] if vf.exists() else {}
        res.append({"workload": k, "rate": r, "seed": s, "cycles": n,
                    "dominant_shape": shapes.most_common(1)[0][0] if shapes else None,
                    "large_colocated_pct": round(100 * colo[True] / n, 1) if n else None,
                    "migrations": num.get("migrations_executed"),
                    "goodput": num.get("joint_slo_goodput_req_s"),
                    "attainment": num.get("joint_slo_attainment")})
    return res


def main():
    rows, mismatch, insufficient = cycle_pass()
    print(f"cycles usable={len(rows)}  replay_mismatch={mismatch}  insufficient={insufficient}")
    ok = [r for r in rows if r["replay_match"]]
    print(f"ALG1_REPLAY = {len(ok)}/{len(ok)+mismatch} "
          f"({100*len(ok)/(len(ok)+mismatch):.1f}%)")
    lat = latency_pass(rows)
    proto = prototype_pass()
    for name, data in (("cycle_objectives", rows), ("composition_latency", lat),
                       ("prototype_pairing", proto)):
        with (OUT / f"{name}.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(data[0])); w.writeheader(); w.writerows(data)
    print("wrote cycle_objectives.csv, composition_latency.csv, prototype_pairing.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
