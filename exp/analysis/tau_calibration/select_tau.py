#!/usr/bin/env python3
"""Stage-B tau analysis and selection under the pre-declared rule only (section 16).

A = aggregate time-weighted mean residual KVPR regret
B = migration GB/min   (migrations/min only if byte accounting proves unreliable)
TRADEOFF_SCORE = sqrt(A_norm^2 + B_norm^2) over FINITE candidates; T5 excluded.
Goodput is used for neither selection nor tie-breaking.
"""
import csv, json, math, statistics, sys
from collections import defaultdict
from pathlib import Path
ROOT = Path("/workspace/prism-exp")
OUT = ROOT / "exp/analysis/tau_calibration"
sys.path.insert(0, str(ROOT / "exp/analysis/estimator_correction/bad_placement_forensic"))
sys.path.insert(0, str(ROOT / "exp/analysis/kvpr_placement_quality"))
sys.path.insert(0, str(ROOT / "exp/analysis/planner_oscillation"))
from forensic import kvpr_of, colocated, BIG                 # noqa: E402
from quality import enumerate_valid, pctl, MODELS            # noqa: E402
from solve_rates import cycles, solve                        # noqa: E402

RAW = ROOT / "exp/results/4het-tau-final/raw"
CONDS = [(k, r, s) for k in ("steady", "bursty") for r in (8, 10) for s in (3, 4)]
FROZEN = json.load(open(OUT / "TAU_CANDIDATES_FROZEN.json"))
CANDS = FROZEN["candidates"]
GB = float(2 ** 30)


def run_dir(arm, k, r, s):
    return RAW / f"prism-{arm}" / k / f"rate_{r}" / f"seed_{s}"


def regret_series(d):
    """(timestamp, delta_r) per controller cycle for the ACTUAL residency."""
    log = d / "server-logs/server.log.global_controller.log"
    out = []
    for c in cycles(log):
        rates, _ = solve(c)
        cur = c.get("current_placement") or {}
        if rates is None or len(cur) < 4:
            continue
        cur_pk, _ = kvpr_of(cur, rates)
        cand = enumerate_valid(rates)
        if not cand or cur_pk == float("inf"):
            continue
        out.append((c["timestamp"], max(0.0, cur_pk - cand[0][0]),
                    colocated(cur), c.get("migration_decision") == "MIGRATE"))
    return out


def main():
    per_run, missing = [], []
    for cand in CANDS:
        for k, r, s in CONDS:
            d = run_dir(cand["id"], k, r, s)
            vf = d / "VERIFICATION.json"
            if not vf.exists():
                missing.append(f"{cand['id']} {k}_r{r}_s{s}")
                continue
            n = json.loads(vf.read_text())["numbers"]
            ser = regret_series(d)
            if len(ser) < 2:
                missing.append(f"{cand['id']} {k}_r{r}_s{s} (no cycles)")
                continue
            span = ser[-1][0] - ser[0][0]
            # time-weighted mean: each cycle's regret weighted by its duration
            tw = sum(a[1] * (b[0] - a[0]) for a, b in zip(ser, ser[1:]))
            twmean = tw / span if span else 0.0
            vals = [x[1] for x in ser]
            mb = (n.get("migrated_weight_bytes", 0) or 0) + (n.get("migrated_kv_bytes", 0) or 0)
            per_run.append({
                "tau_id": cand["id"], "tau": cand["tau"], "workload": k, "rate": r, "seed": s,
                "cond": f"{k}_r{r}_s{s}", "cycles": len(ser), "span_s": round(span, 1),
                "regret_time_weighted_mean": twmean,
                "regret_p50": pctl(vals, 50), "regret_p90": pctl(vals, 90),
                "regret_p95": pctl(vals, 95), "regret_p99": pctl(vals, 99),
                "regret_max": max(vals), "frac_cycles_regret_gt0": sum(1 for v in vals if v > 0) / len(vals),
                "migrations": n["migrations_executed"],
                "migrations_per_min": n["migrations_executed"] / (span / 60) if span else 0.0,
                "migration_bytes": mb,
                "migration_gb_per_min": (mb / GB) / (span / 60) if span else 0.0,
                "large_large_residency_pct": 100 * sum(1 for x in ser if x[2]) / len(ser),
                "goodput_req_s": n["joint_slo_goodput_req_s"],
                "attainment": n["joint_slo_attainment"],
                "throughput_req_s": n["throughput_req_s"],
                "tpot_p99_ms": n["tpot_p99_s"] * 1000, "ttft_p99_s": n["ttft_p99_s"],
                "completed": n["completed"], "aborted": n["aborted"]})
    if missing:
        print(f"INCOMPLETE: {len(missing)} runs missing/unusable", file=sys.stderr)
        for m in missing[:10]:
            print("   ", m, file=sys.stderr)
        return 2
    with (OUT / "kvpr_regret.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(per_run[0])); w.writeheader(); w.writerows(per_run)

    # ---- aggregate per candidate ------------------------------------------
    agg = []
    byte_ok = all(r["migration_bytes"] > 0 for r in per_run if r["migrations"] > 0)
    for cand in CANDS:
        rs = [r for r in per_run if r["tau_id"] == cand["id"]]
        A = statistics.mean(r["regret_time_weighted_mean"] for r in rs)
        Bg = statistics.mean(r["migration_gb_per_min"] for r in rs)
        Bm = statistics.mean(r["migrations_per_min"] for r in rs)
        agg.append({"tau_id": cand["id"], "tau": cand["tau"], "role": cand["role"],
                    "runs": len(rs), "A_regret_tw_mean": A,
                    "B_migration_gb_per_min": Bg, "B_migrations_per_min": Bm,
                    "migrations_total": sum(r["migrations"] for r in rs),
                    "goodput_mean": statistics.mean(r["goodput_req_s"] for r in rs),
                    "attainment_mean": statistics.mean(r["attainment"] for r in rs),
                    "large_large_pct_mean": statistics.mean(r["large_large_residency_pct"] for r in rs)})
    Bkey = "B_migration_gb_per_min" if byte_ok else "B_migrations_per_min"
    finite = [a for a in agg if a["tau_id"] != "T5"]
    Amin, Amax = min(a["A_regret_tw_mean"] for a in finite), max(a["A_regret_tw_mean"] for a in finite)
    Bmin, Bmax = min(a[Bkey] for a in finite), max(a[Bkey] for a in finite)
    for a in agg:
        an = (a["A_regret_tw_mean"] - Amin) / (Amax - Amin) if Amax > Amin else 0.0
        bn = (a[Bkey] - Bmin) / (Bmax - Bmin) if Bmax > Bmin else 0.0
        a["A_norm"], a["B_norm"] = an, bn
        a["TRADEOFF_SCORE"] = math.sqrt(an * an + bn * bn) if a["tau_id"] != "T5" else None
        a["selectable"] = a["tau_id"] != "T5"
    with (OUT / "tau_tradeoff.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(agg[0])); w.writeheader(); w.writerows(agg)

    ranked = sorted(finite, key=lambda a: a["TRADEOFF_SCORE"])
    best = ranked[0]
    tied = [a for a in ranked
            if best["TRADEOFF_SCORE"] == 0 or
            abs(a["TRADEOFF_SCORE"] - best["TRADEOFF_SCORE"]) / max(best["TRADEOFF_SCORE"], 1e-12) <= 0.05]
    tie_note = None
    if len(tied) > 1:
        tie_note = (f"{len(tied)} candidates within 5%: "
                    + ", ".join(f"{t['tau_id']}({t['TRADEOFF_SCORE']:.4f})" for t in tied))
        tied.sort(key=lambda a: (a["A_regret_tw_mean"], a[Bkey], a["tau"]))
        best = tied[0]

    print(f"{'id':>4}{'tau':>24}{'A regret':>13}{'B GB/min':>11}{'mig/min':>9}"
          f"{'A_norm':>8}{'B_norm':>8}{'SCORE':>9}   role")
    for a in agg:
        sc = f"{a['TRADEOFF_SCORE']:.4f}" if a["TRADEOFF_SCORE"] is not None else "excluded"
        print(f"{a['tau_id']:>4}{a['tau']:>24.12g}{a['A_regret_tw_mean']:>13.6f}"
              f"{a['B_migration_gb_per_min']:>11.3f}{a['B_migrations_per_min']:>9.3f}"
              f"{a['A_norm']:>8.3f}{a['B_norm']:>8.3f}{sc:>9}   {a['role'][:38]}")
    print(f"\ncost metric B = {Bkey}" + ("" if byte_ok else "  (BYTE ACCOUNTING UNRELIABLE -> fallback)"))
    if tie_note:
        print("TIE: " + tie_note + " -> broken by lower regret, then lower cost, then lower tau")
    print(f"\nFINAL_TAU = {best['tau']!r}   ({best['tau_id']})")
    json.dump({"FINAL_TAU": best["tau"], "tau_id": best["tau_id"],
               "TAU_STATUS": "FINALIZED", "cost_metric": Bkey,
               "tie_note": tie_note, "ranking": [a["tau_id"] for a in ranked],
               "scores": {a["tau_id"]: a["TRADEOFF_SCORE"] for a in agg}},
              open(OUT / "FINAL_TAU.json", "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
