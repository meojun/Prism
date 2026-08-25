#!/usr/bin/env python3
"""Phase 5 -- why cooldown=0 shortens 3+1 episodes but multiplies migrations.

Offline only. Reads the [PAPER-ALG1-V4] cycle traces of the four cooldown=30
baseline runs and the four cooldown=0 diagnostic runs.

Migration classification uses PLAN IDENTITY, not direction:
  in-flight target := the placement_plan in force when the last migration was
  emitted. A migration emitted while the current plan still equals that target
  is a SWAP_COMPLETION_LEG; a migration emitted under a different plan is a
  NEW_PLAN_MIGRATION.
"""
import csv, json, statistics, sys
from pathlib import Path
ROOT = Path("/workspace/prism-exp")
OUT = ROOT / "exp/analysis/migration_thrashing"
ARMS = {"cooldown30": ROOT / "exp/results/4het-paired/raw/prism",
        "cooldown0": ROOT / "exp/results/4het-cooldown-diagnostic/raw/prism-cooldown0"}
CONDS = [("steady", 8, 1), ("steady", 8, 2), ("steady", 10, 1), ("steady", 10, 2)]


def key(p):
    return tuple(sorted(p.items())) if p else None


def shape(p):
    c = {}
    for m, g in (p or {}).items():
        c[g] = c.get(g, 0) + 1
    v = sorted(c.values())
    while len(v) < 2:
        v = [0] + v
    return "+".join(map(str, v))


def cycles(run):
    log = run / "server-logs/server.log.global_controller.log"
    out = []
    if not log.is_file():
        return out
    for line in log.read_text(errors="replace").splitlines():
        i = line.find("[PAPER-ALG1-V4] {")
        if i < 0:
            continue
        try:
            d = json.loads(line[i + len("[PAPER-ALG1-V4] "):])
        except json.JSONDecodeError:
            continue
        if not (d.get("current_placement") or {}):
            continue
        out.append(d)
    out.sort(key=lambda x: x["timestamp"])
    return out


def pct(v, q):
    v = sorted(v)
    if not v:
        return None
    k = (len(v) - 1) * q / 100
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def main():
    plan_rows, mig_rows, rev_rows, end_rows, stab_rows, cf_rows = [], [], [], [], [], []
    for arm, base in ARMS.items():
        for kind, rate, seed in CONDS:
            tag = f"{kind}{rate}s{seed}"
            cs = cycles(base / kind / f"rate_{rate}" / f"seed_{seed}")
            if not cs:
                continue
            span = cs[-1]["timestamp"] - cs[0]["timestamp"]
            inflight = None          # plan identity of the in-flight target
            last_plan = None
            plan_start = cs[0]["timestamp"]
            lifetimes, changes, sim = [], 0, {"identical": 0, "one": 0, "two": 0, "more": 0}
            moves = []               # (t, model, src, dst) emitted
            cd_wait_same_plan = 0    # cycles: cooldown blocked, plan == in-flight
            cd_wait_new_plan = 0
            for c in cs:
                pk = key(c.get("placement_plan"))
                if last_plan is not None and pk != last_plan:
                    changes += 1
                    lifetimes.append(c["timestamp"] - plan_start)
                    plan_start = c["timestamp"]
                    a, b = dict(last_plan), dict(pk)
                    diff = sum(1 for m in b if a.get(m) != b[m])
                    sim["one" if diff == 1 else "two" if diff == 2 else "more"] += 1
                elif last_plan is not None:
                    sim["identical"] += 1
                last_plan = pk
                plan_rows.append({"arm": arm, "cond": tag, "cycle": c.get("cycle"),
                                  "timestamp": c["timestamp"],
                                  "rate_window_s": 30.0,
                                  "current_shape": shape(c.get("current_placement")),
                                  "plan_shape": shape(c.get("placement_plan")),
                                  "plan_id": str(pk), "plan_changed": pk != last_plan,
                                  "convergence_gap": c.get("convergence_gap"),
                                  "cooldown_active": c.get("cooldown_active"),
                                  "decision": c.get("migration_decision"),
                                  "reason": c.get("migration_reason"),
                                  "kvpr0": (c.get("kvpr") or {}).get("0"),
                                  "kvpr1": (c.get("kvpr") or {}).get("1"),
                                  "peak_kvpr": c.get("peak_kvpr")})
                if c.get("cooldown_active") and (c.get("plan_wants_moved") or []):
                    if inflight is not None and pk == inflight:
                        cd_wait_same_plan += 1
                    else:
                        cd_wait_new_plan += 1
                cand = c.get("candidate")
                if c.get("migration_decision") == "MIGRATE" and cand:
                    cls = ("SWAP_COMPLETION_LEG" if inflight is not None and pk == inflight
                           else "NEW_PLAN_MIGRATION")
                    mig_rows.append({"arm": arm, "cond": tag, "timestamp": c["timestamp"],
                                     "model": cand["model"], "src": cand["from"],
                                     "dst": cand["to"], "classification": cls,
                                     "plan_id": str(pk),
                                     "shape_before": shape(c.get("current_placement")),
                                     "convergence_gap": c.get("convergence_gap")})
                    moves.append((c["timestamp"], cand["model"], cand["from"], cand["to"], cls))
                    inflight = pk
            # --- ping-pong: same model reversed within the run ------------
            npp = 0
            for i, (t1, m, s1, d1, _) in enumerate(moves):
                for t2, m2, s2, d2, _ in moves[i + 1:]:
                    if m2 == m and s2 == d1 and d2 == s1:
                        npp += 1
                        rev_rows.append({"arm": arm, "cond": tag, "model": m,
                                         "first_t": t1, "second_t": t2,
                                         "interval_s": round(t2 - t1, 2),
                                         "path": f"{s1}->{d1} then {s2}->{d2}"})
                        break
            # --- 3+1 episodes and why they ended --------------------------
            cur, start, start_plan, mem_seen, plan_changed_in_ep = None, None, None, 0, False
            for i, c in enumerate(cs):
                sh = shape(c.get("current_placement"))
                if sh != cur:
                    if cur == "1+3" and start is not None:
                        reached = key(c.get("current_placement"))
                        cause = ("swap_completed" if reached == start_plan
                                 else "superseded_by_new_plan" if plan_changed_in_ep
                                 else "other")
                        end_rows.append({"arm": arm, "cond": tag,
                                         "start": start, "end": c["timestamp"],
                                         "duration_s": round(c["timestamp"] - start, 2),
                                         "end_cause": cause,
                                         "memory_blocked_cycles": mem_seen})
                    cur, start = sh, c["timestamp"]
                    start_plan = key(c.get("placement_plan"))
                    mem_seen, plan_changed_in_ep = 0, False
                if cur == "1+3":
                    if key(c.get("placement_plan")) != start_plan:
                        plan_changed_in_ep = True
                    mem_seen += sum(1 for b in (c.get("blocked") or [])
                                    if b.get("reason") == "target memory infeasible")
            nm = sum(1 for r in mig_rows if r["arm"] == arm and r["cond"] == tag)
            stab_rows.append({"arm": arm, "cond": tag, "span_s": round(span, 1),
                              "cycles": len(cs),
                              "unique_plans": len({r["plan_id"] for r in plan_rows
                                                   if r["arm"] == arm and r["cond"] == tag}),
                              "plan_changes": changes,
                              "plan_changes_per_min": round(changes / (span / 60), 2) if span else None,
                              "plan_lifetime_p50_s": round(statistics.median(lifetimes), 2) if lifetimes else None,
                              "plan_lifetime_p95_s": round(pct(lifetimes, 95), 2) if lifetimes else None,
                              "migrations": nm,
                              "migrations_per_min": round(nm / (span / 60), 2) if span else None,
                              "ping_pong": npp,
                              "ping_pong_per_min": round(npp / (span / 60), 2) if span else None,
                              "consec_identical": sim["identical"], "consec_1model": sim["one"],
                              "consec_2model": sim["two"], "consec_more": sim["more"],
                              "cooldown_wait_same_plan_cycles": cd_wait_same_plan,
                              "cooldown_wait_new_plan_cycles": cd_wait_new_plan})
            cf_rows.append({"arm": arm, "cond": tag,
                            "cooldown_waits_bypassed_by_plan_aware": cd_wait_same_plan,
                            "cooldown_waits_still_held": cd_wait_new_plan,
                            "new_plan_migrations": sum(1 for r in mig_rows
                                                       if r["arm"] == arm and r["cond"] == tag
                                                       and r["classification"] == "NEW_PLAN_MIGRATION"),
                            "swap_completion_migrations": sum(1 for r in mig_rows
                                                              if r["arm"] == arm and r["cond"] == tag
                                                              and r["classification"] == "SWAP_COMPLETION_LEG"),
                            "ping_pong": npp})
    OUT.mkdir(parents=True, exist_ok=True)
    for name, data in (("plan_cycles", plan_rows), ("migration_classification", mig_rows),
                       ("plan_reversals", rev_rows), ("three_one_end_causes", end_rows),
                       ("plan_stability", stab_rows), ("plan_aware_counterfactual", cf_rows)):
        if not data:
            continue
        with (OUT / f"{name}.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(data[0])); w.writeheader(); w.writerows(data)
        (OUT / f"{name}.json").write_text(json.dumps(data, indent=1))

    print("§2 Migration classification (plan identity, not direction)\n")
    print(f"{'arm':>12}{'total':>7}{'swap_leg':>10}{'new_plan':>10}{'ping_pong':>11}")
    for arm in ARMS:
        rs = [r for r in mig_rows if r["arm"] == arm]
        print(f"{arm:>12}{len(rs):>7}"
              f"{sum(1 for r in rs if r['classification']=='SWAP_COMPLETION_LEG'):>10}"
              f"{sum(1 for r in rs if r['classification']=='NEW_PLAN_MIGRATION'):>10}"
              f"{sum(r['ping_pong'] for r in stab_rows if r['arm']==arm):>11}")
    print("\n§5 Plan stability")
    print(f"{'cond':>12}{'arm':>12}{'plans':>7}{'chg':>5}{'chg/min':>9}{'life_p50':>10}"
          f"{'migr':>6}{'mig/min':>9}{'pingpong':>9}{'cdwait_same':>12}{'cdwait_new':>11}")
    for kind, rate, seed in CONDS:
        for arm in ARMS:
            r = next((x for x in stab_rows if x["arm"] == arm and x["cond"] == f"{kind}{rate}s{seed}"), None)
            if not r:
                continue
            print(f"{r['cond']:>12}{arm:>12}{r['unique_plans']:>7}{r['plan_changes']:>5}"
                  f"{r['plan_changes_per_min']:>9.2f}{r['plan_lifetime_p50_s'] or 0:>10.1f}"
                  f"{r['migrations']:>6}{r['migrations_per_min']:>9.2f}{r['ping_pong']:>9}"
                  f"{r['cooldown_wait_same_plan_cycles']:>12}{r['cooldown_wait_new_plan_cycles']:>11}")
        print()
    print("§6 Why 3+1 episodes ended")
    print(f"{'arm':>12}{'episodes':>10}{'swap_done':>11}{'superseded':>12}{'other':>7}{'mem_seen':>10}")
    for arm in ARMS:
        rs = [r for r in end_rows if r["arm"] == arm]
        print(f"{arm:>12}{len(rs):>10}"
              f"{sum(1 for r in rs if r['end_cause']=='swap_completed'):>11}"
              f"{sum(1 for r in rs if r['end_cause']=='superseded_by_new_plan'):>12}"
              f"{sum(1 for r in rs if r['end_cause']=='other'):>7}"
              f"{sum(1 for r in rs if r['memory_blocked_cycles']>0):>10}")
    print("\n§3 Ping-pong reversal intervals")
    for arm in ARMS:
        iv = [r["interval_s"] for r in rev_rows if r["arm"] == arm]
        if iv:
            print(f"  {arm}: n={len(iv)} p50={statistics.median(iv):.1f}s "
                  f"min={min(iv):.1f}s max={max(iv):.1f}s")
        else:
            print(f"  {arm}: none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
