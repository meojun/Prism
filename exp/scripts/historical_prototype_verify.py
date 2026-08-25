#!/usr/bin/env python3
"""Did the historical Released Prototype arm run the canonical workloads?

The committed `raw/armA/summary.csv` is stale -- it describes an earlier
300 s study, while the raw runs beneath it look like the 420 s canonical set.
So nothing here reads that summary. Every claim is derived from the run's own
artifacts and checked against the canonical trace on disk.

Per condition it compares, against the canonical .pkl:

  workload kind, rate, seed, request count, the per-request model assignment,
  prompt length, requested output length and both SLOs -- as an order
  independent fingerprint over the whole request population, which is the same
  construction the canonical freeze uses -- plus the arrival sequence and
  relative timing when the run's records carry an arrival field.

Serving-stack provenance (model ids/revisions, dtype, GPUs, CUDA/torch/sglang)
is read from the run's own logs where it is recorded, and reported as absent
where it is not. Nothing is inferred: a field that cannot be read is missing,
not assumed equal.

Verdicts:
  VERIFIED           every workload-identity check passed and none is missing
  PARTIALLY_VERIFIED every check that could be made passed, but some evidence
                     is absent -- the missing items are named
  NOT_VERIFIED       a check failed, or the run is unusable
"""
import argparse
import hashlib
import json
import pickle
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

DEFAULT_HIST = ROOT / ("exp/results/final-prototype-vs-paper-faithful/raw/armA"
                       "/raw/released-prototype")
CONDITIONS = [("bursty", 2), ("bursty", 4), ("bursty", 8), ("bursty", 14),
              ("bursty", 20), ("steady", 4), ("steady", 8), ("steady", 20)]
SEEDS = (1, 2, 3)


def sha_rows(rows):
    rows = sorted(rows)
    return hashlib.sha256(
        json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def canonical_rows(pkl):
    """(model, prompt_len, output_len, slo_ttft, slo_tpot) for every request."""
    _prefix, reqs = pickle.loads(Path(pkl).read_bytes())
    return [(r.model, int(r.prompt_len), int(r.output_len),
             round(float(r.slo_ttft), 9), round(float(r.slo_tpot), 9))
            for r in reqs]


FIELD_ALIASES = {
    "model": ("model", "model_name", "model_id"),
    "prompt_len": ("prompt_len", "prompt_tokens", "input_len", "prompt_length"),
    "output_len": ("output_len", "output_tokens", "gen_len", "output_length"),
    "slo_ttft": ("slo_ttft",),
    "slo_tpot": ("slo_tpot",),
    "arrival": ("arrival_time", "arrival", "start_time", "send_time"),
}


def pick(rec, key):
    for name in FIELD_ALIASES[key]:
        if name in rec and rec[name] is not None:
            return rec[name], name
    return None, None


def run_records(run):
    for f in sorted((run / "requests").glob("*_output_requests.json")):
        try:
            data = json.loads(f.read_text(errors="replace"))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            data = data.get("requests", [])
        if isinstance(data, list) and data:
            return data, f.name
    return [], None


def read_head(path, n=40):
    try:
        with open(path, errors="replace") as f:
            return [next(f) for _ in range(n)]
    except (OSError, StopIteration) as e:
        if isinstance(e, StopIteration):
            try:
                return Path(path).read_text(errors="replace").splitlines()
            except OSError:
                return []
        return []


def stack_provenance(run):
    """What the run's own logs record about the serving stack. No inference."""
    out, missing = {}, []
    bench = read_head(run / "server-logs/bench.log", 4)
    m = None
    for line in bench:
        m = m or re.search(r"Real trace file:\s*(\S+)", line)
        s = re.search(r"\[PRISM_DIRECT\]\s+(\d+)\s+requests,\s+span\s+([\d.]+)s", line)
        if s:
            out["bench_reported_requests"] = int(s.group(1))
            out["bench_reported_span_s"] = float(s.group(2))
    if m:
        out["trace_path_used"] = m.group(1)
        out["trace_basename"] = Path(m.group(1)).name
    else:
        missing.append("trace path (bench.log)")

    stdout = run / "server-logs/stdout.log"
    text = ""
    try:
        # Bounded read: provenance is printed at startup, not throughout.
        with open(stdout, errors="replace") as f:
            text = f.read(400_000)
    except OSError:
        missing.append("server stdout.log")
    if text:
        for key, pat in (("torch", r"torch[ =]+([0-9]+\.[0-9]+\.[0-9]+\+?\w*)"),
                         ("cuda", r"CUDA[ =]+([0-9]+\.[0-9]+)"),
                         ("sglang", r"sglang[ =]+([0-9]+\.[0-9]+\.[0-9.a-z]+)")):
            mm = re.search(pat, text, re.I)
            if mm:
                out[key] = mm.group(1)
            else:
                missing.append(f"{key} version")
        revs = sorted(set(re.findall(r"snapshots/([0-9a-f]{40})", text)))
        if revs:
            out["model_revisions_seen"] = revs
        else:
            missing.append("model revisions (no snapshot paths in stdout.log)")
        ids = sorted(set(re.findall(
            r"((?:meta-llama|Qwen)/[A-Za-z0-9._-]+)", text)))
        if ids:
            out["model_ids_seen"] = ids
        else:
            missing.append("model ids")
        dt = sorted(set(re.findall(r"\b(bfloat16|float16|float32)\b", text)))
        if dt:
            out["dtype_seen"] = dt
        else:
            missing.append("dtype/precision")
    cmd = run / "SERVER_COMMAND.txt"
    if cmd.is_file():
        out["server_command"] = cmd.read_text(errors="replace").strip()[:2000]
    else:
        missing.append("SERVER_COMMAND.txt")
    return out, missing


def verify_condition(wl, rate, seed, hist_base, workloads, canon_detail):
    run = hist_base / wl / f"rate_{rate}" / f"seed_{seed}"
    rec = {"condition": f"{wl}{rate}", "workload": wl, "rate": rate,
           "seed": seed, "run_dir": str(run), "checks": [], "missing": []}

    def chk(name, ok, detail):
        rec["checks"].append({"check": name, "pass": bool(ok),
                              "detail": detail})
        return ok

    if not run.is_dir():
        rec["verdict"] = "NOT_VERIFIED"
        rec["reason"] = "no historical run directory"
        return rec

    fname = f"{wl}_r{rate}_s{seed}.pkl"
    pkl = workloads / fname
    meta = canon_detail.get(fname)
    if not pkl.is_file() or not meta:
        rec["verdict"] = "NOT_VERIFIED"
        rec["reason"] = f"canonical workload {fname} unavailable to compare against"
        return rec

    records, src = run_records(run)
    if not records:
        rec["verdict"] = "NOT_VERIFIED"
        rec["reason"] = "no per-request records in the historical run"
        return rec
    rec["per_request_file"] = src

    # --- request count -----------------------------------------------------
    chk("request count matches the canonical trace",
        len(records) == meta["requests"],
        f"{len(records)} vs {meta['requests']}")

    # --- which per-request fields this dump actually carries ---------------
    probe = records[0]
    have = {k: pick(probe, k)[1] for k in FIELD_ALIASES}
    rec["fields_present"] = {k: v for k, v in have.items() if v}
    for k in ("model", "prompt_len", "output_len", "slo_ttft", "slo_tpot"):
        if not have[k]:
            rec["missing"].append(f"per-request {k}")

    # --- model assignment ---------------------------------------------------
    if have["model"]:
        got = Counter(pick(r, "model")[0] for r in records)
        chk("model set matches", sorted(got) == sorted(meta["models"]),
            f"{sorted(got)} vs {sorted(meta['models'])}")
        want_mix = Counter(t[0] for t in canonical_rows(pkl))
        chk("per-model request counts match", got == want_mix,
            f"{dict(sorted(got.items()))} vs {dict(sorted(want_mix.items()))}")

    # --- the population fingerprint: model + lengths + both SLOs -----------
    if all(have[k] for k in ("model", "prompt_len", "output_len",
                             "slo_ttft", "slo_tpot")):
        got_rows = [(pick(r, "model")[0], int(pick(r, "prompt_len")[0]),
                     int(pick(r, "output_len")[0]),
                     round(float(pick(r, "slo_ttft")[0]), 9),
                     round(float(pick(r, "slo_tpot")[0]), 9))
                    for r in records]
        want = canonical_rows(pkl)
        a, b = sha_rows(got_rows), sha_rows(want)
        rec["population_fingerprint_run"] = a
        rec["population_fingerprint_canonical"] = b
        chk("request population fingerprint matches "
            "(model + prompt_len + output_len + both SLOs, every request)",
            a == b, f"{a[:16]} vs {b[:16]}")

    # --- arrival sequence / relative timing --------------------------------
    if have["arrival"]:
        arr = sorted(float(pick(r, "arrival")[0]) for r in records)
        span = round(arr[-1] - arr[0], 3)
        chk("arrival span matches the canonical trace",
            abs(span - meta["arrival_span_s"]) < 1.0,
            f"{span}s vs {meta['arrival_span_s']}s")
    else:
        rec["missing"].append("per-request arrival timing")

    # --- what the run itself says it ran -----------------------------------
    prov, prov_missing = stack_provenance(run)
    rec["provenance"] = prov
    rec["missing"].extend(prov_missing)
    if "trace_basename" in prov:
        chk("the trace the run loaded is named for this condition",
            prov["trace_basename"] == fname,
            f"{prov['trace_basename']} vs {fname}")
    if "bench_reported_requests" in prov:
        chk("the client's own request count matches the canonical trace",
            prov["bench_reported_requests"] == meta["requests"],
            f"{prov['bench_reported_requests']} vs {meta['requests']}")
    if "bench_reported_span_s" in prov:
        chk("the client's own arrival span matches the canonical trace",
            abs(prov["bench_reported_span_s"] - meta["arrival_span_s"]) < 1.0,
            f"{prov['bench_reported_span_s']}s vs {meta['arrival_span_s']}s")

    failed = [c["check"] for c in rec["checks"] if not c["pass"]]
    rec["failed_checks"] = failed
    if failed:
        rec["verdict"] = "NOT_VERIFIED"
        rec["reason"] = "; ".join(failed)
    elif rec["missing"]:
        rec["verdict"] = "PARTIALLY_VERIFIED"
        rec["reason"] = ("workload identity confirmed by the checks that could "
                         "be made; absent evidence: " + ", ".join(sorted(set(rec["missing"]))))
    else:
        rec["verdict"] = "VERIFIED"
        rec["reason"] = "every workload-identity and provenance check passed"
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--historical", type=Path, default=DEFAULT_HIST)
    ap.add_argument("--workloads", type=Path,
                    default=ROOT / "exp/workloads/final-evaluation")
    ap.add_argument("--manifest", type=Path,
                    default=ROOT / "exp/FINAL_BASELINE_MANIFEST.json")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "exp/results/final-evaluation/06-aggregate"
                                   "/HISTORICAL_PROTOTYPE_PROVENANCE.json")
    args = ap.parse_args()

    canon_detail = json.loads(args.manifest.read_text())["workloads"]["detail"]
    rows = [verify_condition(wl, rate, seed, args.historical, args.workloads,
                             canon_detail)
            for (wl, rate) in CONDITIONS for seed in SEEDS]

    tally = Counter(r["verdict"] for r in rows)
    out = {
        "what_this_is": "provenance of the historical Released Prototype arm, "
                        "derived from each run's own artifacts and checked "
                        "against the canonical traces; the stale armA "
                        "summary.csv is deliberately not read",
        "historical_arm": str(args.historical),
        "conditions": rows,
        "tally": dict(tally),
        "verified": tally["VERIFIED"],
        "partially_verified": tally["PARTIALLY_VERIFIED"],
        "not_verified": tally["NOT_VERIFIED"],
        "comparable": [f"{r['workload']}_r{r['rate']}_s{r['seed']}" for r in rows
                       if r["verdict"] in ("VERIFIED", "PARTIALLY_VERIFIED")],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, default=str))
    for r in rows:
        print(f"  {r['verdict']:19s} {r['workload']:6s} r{r['rate']:<3d} "
              f"s{r['seed']}  {r['reason'][:90]}")
    print(f"\nVERIFIED {tally['VERIFIED']} / PARTIALLY {tally['PARTIALLY_VERIFIED']} "
          f"/ NOT {tally['NOT_VERIFIED']}  -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
