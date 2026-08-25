#!/usr/bin/env python3
"""Attribute each ping-pong reversal to the exogenous or endogenous input term.

The greedy of kvpr_global_v3._greedy_placement is re-implemented offline and
first VALIDATED: run with the reconstructed rates it must reproduce the plan the
trace recorded. Only cycles where it does are used for attribution.

For a reversal (model M moved g->h at T1, then h->g at T2) we recompute the plan
at T2 three ways:
  observed   : (input_rate@T2, decode@T2)          -> should reproduce the reversal
  exo-only   : (input_rate@T2, decode@T1)          -> did arrivals alone flip it?
  endo-only  : (input_rate@T1, decode@T2)          -> did decode alone flip it?
Whichever counterfactual still reverses M carries the flip.
"""
import csv, json, sys
from collections import defaultdict
from pathlib import Path
ROOT = Path("/workspace/prism-exp")
sys.path.insert(0, str(ROOT / "exp/analysis/planner_oscillation"))
from solve_rates import cycles, ARMS, CONDS, SIZE, GPU_MEM   # noqa: E402
from decompose import CELL, TPOT_SLO                          # noqa: E402
OUT = ROOT / "exp/analysis/planner_oscillation"
TAU = 0.00035
NGPU = 2


def greedy(rates, current_gpu):
    """kvpr_global_v3._greedy_placement, with the availability filter relaxed."""
    order = sorted(current_gpu, key=lambda m: (-rates[m], m))
    shared = {i: GPU_MEM for i in range(NGPU)}
    w = {i: 0.0 for i in range(NGPU)}
    target = {}
    for name in order:
        cur = current_gpu[name]
        ms = SIZE[name]
        cands = [i for i in range(NGPU) if shared[i] > ms] or [cur]
        ratios = {i: (w[i] / shared[i] if shared[i] > 0 else float("inf")) for i in cands}
        best = min(ratios, key=lambda i: (ratios[i], i))
        cur_r = w[cur] / shared[cur] if shared.get(cur, 0) > 0 else float("inf")
        chosen = best if (cur_r - ratios[best]) > TAU else cur
        target[name] = chosen
        w[chosen] += rates[name]
        shared[chosen] -= ms
    return target


def main():
    rows = list(csv.DictReader(open(OUT / "cycle_inputs.csv")))
    by = defaultdict(dict)
    for r in rows:
        by[(r["arm"], r["cond"], float(r["timestamp"]))][r["model"]] = {
            "ir": float(r["input_rate"]), "dec": float(r["decode_token_tput"]),
            "gpu": int(r["gpu"]) if r["gpu"] not in ("", "None") else None,
            "wr": float(r["weighted_token_rate"])}
    def wr(m, ir, dec):
        return (ir + dec) * CELL[m] / TPOT_SLO[m] / (2 ** 30)

    # --- validate the offline greedy against the recorded plan --------------
    okv = tot = 0
    plans = {}
    for arm, base in ARMS.items():
        for kind, rate, seed in CONDS:
            for c in cycles(base / kind / f"rate_{rate}" / f"seed_{seed}"
                            / "server-logs/server.log.global_controller.log"):
                k = (arm, f"{kind}{rate}s{seed}", c["timestamp"])
                d = by.get(k)
                if not d or not c.get("placement_plan"):
                    continue
                cur = {m: v["gpu"] for m, v in d.items() if v["gpu"] is not None}
                if len(cur) != len(d):
                    continue
                tot += 1
                got = greedy({m: v["wr"] for m, v in d.items()}, cur)
                if got == c["placement_plan"]:
                    okv += 1
                    plans[k] = (cur, d, c)
    print(f"offline greedy reproduces the recorded placement_plan in {okv}/{tot} "
          f"cycles ({100*okv/max(1,tot):.1f}%)\n")

    # --- reversals and attribution -----------------------------------------
    att = []
    for arm, base in ARMS.items():
        for kind, rate, seed in CONDS:
            cond = f"{kind}{rate}s{seed}"
            cs = cycles(base / kind / f"rate_{rate}" / f"seed_{seed}"
                        / "server-logs/server.log.global_controller.log")
            migs = [(c["timestamp"], c["candidate"]["model"], c["candidate"]["from"],
                     c["candidate"]["to"]) for c in cs
                    if c.get("migration_decision") == "MIGRATE" and c.get("candidate")]
            used = set()
            for i, (t1, m, s1, d1) in enumerate(migs):
                for j in range(i + 1, len(migs)):
                    t2, m2, s2, d2 = migs[j]
                    if j in used or m2 != m or not (s2 == d1 and d2 == s1):
                        continue
                    used.add(j)
                    k1, k2 = (arm, cond, t1), (arm, cond, t2)
                    if k1 not in plans or k2 not in plans:
                        att.append({"arm": arm, "cond": cond, "model": m,
                                    "t1": t1, "t2": t2, "interval_s": round(t2 - t1, 2),
                                    "attribution": "UNKNOWN",
                                    "note": "a decision cycle did not validate"})
                        break
                    cur2, d2v, _ = plans[k2]
                    d1v = plans[k1][1]
                    def plan_with(use_ir_from, use_dec_from):
                        rr = {}
                        for mm in cur2:
                            ir = (d2v if use_ir_from == 2 else d1v)[mm]["ir"]
                            dc = (d2v if use_dec_from == 2 else d1v)[mm]["dec"]
                            rr[mm] = wr(mm, ir, dc)
                        return greedy(rr, cur2)
                    obs = plan_with(2, 2)
                    exo = plan_with(2, 1)      # arrivals moved, decode frozen
                    endo = plan_with(1, 2)     # decode moved, arrivals frozen
                    want = s1               # the reversal sends M back to s1
                    a = ("EXOGENOUS_LOAD_CHANGE" if exo.get(m) == want and endo.get(m) != want
                         else "ENDOGENOUS_SERVICE_FEEDBACK" if endo.get(m) == want and exo.get(m) != want
                         else "MIXED" if exo.get(m) == want and endo.get(m) == want
                         else "NEITHER_ALONE")
                    att.append({"arm": arm, "cond": cond, "model": m,
                                "t1": t1, "t2": t2, "interval_s": round(t2 - t1, 2),
                                "observed_reproduced": obs.get(m) == want,
                                "exo_only_flips": exo.get(m) == want,
                                "endo_only_flips": endo.get(m) == want,
                                "attribution": a,
                                "d_input_rate": round(d2v[m]["ir"] - d1v[m]["ir"], 2),
                                "d_decode_tput": round(d2v[m]["dec"] - d1v[m]["dec"], 2)})
                    break
    with (OUT / "reversal_attribution.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=sorted({k for r in att for k in r}))
        w.writeheader(); w.writerows(att)
    (OUT / "reversal_attribution.json").write_text(json.dumps(att, indent=1))
    from collections import Counter
    print(f"reversals analysed: {len(att)}")
    for arm in ARMS:
        rs = [r for r in att if r["arm"] == arm]
        print(f"\n{arm}: n={len(rs)}")
        for k, v in Counter(r["attribution"] for r in rs).most_common():
            print(f"   {v:3d}  {k}")
        rep = sum(1 for r in rs if r.get("observed_reproduced"))
        print(f"   (offline greedy reproduced the observed reversal in {rep}/{len(rs)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
