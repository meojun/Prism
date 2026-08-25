#!/usr/bin/env python3
"""Phase 7 -- OLD (cached achieved decode tput) vs NEW (30 s sliding window).

Primary endpoints are planner-mechanism metrics, computed by reusing the Phase 5
code (exp/analysis/migration_thrashing/thrash.py) unchanged, with only its two
module globals repointed. Secondary endpoints come from each run's
VERIFICATION.json and per-request dump. Nothing under exp/results is written.
"""
import csv, json, statistics, sys
from pathlib import Path
ROOT = Path("/workspace/prism-exp")
OUT = ROOT / "exp/analysis/estimator_correction"
sys.path.insert(0, str(ROOT / "exp/analysis/migration_thrashing"))
sys.path.insert(0, str(ROOT / "exp/analysis"))
import thrash                                              # noqa: E402

OLD = ROOT / "exp/results/4het-paired/raw/prism"
NEW = ROOT / "exp/results/4het-estimator-correction/raw/prism-estimator"
CONDS = [("steady", 8, 1), ("steady", 8, 2), ("steady", 10, 1), ("steady", 10, 2)]
QWEN7B = "model_6"


def pct(v, q):
    if not v:
        return None
    s = sorted(v); i = min(len(s) - 1, int(round((q / 100) * (len(s) - 1))))
    return s[i]


def primary():
    thrash.ARMS = {"old": OLD, "new": NEW}
    thrash.CONDS = CONDS
    thrash.OUT = OUT / "mechanism"
    thrash.OUT.mkdir(parents=True, exist_ok=True)
    thrash.main()
    return json.loads((thrash.OUT / "plan_stability.json").read_text())


def secondary():
    rows = []
    for arm, base in (("old", OLD), ("new", NEW)):
        for kind, rate, seed in CONDS:
            run = base / kind / f"rate_{rate}" / f"seed_{seed}"
            vf = run / "VERIFICATION.json"
            if not vf.exists():
                continue
            n = json.loads(vf.read_text())["numbers"]
            dumps = list(run.glob("requests/*_output_requests.json"))
            tpot, itl = [], []
            if dumps:
                for r in json.loads(dumps[0].read_text()):
                    if r.get("model") != QWEN7B or not r.get("success", True):
                        continue
                    lat = r.get("itl") or []
                    itl.extend(lat)
                    if lat:
                        tpot.append(sum(lat) / len(lat))
            rows.append({
                "arm": arm, "cond": f"{kind}_r{rate}_s{seed}",
                "goodput_req_s": round(n["joint_slo_goodput_req_s"], 4),
                "attainment": round(n["joint_slo_attainment"], 4),
                "throughput_req_s": round(n["throughput_req_s"], 4),
                "tpot_mean_ms": round(n["tpot_mean_s"] * 1000, 2),
                "tpot_p99_ms": round(n["tpot_p99_s"] * 1000, 2),
                "ttft_mean_s": round(n["ttft_mean_s"], 4),
                "ttft_p99_s": round(n["ttft_p99_s"], 3),
                "migrations": n["migrations_executed"],
                "completed": n["completed"], "aborted": n["aborted"],
                "qwen7b_tpot_p50_ms": round(pct(tpot, 50) * 1000, 2) if tpot else None,
                "qwen7b_tpot_p95_ms": round(pct(tpot, 95) * 1000, 2) if tpot else None,
                "qwen7b_itl_p50_ms": round(pct(itl, 50) * 1000, 2) if itl else None,
                "qwen7b_itl_p99_ms": round(pct(itl, 99) * 1000, 2) if itl else None,
            })
    with (OUT / "secondary.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    return rows


def main():
    stab = primary()
    sec = secondary()

    def agg(rows, arm, field):
        v = [r[field] for r in rows if r["arm"] == arm and r.get(field) is not None]
        return sum(v) if v else None

    def med(rows, arm, field):
        v = [r[field] for r in rows if r["arm"] == arm and r.get(field) is not None]
        return round(statistics.median(v), 3) if v else None

    print("\n" + "=" * 72)
    print("PRIMARY -- planner mechanism (4 conditions pooled)")
    print("=" * 72)
    print(f"{'metric':<32}{'OLD':>14}{'NEW':>14}{'change':>12}")
    prim = [("plan changes/min", "plan_changes_per_min", med),
            ("median plan lifetime (s)", "plan_lifetime_p50_s", med),
            ("plan reversals", "reversals", agg),
            ("ping-pong migrations", "ping_pong", agg),
            ("migrations total", "migrations", agg)]
    for label, field, fn in prim:
        if field not in stab[0]:
            continue
        o, n = fn(stab, "old", field), fn(stab, "new", field)
        ch = f"{(n-o)/o*100:+.1f}%" if o else "n/a"
        print(f"{label:<32}{o:>14}{n:>14}{ch:>12}")

    print("\n" + "=" * 72)
    print("SECONDARY -- downstream (not the success criterion)")
    print("=" * 72)
    print(f"{'metric':<32}{'OLD':>14}{'NEW':>14}{'change':>12}")
    for label, field, fn in [
            ("Qwen2.5-7B TPOT p50 (ms)", "qwen7b_tpot_p50_ms", med),
            ("Qwen2.5-7B TPOT p95 (ms)", "qwen7b_tpot_p95_ms", med),
            ("Qwen2.5-7B ITL p99 (ms)", "qwen7b_itl_p99_ms", med),
            ("aggregate TPOT mean (ms)", "tpot_mean_ms", med),
            ("TTFT mean (s)", "ttft_mean_s", med),
            ("TTFT p99 (s)", "ttft_p99_s", med),
            ("joint-SLO goodput (req/s)", "goodput_req_s", med),
            ("joint-SLO attainment", "attainment", med),
            ("throughput (req/s)", "throughput_req_s", med)]:
        o, n = fn(sec, "old", field), fn(sec, "new", field)
        ch = f"{(n-o)/o*100:+.1f}%" if o else "n/a"
        print(f"{label:<32}{o:>14}{n:>14}{ch:>12}")

    json.dump({"primary": stab, "secondary": sec},
              open(OUT / "SUMMARY.json", "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
