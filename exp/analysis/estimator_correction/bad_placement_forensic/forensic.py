#!/usr/bin/env python3
"""Sections 4-8, 10 -- why Alg1 selects/persists large-large co-residency.

Offline only. Reads the [PAPER-ALG1-V4] cycle traces and the Phase 6 exact
weighted-rate solver. Writes tables; touches no raw log.
"""
import csv, itertools, json, statistics, sys
from collections import defaultdict
from pathlib import Path
ROOT = Path("/workspace/prism-exp")
OUT = ROOT / "exp/analysis/estimator_correction/bad_placement_forensic"
sys.path.insert(0, str(ROOT / "exp/analysis/planner_oscillation"))
from solve_rates import cycles, solve, SIZE, GPU_MEM, NGPU     # noqa: E402

TAU = 0.00035
BIG = ("model_5", "model_6")            # Llama-3.1-8B, Qwen2.5-7B
ARMS = {"NEW": ROOT / "exp/results/4het-estimator-correction/raw/prism-estimator",
        "OLD": ROOT / "exp/results/4het-paired/raw/prism"}
CONDS = [("steady", 8, 1), ("steady", 8, 2), ("steady", 10, 1), ("steady", 10, 2)]
BAD = {"steady8s1", "steady10s1"}


def log_of(base, kind, rate, seed):
    return base / kind / f"rate_{rate}" / f"seed_{seed}" / "server-logs/server.log.global_controller.log"


def colocated(place):
    return place.get(BIG[0]) is not None and place[BIG[0]] == place.get(BIG[1])


def kvpr_of(assign, rates):
    """peak KVPR and per-GPU KVPR for a full assignment {model: gpu}."""
    per = {}
    for g in range(NGPU):
        ms = [m for m, x in assign.items() if x == g]
        shared = GPU_MEM - sum(SIZE[m] for m in ms)
        if shared <= 0:
            return float("inf"), None
        per[g] = sum(rates[m] for m in ms) / shared
    return max(per.values()), per


def greedy(rates, current, order=None):
    """kvpr_global_v3._greedy_placement, literal."""
    order = order or sorted(current, key=lambda m: (-rates[m], m))
    shared = {i: GPU_MEM for i in range(NGPU)}
    w = {i: 0.0 for i in range(NGPU)}
    target, steps = {}, []
    for name in order:
        cur = current[name]; ms = SIZE[name]
        cands = [i for i in range(NGPU) if shared[i] > ms] or [cur]
        ratios = {i: (w[i] / shared[i] if shared[i] > 0 else float("inf")) for i in cands}
        best = min(ratios, key=lambda i: (ratios[i], i))
        cur_r = w[cur] / shared[cur] if shared.get(cur, 0) > 0 else float("inf")
        chosen = best if (cur_r - ratios[best]) > TAU else cur
        steps.append({"model": name, "w_rate": rates[name], "size": SIZE[name],
                      "cand_r": {str(k): v for k, v in ratios.items()},
                      "best_gpu": best, "current_gpu": cur, "current_r": cur_r,
                      "delta": cur_r - ratios[best], "chosen_gpu": chosen})
        target[name] = chosen; w[chosen] += rates[name]; shared[chosen] -= ms
    return target, steps


def main():
    tl_rows, ep_rows, enum_rows, step_rows = [], [], [], []
    replay_ok = replay_tot = 0
    for arm, base in ARMS.items():
        for kind, rate, seed in CONDS:
            cond = f"{kind}{rate}s{seed}"
            cs = cycles(log_of(base, kind, rate, seed))
            solved = {}
            for c in cs:
                r, resid = solve(c)
                if r is not None:
                    solved[c["cycle"]] = r
                cur, plan = c.get("current_placement") or {}, c.get("placement_plan") or {}
                tl_rows.append({
                    "arm": arm, "cond": cond, "cycle": c["cycle"], "timestamp": c["timestamp"],
                    "current_shape": "+".join(str(sum(1 for m in cur if cur[m] == g))
                                              for g in range(NGPU)) if cur else None,
                    "current_colocated": colocated(cur), "plan_colocated": colocated(plan),
                    "plan_changed": None, "migration_decision": c.get("migration_decision"),
                    "migration_reason": c.get("migration_reason"),
                    "candidate": (c.get("candidate") or {}).get("model"),
                    "cand_from": (c.get("candidate") or {}).get("from"),
                    "cand_to": (c.get("candidate") or {}).get("to"),
                    "cooldown_active": c.get("cooldown_active"),
                    "blocked": json.dumps(c.get("blocked") or []),
                    "misplaced": json.dumps(c.get("misplaced_models") or []),
                    "kvpr0": (c.get("kvpr") or {}).get("0"),
                    "kvpr1": (c.get("kvpr") or {}).get("1"),
                    "peak_kvpr": c.get("peak_kvpr"),
                    "alg1_order": json.dumps([x["model"] for x in c.get("line8") or []]),
                    "rates": json.dumps(solved.get(c["cycle"])),
                    "solve_resid": resid,
                    "current": json.dumps(cur), "plan": json.dumps(plan),
                })

            # ---- episodes of actual large-large residency -------------------
            run, cur_ep = None, []
            seq = [r for r in tl_rows if r["arm"] == arm and r["cond"] == cond]
            for r in seq:
                if r["current_colocated"]:
                    cur_ep.append(r)
                elif cur_ep:
                    ep_rows.append(_episode(arm, cond, cur_ep)); cur_ep = []
            if cur_ep:
                ep_rows.append(_episode(arm, cond, cur_ep))

            # ---- replay + enumeration at critical cycles ---------------------
            if arm != "NEW":
                continue
            crit = [e["start_cycle"] for e in ep_rows
                    if e["arm"] == arm and e["cond"] == cond]
            for c in cs:
                if c["cycle"] not in solved:
                    continue
                rates = solved[c["cycle"]]
                cur = c.get("current_placement") or {}
                rec = c.get("placement_plan") or {}
                if not cur or not rec:
                    continue
                rp, steps = greedy(rates, cur)
                match = rp == rec
                replay_tot += 1; replay_ok += int(match)
                is_crit = c["cycle"] in crit
                # exhaustive enumeration over all 2^4 assignments
                cand = []
                for bits in itertools.product(range(NGPU), repeat=4):
                    a = dict(zip(("model_3", "model_4", "model_5", "model_6"), bits))
                    if len(set(a.values())) < NGPU:
                        continue            # an empty GPU is not a valid placement
                    pk, per = kvpr_of(a, rates)
                    if per is None:
                        continue
                    cand.append((pk, a, per))
                cand.sort(key=lambda x: x[0])
                chosen_pk, _ = kvpr_of(rec, rates)
                best_pk = cand[0][0]
                sep = [x for x in cand if x[1][BIG[0]] != x[1][BIG[1]]]
                col = [x for x in cand if x[1][BIG[0]] == x[1][BIG[1]]]
                best_sep = sep[0][0] if sep else None
                best_col = col[0][0] if col else None
                rank = 1 + sum(1 for x in cand if x[0] < chosen_pk - 1e-15)
                rank_sep = 1 + sum(1 for x in cand if x[0] < best_sep - 1e-15) if sep else None
                enum_rows.append({
                    "arm": arm, "cond": cond, "cycle": c["cycle"],
                    "timestamp": c["timestamp"], "critical": is_crit,
                    "chosen_colocated": colocated(rec), "replay_match": match,
                    "chosen_peak_kvpr": chosen_pk, "best_peak_kvpr": best_pk,
                    "best_separated_peak_kvpr": best_sep,
                    "best_colocated_peak_kvpr": best_col,
                    "objective_margin_sep_minus_chosen": (best_sep - chosen_pk) if sep else None,
                    "rel_margin_sep": ((best_sep - chosen_pk) / chosen_pk) if sep and chosen_pk else None,
                    "chosen_rank": rank, "best_separated_rank": rank_sep,
                    "n_valid": len(cand),
                    "colocated_is_optimal": bool(col and abs(best_col - best_pk) < 1e-15),
                    "separated_strictly_better": bool(sep and best_sep < chosen_pk - 1e-15),
                    "chosen_plan": json.dumps(rec), "replay_plan": json.dumps(rp),
                    "rates": json.dumps(rates),
                })
                if is_crit:
                    for i, s in enumerate(steps):
                        step_rows.append({"cond": cond, "cycle": c["cycle"], "step": i, **s,
                                          "cand_r": json.dumps(s["cand_r"])})

    for name, data in (("timeline", tl_rows), ("large_large_episodes", ep_rows),
                       ("kvpr_enumeration", enum_rows), ("greedy_steps", step_rows)):
        if not data:
            continue
        with (OUT / f"{name}.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(data[0])); w.writeheader(); w.writerows(data)
    json.dump({"replay_matched": replay_ok, "replay_total": replay_tot},
              open(OUT / "replay.json", "w"), indent=1)
    print(f"ALG1_REPLAY (NEW runs) = {replay_ok}/{replay_tot} "
          f"({100*replay_ok/replay_tot:.1f}%)")
    print(f"cycles: {len(tl_rows)}  episodes: {len(ep_rows)}  enum cycles: {len(enum_rows)}")
    return 0


def _episode(arm, cond, rows):
    n = len(rows)
    plan_col = sum(1 for r in rows if r["plan_colocated"])
    dur = rows[-1]["timestamp"] - rows[0]["timestamp"]
    if plan_col / n > 0.5:
        cls = "DIRECT_SELECTION"
    elif n <= 2:
        cls = "INTERMEDIATE_STATE"
    else:
        cls = "STALE_OR_FAILED_ACTUATION"
    return {"arm": arm, "cond": cond, "start_cycle": rows[0]["cycle"],
            "end_cycle": rows[-1]["cycle"], "n_cycles": n,
            "start_ts": rows[0]["timestamp"], "duration_s": round(dur, 1),
            "plan_colocated_cycles": plan_col,
            "plan_colocated_frac": round(plan_col / n, 3),
            "classification": cls,
            "any_blocked": any(json.loads(r["blocked"]) for r in rows),
            "cooldown_cycles": sum(1 for r in rows if r["cooldown_active"])}


if __name__ == "__main__":
    sys.exit(main())
