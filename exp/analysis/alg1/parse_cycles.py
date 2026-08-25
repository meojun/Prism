#!/usr/bin/env python3
"""Reconstruct every [PAPER-ALG1-V4] decision cycle. Offline; reads only."""
import csv, json, sys
from pathlib import Path
ROOT = Path("/workspace/prism-exp")
RAW = ROOT / "exp/results/4het-paired/raw/prism"
OUT = ROOT / "exp/analysis/alg1"
CONDS = [("steady", 8, 1), ("steady", 8, 2), ("steady", 10, 1), ("steady", 10, 2),
         ("steady", 2, 1), ("steady", 4, 1), ("steady", 6, 1),
         ("bursty", 6, 1), ("bursty", 8, 1)]


def shape(placement):
    """placement: model->gpu  ->  sorted tuple of models-per-gpu, e.g. (1,3)."""
    c = {}
    for m, g in placement.items():
        c[g] = c.get(g, 0) + 1
    v = sorted(c.values())
    while len(v) < 2:
        v = [0] + v
    return tuple(v)


def main():
    rows = []
    for kind, rate, seed in CONDS:
        log = RAW / kind / f"rate_{rate}" / f"seed_{seed}" / "server-logs/server.log.global_controller.log"
        if not log.is_file():
            continue
        for line in log.read_text(errors="replace").splitlines():
            i = line.find("[PAPER-ALG1-V4] {")
            if i < 0:
                continue
            try:
                d = json.loads(line[i + len("[PAPER-ALG1-V4] "):])
            except json.JSONDecodeError:
                continue
            cur, plan = d.get("current_placement") or {}, d.get("placement_plan") or {}
            kv = d.get("kvpr") or {}
            r = {"workload": kind, "rate": rate, "seed": seed,
                 "cycle": d.get("cycle"), "timestamp": d.get("timestamp"),
                 "tau": d.get("tau"), "peak_kvpr": d.get("peak_kvpr"),
                 "kvpr_gpu0": kv.get("0"), "kvpr_gpu1": kv.get("1"),
                 "current_shape": "+".join(map(str, shape(cur))) if cur else None,
                 "plan_shape": "+".join(map(str, shape(plan))) if plan else None,
                 "current_placement": json.dumps(cur, sort_keys=True),
                 "placement_plan": json.dumps(plan, sort_keys=True),
                 "convergence_gap": d.get("convergence_gap"),
                 "misplaced": ",".join(d.get("misplaced_models") or []),
                 "cooldown_active": d.get("cooldown_active"),
                 "migration_decision": d.get("migration_decision"),
                 "migration_reason": d.get("migration_reason"),
                 "n_wants_moved": len(d.get("plan_wants_moved") or []),
                 "n_blocked": len(d.get("blocked") or []),
                 "blocked_reasons": ";".join(sorted({b.get("reason", "")
                                                     for b in (d.get("blocked") or [])})),
                 }
            cand = d.get("candidate")
            if cand:
                r.update(cand_model=cand.get("model"), cand_from=cand.get("from"),
                         cand_to=cand.get("to"), peak_kvpr_after=cand.get("peak_kvpr_after"))
            # line-8 rows: how many models had best != current, and how many
            # of those were suppressed by tau
            l8 = d.get("line8") or []
            r["n_line8"] = len(l8)
            r["n_best_differs"] = sum(1 for x in l8 if x["best_gpu"] != x["current_gpu"])
            r["n_tau_suppressed"] = sum(1 for x in l8
                                        if x["best_gpu"] != x["current_gpu"]
                                        and x["chosen_gpu"] == x["current_gpu"])
            r["max_abs_delta"] = max((x["absolute_delta"] for x in l8), default=None)
            r["line8"] = json.dumps(l8)
            rows.append(r)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "alg1_cycles.json").write_text(json.dumps(rows, indent=1))
    fields = [k for k in rows[0] if k != "line8"] + ["line8"]
    with (OUT / "alg1_cycles.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)

    print(f"parsed {len(rows)} cycles\n")
    print(f"{'condition':>16}{'cycles':>8}{'cur 2+2':>9}{'cur 3+1':>9}"
          f"{'plan 2+2':>10}{'plan 3+1':>10}{'MIGRATE':>9}")
    for kind, rate, seed in CONDS:
        rs = [r for r in rows if (r["workload"], r["rate"], r["seed"]) == (kind, rate, seed)]
        if not rs:
            continue
        c22 = sum(1 for r in rs if r["current_shape"] == "2+2")
        c31 = sum(1 for r in rs if r["current_shape"] == "1+3")
        p22 = sum(1 for r in rs if r["plan_shape"] == "2+2")
        p31 = sum(1 for r in rs if r["plan_shape"] == "1+3")
        mg = sum(1 for r in rs if r["migration_decision"] == "MIGRATE")
        print(f"{kind+str(rate)+' s'+str(seed):>16}{len(rs):>8}{c22:>9}{c31:>9}{p22:>10}{p31:>10}{mg:>9}")
    print("\nshape legend: '1+3' means one GPU holds 1 model and the other holds 3")
    return 0


if __name__ == "__main__":
    sys.exit(main())
