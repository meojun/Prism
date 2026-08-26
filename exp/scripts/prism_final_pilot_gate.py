#!/usr/bin/env python3
"""Seed-9 diagnostic pilot gate (section 33).

The ONLY performance-based early stop in the pipeline, and it applies to the
seed-9 pilot alone -- never to the final seed7/8 or seed5/6 matrices.

    R = Prism Joint-SLO goodput / Prototype Joint-SLO goodput

    CASE A  R < 0.50          -> STOP  (hard)
    CASE B  0.50 <= R < 0.70  -> HALT for resource-management telemetry review
    CASE C  R >= 0.70         -> PASS, proceed to the full 20-run matrix

Threshold amendment: the original section 33 used 0.80 for CASE C. It was
lowered to 0.70 by explicit instruction BEFORE the pilot was run, so no pilot
result influenced it. Recorded in
exp/manifests/prism_final/PILOT_GATE_THRESHOLDS.json.

This gate applies to the seed-9 diagnostic pilot ONLY. It is never applied to
the final seed7/8 many-model matrix or the seed5/6 4-HET matrix, both of which
run to completion regardless of performance.

exit 0 = PASS, exit 2 = HALT for review (CASE B), exit 1 = hard STOP (CASE A).
"""
import json, sys
from pathlib import Path
R = Path("/workspace/prism-exp")
OUT = R / "exp/results/many-model-pilot/raw"
REP = R / "reports/prism/07_many_model_prism_favorable"


def numbers(arm):
    v = OUT / arm / "bursty/rate_16/seed_9/VERIFICATION.json"
    if not v.exists():
        return None
    return json.loads(v.read_text())["numbers"]


def main():
    proto, prism = numbers("prototype"), numbers("prism")
    if not proto or not prism:
        print("PILOT_GATE = STOP (missing pilot results)", file=sys.stderr)
        return 1
    pg, sg = proto["joint_slo_goodput_req_s"], prism["joint_slo_goodput_req_s"]
    ratio = sg / pg if pg else 0.0
    case = "A" if ratio < 0.50 else ("B" if ratio < 0.70 else "C")
    print(f"PROTOTYPE_GOODPUT = {pg:.4f}")
    print(f"PRISM_GOODPUT     = {sg:.4f}")
    print(f"PRISM_TO_PROTOTYPE_RATIO = {ratio:.4f}   -> CASE {case}")
    for lbl, n in (("prototype", proto), ("prism", prism)):
        print(f"  {lbl:>9}: attain={n['joint_slo_attainment']:.4f} "
              f"thr={n['throughput_req_s']:.3f} tpot_p99={n['tpot_p99_s']*1000:.1f}ms "
              f"ttft_p99={n['ttft_p99_s']:.2f}s migr={n['migrations_executed']} "
              f"completed={n['completed']}/{n['offered_requests']}")
    verdict = {"A": "STOP", "B": "HALT_FOR_TELEMETRY_REVIEW", "C": "PASS"}[case]
    summary = {"rate": 16, "seed": 9, "prototype_goodput": pg, "prism_goodput": sg,
               "ratio": ratio, "case": case,
               "thresholds": {"stop_below": 0.50, "review_below": 0.70,
                              "pass_at_or_above": 0.70},
               "PILOT_GATE": verdict,
               "applies_to": "seed-9 diagnostic pilot only; never to final "
                             "seed7/8 or seed5/6 matrices",
               "prototype": proto, "prism": prism}
    (R / "exp/analysis").mkdir(exist_ok=True)
    d = R / "exp/analysis/many_model_prism_favorable"; d.mkdir(parents=True, exist_ok=True)
    (d / "pilot_gate.json").write_text(json.dumps(summary, indent=1))
    if case == "C":
        print("PILOT_GATE = PASS  (R >= 0.70 -> proceeding to the full 20-run matrix)")
        return 0
    REP.mkdir(parents=True, exist_ok=True)
    (REP / "MANY_MODEL_PILOT_STOP_ANALYSIS.md").write_text(f"""# Many-model pilot — {verdict} (CASE {case})

The seed-9 diagnostic pilot did not clear the pre-declared gate. The full
many-model matrix and the final 4-HET matrix were **not** started.

Thresholds in force: STOP below 0.50, telemetry review below 0.70, pass at or
above 0.70. The 0.70 pass threshold replaced the original 0.80 by explicit
instruction **before the pilot ran**, so no pilot result influenced it.

| | Prototype | Prism |
|---|---:|---:|
| Joint-SLO goodput (req/s) | {pg:.4f} | {sg:.4f} |
| Joint-SLO attainment | {proto['joint_slo_attainment']:.4f} | {prism['joint_slo_attainment']:.4f} |
| throughput (req/s) | {proto['throughput_req_s']:.3f} | {prism['throughput_req_s']:.3f} |
| TPOT p99 (ms) | {proto['tpot_p99_s']*1000:.1f} | {prism['tpot_p99_s']*1000:.1f} |
| TTFT p99 (s) | {proto['ttft_p99_s']:.2f} | {prism['ttft_p99_s']:.2f} |
| migrations | {proto['migrations_executed']} | {prism['migrations_executed']} |

```
R = Prism / Prototype = {ratio:.4f}   -> CASE {case}
PILOT_GATE = {verdict}
```

{"**CASE B** — the ratio is not catastrophic, so the question is whether Prism shows a real resource-management advantage that simply has not translated into Joint-SLO goodput. Review the migration, KVPR, residency and KV-headroom telemetry before deciding." if case == "B" else "**CASE A** — the ratio is below 0.50 even in a deliberately favourable regime. This is a strong negative result and warrants forensic analysis before anything else runs."}

This is a **deliberately Prism-favorable** regime: six models, high
consolidation, 90/10 demand skew on a moving hot pair, 180 s phases chosen so
the 60 s estimator can observe, Algorithm 1 can respond, and a migration can be
amortised. Failing the gate *here* is a stronger negative result than the same
outcome would be in a neutral workload.

Per the protocol this STOP does **not** authorise retuning. `180 s`, `90/10`,
τ, window, SLO, the hot pairs and the rate list are all unchanged and must stay
unchanged until a diagnosis exists.

Pilot artefacts are preserved under `exp/results/many-model-pilot/`.
Machine-readable: `exp/analysis/many_model_prism_favorable/pilot_gate.json`.

**Next step is diagnosis, not adjustment.**
""")
    print(f"PILOT_GATE = {verdict} (CASE {case}) -- analysis written", file=sys.stderr)
    return 2 if case == "B" else 1


if __name__ == "__main__":
    sys.exit(main())
