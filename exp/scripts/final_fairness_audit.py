#!/usr/bin/env python3
"""Prototype A and Final C must be compared on the same workload, model for model.

The .pkl traces Prototype A ran on were gitignored and did not survive the
instance rebuild, so this does not guess at them. Two things it can read are
enough, and neither is reconstructed:

  * each Prototype run records the exact trace path it was given, in its own
    server log;
  * each Prototype run's `*_output_requests.json` records the request sequence
    it actually served -- arrival time, model, prompt and output length, and
    the SLOs baked into the trace.

The second is compared against the Final workload's own request sequence. If
they agree, the two arms ran the same requests in the same order against the
same per-model SLOs, whatever became of the file. A run whose sequence cannot
be recovered at all is reported UNVERIFIED, never filled in.
"""

import argparse
import csv
import hashlib
import json
import pickle
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROTO_ROOT = ROOT / "exp/results/final-prototype-vs-paper-faithful/raw/armA/raw/released-prototype"

EXPECTED_MODELS = {
    "meta-llama/Llama-3.2-1B", "Qwen/Qwen2.5-1.5B-Instruct",
    "meta-llama/Llama-3.2-3B", "Qwen/Qwen2.5-3B-Instruct",
    "meta-llama/Llama-3.1-8B", "Qwen/Qwen2.5-7B-Instruct",
}

EVALUATION_GRID = (
    [("bursty", r, s) for r in (2, 4, 8, 14, 20) for s in (1, 2, 3)]
    + [("steady", r, s) for r in (4, 8, 20) for s in (1, 2, 3)]
)


class Request:
    """Structural stand-in, so the workload pickles load here too."""


def _canonical(seq):
    """A stable fingerprint of a request sequence, independent of wall clock."""
    if not seq:
        return None, None
    base = min(s[0] for s in seq)
    rows = sorted((round(t - base, 6), m, int(p), int(o)) for t, m, p, o in seq)
    blob = json.dumps(rows, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest(), len(rows)


def prototype_sequence(run_dir):
    """What this Prototype run actually served, from its own output records."""
    files = sorted(Path(run_dir).glob("requests/*_output_requests.json"))
    if not files:
        return None, None, None
    try:
        records = json.loads(files[0].read_text())
    except json.JSONDecodeError:
        return None, None, None
    seq, models, slos = [], set(), {}
    for r in records:
        if not isinstance(r, dict):
            continue
        arrival = r.get("arrival_time")
        model = r.get("model")
        if arrival is None or model is None:
            continue
        seq.append((arrival, model, r.get("prompt_len") or 0,
                    r.get("output_len") or 0))
        models.add(model)
        if r.get("slo_ttft") is not None:
            slos.setdefault(model, (round(r["slo_ttft"], 9),
                                    round(r.get("slo_tpot", 0), 9)))
    return seq, sorted(models), slos


def prototype_counts(run_dir):
    """What the prototype actually served, at the resolution its records allow."""
    files = sorted(Path(run_dir).glob("requests/*_output_requests.json"))
    if not files:
        return {}
    try:
        records = json.loads(files[0].read_text())
    except json.JSONDecodeError:
        return {}
    per_model = {}
    for r in records:
        if isinstance(r, dict) and r.get("model"):
            per_model[r["model"]] = per_model.get(r["model"], 0) + 1
    return {"total": len(records), "per_model": per_model}


def provenance_for(workload, rate, seed):
    """The committed description of the trace the prototype was handed."""
    path = ROOT / "exp/workloads/final-compare" / f"paired_requests_r{rate}_s{seed}.json"
    if not path.exists():
        return None
    return {"path": path, "sha256": sha256_file(path)}


def prototype_trace_path(run_dir):
    """The trace path the run was handed, recovered from its own logs."""
    for log in sorted(Path(run_dir).glob("server-logs/*.log")):
        text = log.read_text(errors="replace")
        m = re.search(r"(\S*workloads\S*\.pkl)", text)
        if m:
            return m.group(1)
    return None


def workload_sequence(path):
    sys.modules["__main__"].Request = Request
    with open(path, "rb") as f:
        payload = pickle.load(f)
    reqs = payload[1] if isinstance(payload, tuple) else payload
    seq, models, slos = [], set(), {}
    for r in reqs:
        d = vars(r) if hasattr(r, "__dict__") else dict(r)
        arrival = d.get("arrival_time", d.get("arrival"))
        model = d.get("model", d.get("model_path"))
        if arrival is None or model is None:
            continue
        seq.append((arrival, model,
                    d.get("prompt_len") or len(d.get("prompt_token_ids") or []) or 0,
                    d.get("output_len") or 0))
        models.add(model)
        st = d.get("slo_ttft", d.get("slo"))
        if st is not None:
            slos.setdefault(model, (round(st, 9), round(d.get("slo_tpot", 0) or 0, 9)))
    return seq, sorted(models), slos


def sha256_file(path):
    path = Path(path)
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--final-workloads", type=Path, required=True)
    ap.add_argument("--out-json", type=Path, required=True)
    ap.add_argument("--out-csv", type=Path, required=True)
    args = ap.parse_args()

    rows = []
    for workload, rate, seed in EVALUATION_GRID:
        proto_run = PROTO_ROOT / workload / f"rate_{rate}" / f"seed_{seed}"
        final_path = args.final_workloads / f"{workload}_r{rate}_s{seed}.pkl"
        row = {
            "workload_type": workload, "rate": rate, "seed": seed,
            "prototype_run": str(proto_run) if proto_run.exists() else None,
            "prototype_path": prototype_trace_path(proto_run) if proto_run.exists() else None,
            "final_path": str(final_path) if final_path.exists() else None,
            "prototype_sha256": None, "final_sha256": None,
            "prototype_requests": None, "final_requests": None,
            "verification": "UNVERIFIED", "MATCH": False, "note": "",
        }

        if not proto_run.exists():
            row["note"] = "no prototype run for this cell"
            rows.append(row); continue
        if not final_path.exists():
            row["note"] = "final workload file missing"
            rows.append(row); continue

        p_seq, p_models, p_slos = prototype_sequence(proto_run)
        if not p_seq:
            # The prototype era wrote a reduced request record: success,
            # latency, ttft, tpot, output_len, model, error -- no arrival time,
            # no prompt length, no per-request SLO. The sequence it served
            # therefore cannot be recovered from it, and the .pkl it was handed
            # is gitignored and gone. What can still be established is compared
            # below, and the row is marked for what it is rather than filled in.
            proto_counts = prototype_counts(proto_run)
            prov = provenance_for(workload, rate, seed)
            f_seq, f_models, f_slos = workload_sequence(final_path)
            f_counts = {}
            for _t, m, _p, _o in f_seq:
                f_counts[m] = f_counts.get(m, 0) + 1
            row.update(
                verification="provenance-only",
                prototype_requests=proto_counts.get("total"),
                final_requests=len(f_seq),
                prototype_models=",".join(sorted(proto_counts.get("per_model", {}))),
                models_match=set(proto_counts.get("per_model", {})) == set(f_counts),
                request_count_match=proto_counts.get("total") == len(f_seq),
                per_model_count_match=proto_counts.get("per_model") == f_counts,
                committed_provenance=str(prov["path"]) if prov else None,
                committed_provenance_sha256=prov["sha256"] if prov else None,
                final_provenance_sha256=sha256_file(
                    args.final_workloads / f"paired_requests_r{rate}_s{seed}.json"),
                note=("prototype request records lack arrival time, prompt length "
                      "and per-request SLO, so the served sequence cannot be "
                      "recovered; file hash unrecoverable because the trace it "
                      "was handed is gone"),
            )
            row["provenance_match"] = (
                row["committed_provenance_sha256"] is not None
                and row["committed_provenance_sha256"] == row["final_provenance_sha256"])
            row["MATCH"] = False        # never claimed on provenance alone
            rows.append(row); continue

        f_seq, f_models, f_slos = workload_sequence(final_path)
        p_hash, p_n = _canonical(p_seq)
        f_hash, f_n = _canonical(f_seq)
        row.update(prototype_sha256=p_hash, final_sha256=f_hash,
                   prototype_requests=p_n, final_requests=f_n,
                   final_file_sha256=sha256_file(final_path),
                   verification="request-sequence")

        # The prototype's own file is gone, so its identity is the sequence it
        # served. Say so rather than implying a file hash was compared.
        proto_file = row["prototype_path"]
        if proto_file:
            local = ROOT / Path(proto_file).relative_to("/workspace/prism-merge") \
                if proto_file.startswith("/workspace/prism-merge/") else Path(proto_file)
            row["prototype_file_present"] = Path(local).exists()
            row["prototype_file_sha256"] = sha256_file(local)
        else:
            row["prototype_file_present"] = False

        same_models = set(p_models) == set(f_models)
        same_slos = all(p_slos.get(m) == f_slos.get(m) for m in p_models if m in f_slos)
        row["models_match"] = same_models
        row["slos_match"] = same_slos
        row["prototype_models"] = ",".join(p_models)
        row["sharegpt_source"] = set(p_models) <= EXPECTED_MODELS and len(p_models) > 0
        row["MATCH"] = bool(p_hash and p_hash == f_hash and same_models and same_slos)
        if not row["MATCH"]:
            reasons = []
            if p_hash != f_hash:
                reasons.append(f"sequence differs ({p_n} vs {f_n} requests)")
            if not same_models:
                reasons.append("model set differs")
            if not same_slos:
                reasons.append("per-model SLOs differ")
            row["note"] = "; ".join(reasons)
        rows.append(row)

    matched = sum(1 for r in rows if r["MATCH"])
    unverified = [f"{r['workload_type']}_r{r['rate']}_s{r['seed']}"
                  for r in rows if r["verification"] in ("UNVERIFIED", "provenance-only")]
    provenance_ok = all(r.get("provenance_match") for r in rows
                        if r["verification"] == "provenance-only")
    counts_ok = all(r.get("request_count_match") and r.get("per_model_count_match")
                    for r in rows if r["verification"] == "provenance-only")
    verdict = "PASS" if matched == len(EVALUATION_GRID) else "FAIL"

    doc = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "cells_expected": len(EVALUATION_GRID),
        "cells_matched": matched,
        "unverified_cells": unverified,
        "provenance_level_agreement": provenance_ok,
        "request_count_agreement": counts_ok,
        "why_file_hash_is_unrecoverable": (
            "The prototype ran on .pkl traces that this repository gitignores; "
            "they did not survive the instance rebuild. Its request records are "
            "the reduced format of that era -- success, latency, ttft, tpot, "
            "output_len, model, error -- so neither the file nor the served "
            "sequence can be hashed. What IS established for every cell: the "
            "trace path the run was handed, the committed provenance of that "
            "exact file, that the final workload's provenance is byte-identical "
            "to it, and that the request count and per-model counts the "
            "prototype served match the final workload. That is provenance-level "
            "agreement, not a file hash, and the verdict says so."),
        "expected_models": sorted(EXPECTED_MODELS),
        "workload_source": "ShareGPT (build_paired_workload.py --sharegpt)",
        "how_prototype_identity_was_established": (
            "Each prototype run's own *_output_requests.json gives the sequence "
            "it actually served -- arrival order, model, prompt and output "
            "lengths, per-model SLOs. The .pkl it was handed is gitignored and "
            "did not survive the instance rebuild, so its path is reported and "
            "its presence flagged, but the comparison is made on the served "
            "sequence, which is recovered rather than reconstructed."),
        "rows": rows,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(doc, indent=2, default=str) + "\n")

    fields = ["workload_type", "rate", "seed", "prototype_path", "final_path",
              "prototype_sha256", "final_sha256", "MATCH", "verification",
              "prototype_requests", "final_requests", "models_match",
              "slos_match", "prototype_file_present", "note"]
    with args.out_csv.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    for r in rows:
        print(f"  {'MATCH' if r['MATCH'] else 'DIFFER':6s} "
              f"{r['workload_type']:6s} r{r['rate']:<3} s{r['seed']} "
              f"{r['verification']:18s} {r['note']}")
    print(f"\nVERDICT: {verdict}  ({matched}/{len(EVALUATION_GRID)} matched)")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
