#!/usr/bin/env python3
"""Stage A -- offline achievable-gain bound for the intermediate 3+1 state.

Run exposure (wall time in 3+1) and DECODE exposure (decode steps that happened
while the request's own GPU held 3 models) are computed separately; they are not
the same quantity and are not approximated as each other.

The counterfactual is descriptive, not causal: it asks what aggregate TPOT would
look like if the requests that decoded under 3-model co-residency had instead
shown the SAME RUN's, SAME MODEL's empirical 2-model-GPU decode cost. It is an
exposure-weighted bound, not a prediction of what removing the cooldown does.
"""
import bisect, csv, json, statistics, sys
from pathlib import Path
ROOT = Path("/workspace/prism-exp")
sys.path.insert(0, str(ROOT / "exp/analysis"))
from model_state_overlap import load_reqs   # noqa: E402
RAW = ROOT / "exp/results/4het-paired/raw/prism"
OUT = ROOT / "exp/analysis/swap_pathology_bound"
CONDS = [("steady", 8, 1), ("steady", 8, 2), ("steady", 10, 1), ("steady", 10, 2)]
NAME = {"model_3": "Llama-3.2-3B", "model_4": "Qwen2.5-3B",
        "model_5": "Llama-3.1-8B", "model_6": "Qwen2.5-7B"}


def pct(v, q):
    v = sorted(v)
    if not v:
        return None
    k = (len(v) - 1) * q / 100
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def timeline(cycles):
    ts, per_model, shape = [], [], []
    for c in sorted(cycles, key=lambda x: x["timestamp"]):
        p = json.loads(c["current_placement"])
        if not p:
            continue
        cnt = {}
        for m, g in p.items():
            cnt[g] = cnt.get(g, 0) + 1
        ts.append(c["timestamp"])
        per_model.append({m: cnt[g] for m, g in p.items()})
        shape.append("+".join(map(str, sorted(cnt.values()))))
    return ts, per_model, shape


def main():
    allc = json.loads((ROOT / "exp/analysis/alg1/alg1_cycles.json").read_text())
    rows, model_rows, cf_rows = [], [], []
    for kind, rate, seed in CONDS:
        cyc = [c for c in allc if (c["workload"], c["rate"], c["seed"]) == (kind, rate, seed)]
        ts, per_model, shape = timeline(cyc)
        run = RAW / kind / f"rate_{rate}" / f"seed_{seed}"
        reqs = [r for r in load_reqs(run) if isinstance(r, dict) and r.get("success")]
        if not (ts and reqs):
            continue
        t0 = min(r["arrival_time"] for r in reqs)
        t1 = max(r["finish_time"] for r in reqs)
        span = t1 - t0

        # --- A1a: run-time exposure (wall clock in each shape) -------------
        wall = {}
        for i, t in enumerate(ts):
            a = max(t, t0)
            b = min(ts[i + 1] if i + 1 < len(ts) else t1, t1)
            if b > a:
                wall[shape[i]] = wall.get(shape[i], 0.0) + (b - a)
        run31 = wall.get("1+3", 0.0)

        # --- A1b: DECODE exposure, per model and overall ------------------
        dec = {}          # (model, n_on_gpu) -> [itl gaps]
        req_bucket = {}   # (model, n_at_start) -> [tpot]
        for r in reqs:
            m, dts, itl = r.get("model"), r.get("decode_timestamps") or [], r.get("itl") or []
            if m not in NAME or len(dts) < 2 or not itl:
                continue
            for i, gap in enumerate(itl):
                j = bisect.bisect_right(ts, dts[i]) - 1
                if j < 0:
                    continue
                n = per_model[j].get(m)
                if n:
                    dec.setdefault((m, n), []).append(gap)
            j = bisect.bisect_right(ts, dts[0]) - 1
            if j >= 0 and per_model[j].get(m) and r.get("tpot") is not None:
                req_bucket.setdefault((m, per_model[j][m]), []).append(r["tpot"])

        tot_time = sum(sum(v) for v in dec.values())
        t3 = sum(sum(v) for k, v in dec.items() if k[1] >= 3)
        rows.append({"workload": kind, "rate": rate, "seed": seed,
                     "run_span_s": round(span, 1),
                     "run_3plus1_s": round(run31, 1),
                     "run_3plus1_pct": round(100 * run31 / span, 1),
                     "decode_time_s": round(tot_time, 1),
                     "decode_time_3model_s": round(t3, 1),
                     "decode_exposure_pct": round(100 * t3 / tot_time, 1) if tot_time else None,
                     "episodes": sum(1 for i in range(1, len(shape))
                                     if shape[i] == "1+3" and shape[i - 1] != "1+3")})
        for m in NAME:
            mt = sum(sum(v) for k, v in dec.items() if k[0] == m)
            m3 = sum(sum(v) for k, v in dec.items() if k[0] == m and k[1] >= 3)
            if not mt:
                continue
            model_rows.append({"workload": kind, "rate": rate, "seed": seed,
                               "model": m, "model_name": NAME[m],
                               "decode_time_s": round(mt, 1),
                               "decode_time_3model_s": round(m3, 1),
                               "decode_exposure_pct": round(100 * m3 / mt, 1)})

        # --- A2 + A3: same-run, same-model counterfactual ------------------
        obs, cf_lo, cf_ce, cf_hi, n_used, n_unavail = [], [], [], [], 0, 0
        for (m, n), tps in req_bucket.items():
            two = req_bucket.get((m, 2))
            if n >= 3:
                if not two:
                    n_unavail += len(tps)
                    obs += tps
                    cf_lo += tps; cf_ce += tps; cf_hi += tps
                    continue
                n_used += len(tps)
                obs += tps
                # replace each exposed request's TPOT with the same-run,
                # same-model empirical 2-model-GPU distribution quantiles
                cf_ce += [pct(two, 50)] * len(tps)
                cf_lo += [pct(two, 75)] * len(tps)   # conservative
                cf_hi += [pct(two, 25)] * len(tps)   # optimistic
            else:
                obs += tps; cf_lo += tps; cf_ce += tps; cf_hi += tps
        cf_rows.append({"workload": kind, "rate": rate, "seed": seed,
                        "n_requests": len(obs), "n_exposed_replaced": n_used,
                        "n_exposed_unavailable": n_unavail,
                        "obs_tpot_p50": pct(obs, 50), "obs_tpot_mean": statistics.fmean(obs),
                        "cf_central_tpot_mean": statistics.fmean(cf_ce),
                        "cf_low_tpot_mean": statistics.fmean(cf_lo),
                        "cf_high_tpot_mean": statistics.fmean(cf_hi),
                        "tpot_recovery_central_pct":
                            round(100 * (statistics.fmean(obs) - statistics.fmean(cf_ce))
                                  / statistics.fmean(obs), 2),
                        "tpot_recovery_low_pct":
                            round(100 * (statistics.fmean(obs) - statistics.fmean(cf_lo))
                                  / statistics.fmean(obs), 2),
                        "tpot_recovery_high_pct":
                            round(100 * (statistics.fmean(obs) - statistics.fmean(cf_hi))
                                  / statistics.fmean(obs), 2)})
    OUT.mkdir(parents=True, exist_ok=True)
    for name, data in (("exposure_run", rows), ("exposure_by_model", model_rows),
                       ("counterfactual_tpot", cf_rows)):
        (OUT / f"{name}.json").write_text(json.dumps(data, indent=1))
        with (OUT / f"{name}.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(data[0])); w.writeheader(); w.writerows(data)

    print("A1. Exposure -- wall-clock vs decode-time (these are NOT the same)\n")
    print(f"{'cond':>12}{'span_s':>8}{'3+1_s':>8}{'run%':>7}{'decode_s':>10}{'3model_s':>10}{'decode%':>9}{'eps':>5}")
    for r in rows:
        print(f"{r['workload']+str(r['rate'])+'s'+str(r['seed']):>12}{r['run_span_s']:>8.0f}"
              f"{r['run_3plus1_s']:>8.0f}{r['run_3plus1_pct']:>7.1f}{r['decode_time_s']:>10.0f}"
              f"{r['decode_time_3model_s']:>10.0f}{r['decode_exposure_pct']:>9.1f}{r['episodes']:>5}")
    print("\n   per-model decode exposure to 3-model co-residency (%)")
    print(f"{'cond':>12}" + "".join(f"{NAME[m]:>14}" for m in ("model_3","model_4","model_5","model_6")))
    for kind, rate, seed in CONDS:
        line = f"{kind+str(rate)+'s'+str(seed):>12}"
        for m in ("model_3", "model_4", "model_5", "model_6"):
            v = next((x["decode_exposure_pct"] for x in model_rows
                      if (x["workload"], x["rate"], x["seed"], x["model"]) == (kind, rate, seed, m)), None)
            line += f"{v:>14.1f}" if v is not None else f"{'--':>14}"
        print(line)
    print("\nA3. Counterfactual bound (descriptive, NOT causal)\n")
    print(f"{'cond':>12}{'obs_mean':>10}{'cf_low':>9}{'cf_central':>11}{'cf_high':>9}"
          f"{'recov_low%':>11}{'recov_ce%':>10}{'recov_hi%':>10}{'unavail':>8}")
    for r in cf_rows:
        print(f"{r['workload']+str(r['rate'])+'s'+str(r['seed']):>12}{r['obs_tpot_mean']:>10.4f}"
              f"{r['cf_low_tpot_mean']:>9.4f}{r['cf_central_tpot_mean']:>11.4f}{r['cf_high_tpot_mean']:>9.4f}"
              f"{r['tpot_recovery_low_pct']:>11.2f}{r['tpot_recovery_central_pct']:>10.2f}"
              f"{r['tpot_recovery_high_pct']:>10.2f}{r['n_exposed_unavailable']:>8}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
