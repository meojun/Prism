#!/usr/bin/env python3
"""Stage B mechanism analysis: cooldown 30 vs 0.

Primary endpoints are placement mechanics, not goodput:
  1. 3+1 episode duration
  2. fraction of run in 3+1
  3. completion latency of two-move (swap) plans
A swap plan is an ALG1 cycle whose placement_plan is balanced 2+2 and whose
plan_wants_moved is 2. Its completion is the first later moment at which the
runtime placement matches that plan's shape again.
"""
import csv, json, statistics, sys
from pathlib import Path
ROOT = Path("/workspace/prism-exp")
ARMS = {"cooldown30": ROOT / "exp/results/4het-paired/raw/prism",
        "cooldown0": ROOT / "exp/results/4het-cooldown-diagnostic/raw/prism-cooldown0"}
OUT = ROOT / "exp/analysis/swap_pathology_bound"
CONDS = [("steady", 8, 1), ("steady", 8, 2), ("steady", 10, 1), ("steady", 10, 2)]


def shape(p):
    c = {}
    for m, g in p.items():
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
        cur = d.get("current_placement") or {}
        if not cur:
            continue
        d["_shape"] = shape(cur)
        d["_plan_shape"] = shape(d.get("placement_plan") or {}) if d.get("placement_plan") else None
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
    eps_rows, swap_rows, sum_rows, mem_rows = [], [], [], []
    for arm, base in ARMS.items():
        for kind, rate, seed in CONDS:
            run = base / kind / f"rate_{rate}" / f"seed_{seed}"
            cs = cycles(run)
            if not cs:
                continue
            span = cs[-1]["timestamp"] - cs[0]["timestamp"]
            # --- 3+1 episodes -----------------------------------------
            eps, cur, start = [], None, None
            t31 = 0.0
            for i, c in enumerate(cs):
                if c["_shape"] != cur:
                    if cur == "1+3" and start is not None:
                        eps.append(c["timestamp"] - start)
                    cur = c["_shape"]; start = c["timestamp"]
                if c["_shape"] == "1+3" and i + 1 < len(cs):
                    t31 += cs[i + 1]["timestamp"] - c["timestamp"]
            if cur == "1+3" and start is not None:
                eps.append(cs[-1]["timestamp"] - start)
            for e in eps:
                eps_rows.append({"arm": arm, "workload": kind, "rate": rate,
                                 "seed": seed, "duration_s": round(e, 2)})
            # --- swap plans -------------------------------------------
            nswap = ncomp = 0
            lat = []
            for i, c in enumerate(cs):
                if not (c["_plan_shape"] == "2+2" and len(c.get("plan_wants_moved") or []) == 2):
                    continue
                nswap += 1
                for j in range(i + 1, len(cs)):
                    if cs[j]["_shape"] == "2+2" and cs[j].get("convergence_gap") == 0:
                        ncomp += 1
                        lat.append(cs[j]["timestamp"] - c["timestamp"])
                        break
            # --- memory-blocked cycles --------------------------------
            memc = 0
            for c in cs:
                for b in (c.get("blocked") or []):
                    if b.get("reason") == "target memory infeasible":
                        memc += 1
                        mem_rows.append({"arm": arm, "workload": kind, "rate": rate,
                                         "seed": seed, "model": b.get("model"),
                                         "need_gib": b.get("need_gib"),
                                         "free_gib": b.get("free_gib"),
                                         "shortfall_gib": round((b.get("need_gib") or 0)
                                                                - (b.get("free_gib") or 0), 2),
                                         "shape": c["_shape"]})
            cool = sum(1 for c in cs if c.get("cooldown_active"))
            sum_rows.append({"arm": arm, "workload": kind, "rate": rate, "seed": seed,
                             "cycles": len(cs), "span_s": round(span, 1),
                             "cycles_3plus1": sum(1 for c in cs if c["_shape"] == "1+3"),
                             "frac_3plus1_pct": round(100 * t31 / span, 1) if span else None,
                             "episodes": len(eps),
                             "ep_p50_s": round(statistics.median(eps), 2) if eps else None,
                             "ep_p95_s": round(pct(eps, 95), 2) if eps else None,
                             "ep_max_s": round(max(eps), 2) if eps else None,
                             "swap_plans": nswap, "swaps_completed": ncomp,
                             "swap_lat_p50_s": round(statistics.median(lat), 2) if lat else None,
                             "swap_lat_p95_s": round(pct(lat, 95), 2) if lat else None,
                             "swap_lat_max_s": round(max(lat), 2) if lat else None,
                             "cooldown_active_cycles": cool,
                             "memory_blocked_cycles": memc,
                             "migrations": sum(1 for c in cs if c.get("migration_decision") == "MIGRATE")})
    for name, data in (("placement_episodes_on_off", eps_rows),
                       ("mechanism_summary", sum_rows),
                       ("memory_blocked_swaps", mem_rows)):
        if not data:
            continue
        (OUT / f"{name}.json").write_text(json.dumps(data, indent=1))
        with (OUT / f"{name}.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(data[0])); w.writeheader(); w.writerows(data)

    print("Stage B mechanism -- cooldown 30 vs 0\n")
    print(f"{'cond':>12}{'arm':>12}{'3+1%':>7}{'eps':>5}{'ep_p50':>8}{'ep_max':>8}"
          f"{'swaps':>7}{'done':>6}{'lat_p50':>9}{'cool_cyc':>9}{'mem_cyc':>8}{'migr':>6}")
    for kind, rate, seed in CONDS:
        for arm in ("cooldown30", "cooldown0"):
            r = next((x for x in sum_rows if (x["arm"], x["workload"], x["rate"], x["seed"])
                      == (arm, kind, rate, seed)), None)
            if not r:
                continue
            print(f"{kind+str(rate)+'s'+str(seed):>12}{arm:>12}{r['frac_3plus1_pct'] or 0:>7.1f}"
                  f"{r['episodes']:>5}{r['ep_p50_s'] or 0:>8.1f}{r['ep_max_s'] or 0:>8.1f}"
                  f"{r['swap_plans']:>7}{r['swaps_completed']:>6}"
                  f"{r['swap_lat_p50_s'] or 0:>9.1f}{r['cooldown_active_cycles']:>9}"
                  f"{r['memory_blocked_cycles']:>8}{r['migrations']:>6}")
        print()
    for arm in ("cooldown30", "cooldown0"):
        e = [x["duration_s"] for x in eps_rows if x["arm"] == arm]
        if e:
            print(f"{arm}: {len(e)} episodes, p50={statistics.median(e):.1f}s "
                  f"p95={pct(e,95):.1f}s max={max(e):.1f}s total={sum(e):.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
