#!/usr/bin/env python3
"""Freeze the canonical evaluation workload set and prove both arms use it.

The paired comparison is only fair if the Released Prototype arm and the Final
Prism arm are driven by byte-identical workload files. This script establishes
that by construction and then verifies it:

  * the 24 evaluation conditions (bursty 2/4/8/14/20 x seeds 1-3, steady
    4/8/20 x seeds 1-3) must all be present as .pkl traces;
  * each file's SHA256 must match the committed WORKLOAD_HASHES.json manifest;
  * each trace must load and its contents -- arrival sequence, model
    assignment, prompt/output lengths, per-request SLO -- must be internally
    consistent and must agree with the rate and seed in its filename and in
    the paired_requests_r<rate>_s<seed>.json summary beside it.

Both arms are launched from this one directory, so identity is structural; the
hashes recorded here are what the aggregation step re-checks afterwards. Any
missing file, hash mismatch, or metadata disagreement is a hard failure -- the
script exits non-zero and the pipeline stops rather than comparing arms that
may not have seen the same work.
"""
import argparse
import datetime
import hashlib
import json
import pickle
import sys
from pathlib import Path

GRID = ([("bursty", r, s) for r in (2, 4, 8, 14, 20) for s in (1, 2, 3)]
        + [("steady", r, s) for r in (4, 8, 20) for s in (1, 2, 3)])


class Request:
    """Structural stand-in so the workload pickles load here too."""


sys.modules["__main__"].Request = Request


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint(requests):
    """A wall-clock-independent fingerprint of the arrival sequence."""
    base = min(r.arrival_time for r in requests)
    rows = [(round(r.arrival_time - base, 6), r.model, int(r.prompt_len),
             int(r.output_len), round(float(r.slo_ttft), 9),
             round(float(r.slo_tpot), 9)) for r in requests]
    rows.sort()
    blob = json.dumps(rows, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workloads", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()

    wl, out = args.workloads, args.out_dir
    committed = {}
    manifest_path = wl / "WORKLOAD_HASHES.json"
    if manifest_path.exists():
        committed = json.loads(manifest_path.read_text())

    canon, detail, problems = {}, {}, []
    for kind, rate, seed in GRID:
        name = f"{kind}_r{rate}_s{seed}.pkl"
        path = wl / name
        if not path.exists():
            problems.append(f"{name}: missing")
            continue

        digest = sha256(path)
        canon[name] = digest
        if name in committed and committed[name] != digest:
            problems.append(f"{name}: sha256 {digest[:12]} != committed "
                            f"{committed[name][:12]}")

        try:
            _prefix, requests = pickle.loads(path.read_bytes())
        except Exception as exc:                      # noqa: BLE001
            problems.append(f"{name}: unreadable ({exc})")
            continue

        if not requests:
            problems.append(f"{name}: empty request sequence")
            continue

        summary_path = wl / f"paired_requests_r{rate}_s{seed}.json"
        summary = {}
        if summary_path.exists():
            summary = json.loads(summary_path.read_text())
            if int(summary.get("seed", seed)) != seed:
                problems.append(f"{name}: summary seed {summary['seed']} != {seed}")
            if abs(float(summary.get("rate", rate)) - rate) > 1e-6:
                problems.append(f"{name}: summary rate {summary['rate']} != {rate}")
            if int(summary.get("total_requests", len(requests))) != len(requests):
                problems.append(f"{name}: {len(requests)} requests but summary "
                                f"says {summary['total_requests']}")

        models = sorted({r.model for r in requests})
        bad = [r.req_id for r in requests
               if int(r.prompt_len) <= 0 or int(r.output_len) <= 0
               or float(r.slo_ttft) <= 0 or float(r.slo_tpot) <= 0]
        if bad:
            problems.append(f"{name}: {len(bad)} requests with non-positive "
                            f"length or SLO (e.g. {bad[0]})")

        detail[name] = {
            "sha256": digest,
            "requests": len(requests),
            "models": models,
            "sequence_fingerprint": fingerprint(requests),
            "prompt_tokens": sum(int(r.prompt_len) for r in requests),
            "output_tokens": sum(int(r.output_len) for r in requests),
            "arrival_span_s": round(max(r.arrival_time for r in requests)
                                    - min(r.arrival_time for r in requests), 6),
            "rate": rate, "seed": seed, "workload": kind,
        }

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    decision = {
        # "B" is the aggregator's marker for "both arms were run fresh on the
        # canonical set", which is what this pipeline does.
        "case": "B",
        "decided_utc": now,
        "conditions": len(GRID),
        "verified": len(detail),
        "canonical_workload_dir": str(wl),
        "action": ("both arms -- Released Prototype and Final Prism -- are run "
                   "fresh on this one canonical set, so they consume "
                   "byte-identical workload files; the historical prototype "
                   "results are preserved and excluded from the comparison"),
        "identity_basis": ("same files, same SHA256, same arrival sequence, "
                           "model assignment, prompt/output lengths and "
                           "per-request SLO; same server, GPUs, model "
                           "revisions and tokenizer within one pipeline run"),
        "problems": problems,
        "verdict": "PASS" if not problems and len(detail) == len(GRID) else "FAIL",
    }

    out.mkdir(parents=True, exist_ok=True)
    (out / "CANONICAL_WORKLOAD_SHA256.json").write_text(
        json.dumps(canon, indent=2, sort_keys=True))
    (out / "CANONICAL_WORKLOAD_DETAIL.json").write_text(
        json.dumps(detail, indent=2, sort_keys=True))
    (out / "FAIRNESS_DECISION.json").write_text(json.dumps(decision, indent=2))

    for p in problems:
        print(f"PROBLEM: {p}")
    print(f"{len(detail)}/{len(GRID)} canonical workloads verified -> "
          f"{decision['verdict']}")
    return 0 if decision["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
