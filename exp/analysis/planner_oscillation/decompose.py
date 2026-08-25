#!/usr/bin/env python3
"""Split Algorithm 1's demand term into its exogenous and endogenous parts.

weighted_token_rate = (input_rate + decode_token_tput) * cell_size / tpot_slo / 2**30

  input_rate        EXOGENOUS  -- prompt tokens that ARRIVED in the last 30 s.
                    Recomputed independently from the per-request dump, which is
                    the same quantity the policy sums (received_reqs is never
                    pruned, so the window filter selects by arrival time).
  decode_token_tput ENDOGENOUS -- tokens the engine ACTUALLY decoded, per second,
                    reported every 1 s and only while the model is activated.

decode_token_tput is obtained by subtraction, so every error in the independent
input_rate lands in it; the residual check in solve_rates.py bounds the w_rate
side, and the two terms are reported together so the split stays inspectable.
"""
import bisect, csv, json, sys
from pathlib import Path
import numpy as np
ROOT = Path("/workspace/prism-exp")
sys.path.insert(0, str(ROOT / "exp/analysis/planner_oscillation"))
sys.path.insert(0, str(ROOT / "exp/analysis"))
from solve_rates import solve, cycles, ARMS, CONDS, SIZE, GPU_MEM   # noqa: E402
from model_state_overlap import load_reqs                            # noqa: E402
OUT = ROOT / "exp/analysis/planner_oscillation"
CELL = {"model_3": 114688, "model_4": 36864, "model_5": 131072, "model_6": 57344}
SLO_BASE = {"model_3": 0.010592997074127197, "model_4": 0.012399986386299133,
            "model_5": 0.012781862169504166, "model_6": 0.01164202019572258}
TPOT_SLO = {m: v * 3.0 for m, v in SLO_BASE.items()}   # kvpr-tpot-slo-scale 3
WINDOW = 30.0


def input_rate_series(run):
    """arrival-time sorted (t, prompt_len) per model, for the 30 s sliding sum."""
    per = {}
    for r in load_reqs(run):
        if not isinstance(r, dict):
            continue
        m, at, pl = r.get("model"), r.get("arrival_time"), r.get("prompt_len")
        if m and at and pl:
            per.setdefault(m, []).append((at, float(pl)))
    for m in per:
        per[m].sort()
    return {m: (np.array([x[0] for x in v]), np.cumsum([x[1] for x in v]))
            for m, v in per.items()}


def input_rate_at(series, m, t):
    if m not in series:
        return 0.0
    ts, cum = series[m]
    hi = bisect.bisect_right(ts, t)
    lo = bisect.bisect_left(ts, t - WINDOW)
    tot = (cum[hi - 1] if hi else 0.0) - (cum[lo - 1] if lo else 0.0)
    return float(tot) / WINDOW


def build(arm, base, kind, rate, seed):
    run = base / kind / f"rate_{rate}" / f"seed_{seed}"
    cs = cycles(run / "server-logs/server.log.global_controller.log")
    series = input_rate_series(run)
    rows = []
    for c in cs:
        w, resid = solve(c)
        if w is None or resid > 1e-5:
            continue
        cur = c.get("current_placement") or {}
        for m, wr in w.items():
            if m not in CELL:
                continue
            token_rate = wr * (2 ** 30) * TPOT_SLO[m] / CELL[m]
            ir = input_rate_at(series, m, c["timestamp"])
            rows.append({"arm": arm, "cond": f"{kind}{rate}s{seed}",
                         "cycle": c.get("cycle"), "timestamp": c["timestamp"],
                         "model": m, "gpu": cur.get(m),
                         "weighted_token_rate": wr,
                         "token_rate": token_rate,
                         "input_rate": ir,
                         "decode_token_tput": token_rate - ir,
                         "cell_size": CELL[m], "tpot_slo_s": TPOT_SLO[m],
                         "kvpr0": (c.get("kvpr") or {}).get("0"),
                         "kvpr1": (c.get("kvpr") or {}).get("1"),
                         "decision": c.get("migration_decision"),
                         "moved_model": (c.get("candidate") or {}).get("model"),
                         "resid": resid})
    return rows


if __name__ == "__main__":
    allr = []
    for arm, base in ARMS.items():
        for kind, rate, seed in CONDS:
            allr += build(arm, base, kind, rate, seed)
    with (OUT / "cycle_inputs.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(allr[0])); w.writeheader(); w.writerows(allr)
    (OUT / "cycle_inputs.json").write_text(json.dumps(allr[:4000], indent=1))
    print(f"cycle_inputs rows: {len(allr)}\n")
    neg = sum(1 for r in allr if r["decode_token_tput"] < -1.0)
    print(f"sanity: decode_token_tput materially negative in {neg}/{len(allr)} rows "
          f"({100*neg/len(allr):.1f}%) -- negatives bound the accuracy of the split\n")
    print("Share of the demand term that is ENDOGENOUS (decode throughput):")
    print(f"{'cond':>12}{'arm':>12}" + "".join(f"{m:>12}" for m in ("model_3","model_4","model_5","model_6")))
    for kind, rate, seed in CONDS:
        for arm in ARMS:
            rs = [r for r in allr if r["arm"] == arm and r["cond"] == f"{kind}{rate}s{seed}"]
            if not rs:
                continue
            line = f"{kind+str(rate)+'s'+str(seed):>12}{arm:>12}"
            for m in ("model_3", "model_4", "model_5", "model_6"):
                v = [r for r in rs if r["model"] == m]
                if not v:
                    line += f"{'--':>12}"; continue
                tot = sum(abs(x["token_rate"]) for x in v)
                dec = sum(abs(x["decode_token_tput"]) for x in v)
                line += f"{100*dec/tot if tot else 0:>11.1f}%"
            print(line)
