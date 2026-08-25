#!/usr/bin/env python3
"""Recover per-model weighted_token_rate exactly, as a linear system.

Unknowns: weighted_token_rate for each resident model.
Equations, all from the trace itself:
  (a) line8 row k exposes w_rate[g] = sum of rates already placed on g by the
      greedy, for g in {current_gpu, best_gpu}, with shared_kv[g] determined by
      the chosen_gpu of the preceding rows and the fixed model_size table.
  (b) the recorded `kvpr` field gives, per GPU, the sum of rates over the ACTUAL
      resident models divided by (gpu_mem - sum model_size).
Solved by least squares; the residual is reported, so an inexact recovery is
visible rather than silent.
"""
import json, sys
from pathlib import Path
import numpy as np
ROOT = Path("/workspace/prism-exp")
SIZE = {"model_3": 6.0, "model_4": 5.8359375,
        "model_5": 15.080078125, "model_6": 14.283203125}
GPU_MEM, NGPU = 79.25, 2


def solve(cycle):
    l8 = cycle.get("line8") or []
    cur = cycle.get("current_placement") or {}
    kv = cycle.get("kvpr") or {}
    models = sorted({r["model"] for r in l8} | set(cur))
    if not models:
        return None, None
    idx = {m: i for i, m in enumerate(models)}
    A, b = [], []
    shared = {i: GPU_MEM for i in range(NGPU)}
    placed = {i: [] for i in range(NGPU)}
    for row in l8:
        for gpu, ratio in ((row["current_gpu"], row["current_r"]),
                           (row["best_gpu"], row["best_r"])):
            if shared.get(gpu, 0) <= 0:
                continue
            v = np.zeros(len(models))
            for m in placed[gpu]:
                v[idx[m]] = 1.0
            A.append(v); b.append(ratio * shared[gpu])
        c = row["chosen_gpu"]
        placed[c].append(row["model"])
        shared[c] -= SIZE.get(row["model"], 0.0)
    for gid in range(NGPU):
        res = [m for m, g in cur.items() if g == gid]
        if not res or str(gid) not in kv:
            continue
        skv = GPU_MEM - sum(SIZE.get(m, 0.0) for m in res)
        if skv <= 0:
            continue
        v = np.zeros(len(models))
        for m in res:
            v[idx[m]] = 1.0
        A.append(v); b.append(kv[str(gid)] * skv)
    if not A:
        return None, None
    A, b = np.array(A), np.array(b)
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    resid = float(np.max(np.abs(A @ sol - b))) if len(b) else float("inf")
    scale = max(1e-12, float(np.max(np.abs(b))))
    return {m: float(sol[idx[m]]) for m in models}, resid / scale


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


ARMS = {"cooldown30": ROOT / "exp/results/4het-paired/raw/prism",
        "cooldown0": ROOT / "exp/results/4het-cooldown-diagnostic/raw/prism-cooldown0"}
CONDS = [("steady", 8, 1), ("steady", 8, 2), ("steady", 10, 1), ("steady", 10, 2)]

if __name__ == "__main__":
    print("Exactness of the reconstructed per-model weighted_token_rate\n")
    print(f"{'arm':>12}{'cond':>12}{'cycles':>8}{'solved':>8}{'resid<1e-6':>12}{'max_resid':>12}")
    T = S = G = 0
    for arm, base in ARMS.items():
        for kind, rate, seed in CONDS:
            cs = cycles(base / kind / f"rate_{rate}" / f"seed_{seed}"
                        / "server-logs/server.log.global_controller.log")
            n = good = 0
            worst = 0.0
            for c in cs:
                r, res = solve(c)
                if r is None:
                    continue
                n += 1
                worst = max(worst, res)
                if res < 1e-6:
                    good += 1
            T += len(cs); S += n; G += good
            print(f"{arm:>12}{kind+str(rate)+'s'+str(seed):>12}{len(cs):>8}{n:>8}{good:>12}{worst:>12.2e}")
    print(f"\ntotal={T} solved={S} exact(resid<1e-6)={G} ({100*G/max(1,T):.1f}%)")
