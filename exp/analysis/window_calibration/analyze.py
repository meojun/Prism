#!/usr/bin/env python3
"""Window calibration analysis: 30 s vs 60 s, paired on byte-identical traces.

Reuses the established definitions unchanged -- weighted demand, shared_kv,
KVPR, valid placement, large-large, objective margin, replay matching -- by
importing the Phase 7b forensic and the 4-HET quality machinery.
"""
import csv, json, statistics, sys
from collections import Counter, defaultdict
from pathlib import Path
ROOT = Path("/workspace/prism-exp")
OUT = ROOT / "exp/analysis/window_calibration"
sys.path.insert(0, str(ROOT / "exp/analysis/estimator_correction/bad_placement_forensic"))
sys.path.insert(0, str(ROOT / "exp/analysis/kvpr_placement_quality"))
sys.path.insert(0, str(ROOT / "exp/analysis/planner_oscillation"))
from forensic import greedy, kvpr_of, colocated, BIG          # noqa: E402
from quality import enumerate_valid, shape, pctl, MODELS, NAMES  # noqa: E402
from solve_rates import cycles, solve, NGPU                    # noqa: E402

RAW = ROOT / "exp/results/4het-window-calibration/raw"
ARMS = {30: RAW / "prism-w30", 60: RAW / "prism-w60"}
CONDS = [(k, r, s) for k in ("steady", "bursty") for r in (8, 10) for s in (3, 4)]


def ctrl(base, k, r, s):
    return base / k / f"rate_{r}" / f"seed_{s}" / "server-logs/server.log.global_controller.log"


def per_run(win, k, r, s):
    """One pass over a run's controller cycles -> all per-cycle facts."""
    rows = []
    for c in cycles(ctrl(ARMS[win], k, r, s)):
        rates, resid = solve(c)
        cur = c.get("current_placement") or {}
        rec = c.get("placement_plan") or {}
        if rates is None or len(cur) < 4 or len(rec) < 4:
            continue
        rp, _ = greedy(rates, cur)
        cand = enumerate_valid(rates)
        if not cand:
            continue
        gbest = cand[0][0]
        sep = [x for x in cand if x[1][BIG[0]] != x[1][BIG[1]]]
        col = [x for x in cand if x[1][BIG[0]] == x[1][BIG[1]]]
        rows.append({
            "window_s": win, "workload": k, "rate": r, "seed": s,
            "cond": f"{k}_r{r}_s{s}", "cycle": c["cycle"], "timestamp": c["timestamp"],
            "replay_match": rp == rec,
            "rank1_model": max(MODELS, key=lambda m: rates[m]),
            "optimum_is_colocated": bool(col and col[0][0] <= gbest + 1e-15),
            "separated_penalty": (sep[0][0] - gbest) / gbest if sep and gbest else None,
            "plan_colocated": colocated(rec), "residency_colocated": colocated(cur),
            "residency_shape": shape(cur), "plan_id": json.dumps(rec, sort_keys=True),
            "migration": c.get("migration_decision") == "MIGRATE",
            "cand_model": (c.get("candidate") or {}).get("model"),
            **{f"wrate_{m}": rates[m] for m in MODELS},
        })
    return rows


def episodes(vals, ts):
    """Lifetimes of maximal constant runs of `vals`."""
    out, start, prev = [], None, object()
    for v, t in zip(vals, ts):
        if v != prev:
            if start is not None:
                out.append(t - start)
            start, prev = t, v
    if start is not None and ts:
        out.append(ts[-1] - start)
    return out


def main():
    cyc = []
    for win in ARMS:
        for k, r, s in CONDS:
            if not ctrl(ARMS[win], k, r, s).exists():
                print(f"MISSING: w{win} {k}_r{r}_s{s}", file=sys.stderr)
                continue
            cyc += per_run(win, k, r, s)
    if not cyc:
        print("no cycles parsed", file=sys.stderr); return 1

    by = defaultdict(list)
    for c in cyc:
        by[(c["window_s"], c["cond"])].append(c)

    rank_rows, rate_rows, plan_rows, comp_rows = [], [], [], []
    for (win, cond), rs in sorted(by.items()):
        rs.sort(key=lambda x: x["timestamp"])
        ts = [x["timestamp"] for x in rs]
        span = (ts[-1] - ts[0]) or 1.0
        ok = [x for x in rs if x["replay_match"]]
        n_ok = len(ok) or 1

        # --- rank dynamics -------------------------------------------------
        r1 = [x["rank1_model"] for x in rs]
        life = episodes(r1, ts)
        switches = sum(1 for a, b in zip(r1, r1[1:]) if a != b)
        dist = Counter(r1)
        rank_rows.append({
            "window_s": win, "cond": cond, "cycles": len(rs),
            "rank1_switches": switches,
            "rank1_switches_per_min": round(switches / (span / 60), 3),
            "rank1_lifetime_p50_s": round(statistics.median(life), 2) if life else None,
            "rank1_lifetime_p95_s": round(pctl(life, 95), 2) if life else None,
            **{f"rank1_{NAMES[m]}_pct": round(100 * dist[m] / len(rs), 1) for m in MODELS},
            "rank1_small_pct": round(100 * (dist["model_3"] + dist["model_4"]) / len(rs), 1),
        })

        # --- rate stability -------------------------------------------------
        row = {"window_s": win, "cond": cond}
        for m in MODELS:
            v = [x[f"wrate_{m}"] for x in rs]
            d = [abs(b - a) for a, b in zip(v, v[1:])]
            mu = statistics.mean(v) if v else 0
            row[f"{NAMES[m]}_delta_p50"] = round(pctl(d, 50), 4) if d else None
            row[f"{NAMES[m]}_delta_p95"] = round(pctl(d, 95), 4) if d else None
            row[f"{NAMES[m]}_cv"] = round(statistics.pstdev(v) / mu, 4) if mu else None
        rate_rows.append(row)

        # --- planner stability ---------------------------------------------
        plans = [x["plan_id"] for x in rs]
        plife = episodes(plans, ts)
        changes = sum(1 for a, b in zip(plans, plans[1:]) if a != b)
        migs = [x for x in rs if x["migration"]]
        seen, rev, pp = {}, 0, 0
        for x in migs:
            key = x["cand_model"]
            if key in seen:
                rev += 1
                if x["timestamp"] - seen[key] <= 300:
                    pp += 1
            seen[key] = x["timestamp"]
        plan_rows.append({
            "window_s": win, "cond": cond, "span_s": round(span, 1),
            "plan_changes": changes,
            "plan_changes_per_min": round(changes / (span / 60), 3),
            "plan_lifetime_p50_s": round(statistics.median(plife), 2) if plife else None,
            "plan_lifetime_p95_s": round(pctl(plife, 95), 2) if plife else None,
            "migrations": len(migs), "reversals": rev, "ping_pong": pp,
            "replay_confirmed": len(ok), "replay_pct": round(100 * len(ok) / len(rs), 1),
        })

        # --- placement composition -----------------------------------------
        sm = [x for x in ok if x["rank1_model"] in ("model_3", "model_4")]
        lg = [x for x in ok if x["rank1_model"] in ("model_5", "model_6")]
        f31 = sum(1 for x in rs if x["residency_shape"] in ("3+1", "1+3"))
        sp = [x["separated_penalty"] for x in ok if x["separated_penalty"] is not None]
        comp_rows.append({
            "window_s": win, "cond": cond, "replay_confirmed": len(ok),
            "kvpr_optimal_LL_pct": round(100 * sum(1 for x in ok if x["optimum_is_colocated"]) / n_ok, 1),
            "plan_LL_pct": round(100 * sum(1 for x in ok if x["plan_colocated"]) / n_ok, 1),
            "residency_LL_pct": round(100 * sum(1 for x in rs if x["residency_colocated"]) / len(rs), 1),
            "three_one_exposure_pct": round(100 * f31 / len(rs), 1),
            "sep_penalty_p50": round(statistics.median(sp), 5) if sp else None,
            "p_optLL_given_small_rank1": round(100 * sum(1 for x in sm if x["optimum_is_colocated"]) / len(sm), 1) if sm else None,
            "n_small_rank1": len(sm),
            "p_optLL_given_large_rank1": round(100 * sum(1 for x in lg if x["optimum_is_colocated"]) / len(lg), 1) if lg else None,
            "n_large_rank1": len(lg),
        })

    for name, data in (("cycle_facts", cyc), ("rank_dynamics", rank_rows),
                       ("rate_stability", rate_rows), ("planner_stability", plan_rows),
                       ("placement_composition", comp_rows)):
        with (OUT / f"{name}.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(data[0])); w.writeheader(); w.writerows(data)
    print(f"parsed {len(cyc)} cycles across {len(by)} run-arms -> "
          f"{len(rank_rows)} rank rows, {len(plan_rows)} planner rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
