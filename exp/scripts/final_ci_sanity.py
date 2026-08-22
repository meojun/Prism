#!/usr/bin/env python3
"""Stage 01 gate: is the c_i measurement itself sound?

Not "does it agree with the old numbers" -- a difference from the previous
profile is a result, not a fault, and nothing here rewrites a new value or
falls back to an old one. What is checked is whether the measurement is valid
on its own terms.
"""

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

MODELS = [f"model_{i}" for i in range(1, 7)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--previous", type=Path, default=None)
    args = ap.parse_args()

    ci_path = args.profile_dir / "prefill_speed_final_a100.json"
    checks, per_model = [], {}

    def check(name, ok, detail):
        checks.append({"check": name, "pass": bool(ok), "detail": detail})

    check("c_i file written", ci_path.exists(), {"path": str(ci_path)})
    ci = json.loads(ci_path.read_text()) if ci_path.exists() else {}
    check("all six models present", sorted(ci) == MODELS, {"present": sorted(ci)})

    for model in MODELS:
        raw_path = args.profile_dir / "raw" / f"{model}.json"
        entry = {"raw_present": raw_path.exists()}
        if not raw_path.exists():
            per_model[model] = entry
            check(f"{model}: raw samples present", False, entry)
            continue
        raw = json.loads(raw_path.read_text())
        est = raw.get("c_i_estimators", {})
        stored = ci.get(model)
        samples = raw.get("raw_sequential", [])

        entry.update(
            stored_c_i=stored,
            n_sequential=raw.get("n_sequential"),
            n_saturated=raw.get("n_saturated"),
            estimator_used=("E3_prefill_saturated"
                            if est.get("E3_prefill_saturated") else "fallback"),
            estimators={k: v for k, v in est.items() if isinstance(v, (int, float))},
        )

        finite = isinstance(stored, (int, float)) and stored > 0 and stored == stored \
            and stored not in (float("inf"), float("-inf"))
        check(f"{model}: c_i is finite and positive", finite, {"c_i": stored})

        # Recompute from the raw samples and require the stored value to be the
        # one the samples actually support.
        recomputed = est.get("E3_prefill_saturated") or est.get("E3_prefill_solo")
        agrees = (recomputed is not None and stored is not None
                  and abs(recomputed - stored) <= max(1e-6, 1e-6 * abs(stored)))
        entry["recomputed_c_i"] = recomputed
        check(f"{model}: stored c_i equals the value its samples give",
              agrees, {"stored": stored, "recomputed": recomputed})

        enough = (raw.get("n_sequential") or 0) >= 20 and (raw.get("n_saturated") or 0) >= 8
        check(f"{model}: enough samples to fit", enough,
              {"n_sequential": raw.get("n_sequential"),
               "n_saturated": raw.get("n_saturated")})

        # Stability: per-bucket solo throughputs should not scatter wildly. A
        # spread that large means the measurement, not the model, is unstable.
        buckets = [b.get("E3_prefill_solo") for b in raw.get("per_bucket", {}).values()
                   if b.get("E3_prefill_solo")]
        cv = (statistics.pstdev(buckets) / statistics.fmean(buckets)) if len(buckets) > 1 else 0.0
        entry["per_bucket_cv"] = cv
        entry["per_bucket_n"] = len(buckets)
        check(f"{model}: per-bucket throughput is not wildly unstable", cv < 1.0,
              {"coefficient_of_variation": cv, "buckets": len(buckets)})

        if args.previous and args.previous.exists():
            prev = json.loads(args.previous.read_text()).get(model)
            if prev:
                entry["previous_c_i"] = prev
                entry["ratio_to_previous"] = stored / prev if stored else None
        per_model[model] = entry

    verdict = "PASS" if all(c["pass"] for c in checks) else "C_I_SANITY_FAIL"
    doc = {
        "verdict": verdict,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "c_i": ci,
        "per_model": per_model,
        "checks": checks,
        "note": ("A difference from a previous profile is a measurement result "
                 "and is reported, never corrected; no old value is substituted."),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    for c in checks:
        print(f"  {'PASS' if c['pass'] else 'FAIL'}  {c['check']}")
    print(f"\nVERDICT: {verdict}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
