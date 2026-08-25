#!/usr/bin/env python3
"""Offline replay -- fixed pre-registered variant set (no parameter search).

  primary      : migrated model's decode term held at its pre-migration value, 2 cycles
  persistence  : same, 3 cycles                    (is the conclusion an artefact of K=2?)
  drop_only    : 2 cycles, but only the artificial DROP is removed -- max(observed, pre).
                 The post-migration spike is left untouched, isolating the
                 deactivation-gating artefact from the genuine ramp-back-up.

Everything else is unchanged: same input_rate, same objective, same tau, same greedy.
Reported per attribution class, because selective suppression of the ENDOGENOUS
class is the discriminating result -- uniform suppression would instead indicate
the counterfactual is simply too aggressive.
"""
import csv, json, sys
from collections import defaultdict
from pathlib import Path
ROOT = Path("/workspace/prism-exp")
OUT = ROOT / "exp/analysis/planner_oscillation"
sys.path.insert(0, str(OUT))
from solve_rates import cycles, ARMS, CONDS            # noqa: E402
from decompose import CELL, TPOT_SLO                   # noqa: E402
from attribute import greedy                           # noqa: E402

VARIANTS = (("primary", 2, "hold"), ("persistence", 3, "hold"), ("drop_only", 2, "drop"))


def wr(m, ir, dec):
    return (ir + dec) * CELL[m] / TPOT_SLO[m] / (2 ** 30)


def main():
    per = defaultdict(dict)
    for r in csv.DictReader(open(OUT / "cycle_inputs.csv")):
        per[(r["arm"], r["cond"])].setdefault(float(r["timestamp"]), {})[r["model"]] = {
            "ir": float(r["input_rate"]), "dec": float(r["decode_token_tput"]),
            "gpu": int(r["gpu"]) if r["gpu"] not in ("", "None") else None}
    att = json.load(open(OUT / "reversal_attribution.json"))
    classes = ["EXOGENOUS_LOAD_CHANGE", "ENDOGENOUS_SERVICE_FEEDBACK", "MIXED", "NEITHER_ALONE", "UNKNOWN"]
    out, detail = [], []
    for name, K, mode in VARIANTS:
        sup = defaultdict(int); tot = defaultdict(int)
        for arm, base in ARMS.items():
            for kind, rate, seed in CONDS:
                cond = f"{kind}{rate}s{seed}"
                cs = cycles(base / kind / f"rate_{rate}" / f"seed_{seed}"
                            / "server-logs/server.log.global_controller.log")
                ts = sorted(per[(arm, cond)])
                if not ts:
                    continue
                idx = {t: i for i, t in enumerate(ts)}
                migs = [(c["timestamp"], c["candidate"]["model"]) for c in cs
                        if c.get("migration_decision") == "MIGRATE" and c.get("candidate")]
                frozen = {}
                for mt, mm in migs:
                    i = idx[min(ts, key=lambda x: abs(x - mt))]
                    if i == 0:
                        continue
                    prev = per[(arm, cond)][ts[i - 1]].get(mm)
                    if prev is None:
                        continue
                    for j in range(i, min(i + K + 1, len(ts))):
                        cur = per[(arm, cond)][ts[j]].get(mm)
                        if cur is None:
                            continue
                        frozen[(ts[j], mm)] = (prev["dec"] if mode == "hold"
                                               else max(cur["dec"], prev["dec"]))
                for r in att:
                    if r["arm"] != arm or r["cond"] != cond:
                        continue
                    near = min(ts, key=lambda x: abs(x - r["t2"]))
                    d = per[(arm, cond)][near]
                    if any(v["gpu"] is None for v in d.values()):
                        continue
                    cls = r["attribution"]; tot[cls] += 1
                    cur_gpu = {m: v["gpu"] for m, v in d.items()}
                    rr = {m: wr(m, v["ir"], frozen.get((near, m), v["dec"])) for m, v in d.items()}
                    if greedy(rr, cur_gpu).get(r["model"]) == cur_gpu[r["model"]]:
                        sup[cls] += 1
                        detail.append({"variant": name, "arm": arm, "cond": cond,
                                       "model": r["model"], "t2": r["t2"], "attribution": cls})
        row = {"variant": name, "hold_cycles": K, "mode": mode,
               "total": sum(tot.values()), "suppressed": sum(sup.values())}
        row["suppressed_pct"] = round(100 * row["suppressed"] / row["total"], 1)
        for c in classes:
            row[c] = f"{sup[c]}/{tot[c]}"
        out.append(row)
    with (OUT / "replay_freeze.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0])); w.writeheader(); w.writerows(out)
    with (OUT / "replay_suppressed_detail.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(detail[0])); w.writeheader(); w.writerows(detail)
    (OUT / "replay_freeze.json").write_text(json.dumps(out, indent=1))
    for r in out:
        print(f"\n=== {r['variant']}  (hold {r['hold_cycles']} cycles, {r['mode']}) ===")
        print(f"  suppressed {r['suppressed']}/{r['total']}  ({r['suppressed_pct']}%)")
        for c in classes:
            s, t = r[c].split("/")
            pct = f"{100*int(s)/int(t):5.1f}%" if int(t) else "    -"
            print(f"    {c:<30} {s:>3}/{t:<3} {pct}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
