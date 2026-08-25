#!/usr/bin/env python3
"""Phase 6 §2/§3 -- can per-model weighted_token_rate be recovered from line8?

The greedy pass (kvpr_global_v3._greedy_placement) walks models in descending
weighted rate, keeping w_rate[gpu] and shared_kv[gpu]. Each line8 row exposes
  current_r = w_rate[current_gpu] / shared_kv[current_gpu]
  best_r    = w_rate[best_gpu]    / shared_kv[best_gpu]
evaluated BEFORE that model is placed. shared_kv is fully determined by the
chosen_gpu of the preceding rows plus the fixed model_size table, so each row
yields observations of w_rate on up to two GPUs at a known step. w_rate[g]
changes only at the step that places a model on g, so differencing consecutive
observations recovers that model's rate.

Validation is independent: the reconstructed rates are fed back through the
recorded ACTUAL placement and must reproduce the trace's own `kvpr` field.
"""
import json, sys
from pathlib import Path
ROOT = Path("/workspace/prism-exp")
SIZE = {"model_3": 6.0, "model_4": 5.8359375,
        "model_5": 15.080078125, "model_6": 14.283203125}
GPU_MEM = 79.25          # "Total single GPU memory: 79.25 GB" from the run logs
NGPU = 2


def reconstruct(cycle):
    """Return (rates_by_model, n_exact) or (None, 0)."""
    l8 = cycle.get("line8") or []
    if not l8:
        return None, 0
    shared = {i: GPU_MEM for i in range(NGPU)}
    obs = {i: [] for i in range(NGPU)}        # (step, w_rate)
    for step, row in enumerate(l8):
        g, b = row["current_gpu"], row["best_gpu"]
        if shared[g] > 0:
            obs[g].append((step, row["current_r"] * shared[g]))
        if shared[b] > 0:
            obs[b].append((step, row["best_r"] * shared[b]))
        shared[row["chosen_gpu"]] -= SIZE.get(row["model"], 0.0)
    rates, exact = {}, 0
    for step, row in enumerate(l8):
        c = row["chosen_gpu"]
        before = [v for s, v in obs[c] if s <= step]
        after = [v for s, v in obs[c] if s > step]
        if before and after:
            rates[row["model"]] = after[0] - before[-1]
            exact += 1
        else:
            rates[row["model"]] = None
    return rates, exact


def validate(cycle, rates):
    """Feed reconstructed rates through the ACTUAL placement; compare to `kvpr`."""
    cur = cycle.get("current_placement") or {}
    rec = cycle.get("kvpr") or {}
    if not cur or not rec or any(rates.get(m) is None for m in cur):
        return None
    worst = 0.0
    for gid in range(NGPU):
        models = [m for m, g in cur.items() if g == gid]
        used = sum(SIZE.get(m, 0.0) for m in models)
        kv = GPU_MEM - used
        if kv <= 0:
            return None
        got = sum(rates[m] for m in models) / kv
        want = rec.get(str(gid))
        if want is None:
            return None
        worst = max(worst, abs(got - want) / want if want else abs(got - want))
    return worst


def cycles(p):
    out = []
    if not Path(p).is_file():
        return out
    for line in open(p, errors="replace"):
        i = line.find("[PAPER-ALG1-V4] {")
        if i < 0:
            continue
        try:
            d = json.loads(line[i + len("[PAPER-ALG1-V4] "):])
        except json.JSONDecodeError:
            continue
        if d.get("current_placement"):
            out.append(d)
    out.sort(key=lambda x: x["timestamp"])
    return out


if __name__ == "__main__":
    ARMS = {"cooldown30": ROOT / "exp/results/4het-paired/raw/prism",
            "cooldown0": ROOT / "exp/results/4het-cooldown-diagnostic/raw/prism-cooldown0"}
    print("Reconstruction feasibility -- validated against the trace's own kvpr field\n")
    print(f"{'arm':>12}{'cond':>12}{'cycles':>8}{'all4_exact':>12}{'validated':>11}{'max_rel_err':>13}")
    tot = ok = val = 0
    worst_all = 0.0
    for arm, base in ARMS.items():
        for kind, rate, seed in [("steady", 8, 1), ("steady", 8, 2),
                                 ("steady", 10, 1), ("steady", 10, 2)]:
            cs = cycles(base / kind / f"rate_{rate}" / f"seed_{seed}"
                        / "server-logs/server.log.global_controller.log")
            n4 = nv = 0
            worst = 0.0
            for c in cs:
                r, e = reconstruct(c)
                if r and e == len(c.get("line8") or []):
                    n4 += 1
                    w = validate(c, r)
                    if w is not None:
                        nv += 1
                        worst = max(worst, w)
            tot += len(cs); ok += n4; val += nv; worst_all = max(worst_all, worst)
            print(f"{arm:>12}{kind+str(rate)+'s'+str(seed):>12}{len(cs):>8}{n4:>12}{nv:>11}"
                  f"{worst:>13.2e}")
    print(f"\ntotal cycles={tot}  all-4-models-exact={ok} ({100*ok/tot:.1f}%)  "
          f"kvpr-validated={val} ({100*val/tot:.1f}%)  worst relative error={worst_all:.2e}")
