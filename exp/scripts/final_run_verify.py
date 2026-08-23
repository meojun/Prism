#!/usr/bin/env python3
"""Per-run verification record: the checklist every pipeline run must satisfy.

Reads the run's own artifacts -- benchmark output, server logs, the Algorithm 2
interaction report and the client descriptor validation -- and writes one
VERIFICATION.json holding both the correctness gates and the reported numbers.

Gates (all must hold):
    rc == 0                          the harness returned success
    completed + aborted accounted    every offered request reached an end state
    stale dispatched sequences == 0
    Algorithm 2 order violations == 0
    ownership / identity mismatches == 0
    client descriptor failures == 0
    unexpected connection failures == 0

Numbers (recorded, never gated): throughput, TTFT mean/p99, TPOT mean/p99, E2E,
Joint-SLO attainment and goodput, migration count, migrated weight and KV bytes.

Exit status is 0 only when every gate holds.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from final_metrics import collect  # noqa: E402


def _load(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:                                  # noqa: BLE001
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    run = args.run

    m = collect(run)
    interaction = _load(run / "ALG2_INTERACTION.json")
    fd = _load(run / "CLIENT_FD_VALIDATION.json")
    checks = {c["check"]: c for c in interaction.get("checks", [])}

    def gate(name):
        c = checks.get(name)
        return None if c is None else bool(c["pass"])

    rc = (run / "pipeline.rc").read_text().strip() if (run / "pipeline.rc").exists() else None
    fd_failures = fd.get("fd_exhaustion")
    conn_failures = fd.get("connection_failures")

    gates = {
        "rc_zero": rc == "0",
        "all_requests_accounted": (m["accounted"] == m["offered_requests"]
                                   if m["offered_requests"] else False),
        "no_stale_dispatched_sequences": gate("no_stale_dispatched_sequences"),
        "no_alg2_order_violation": (m["alg2_order_violations"] == 0
                                    if m["alg2_order_violations"] is not None
                                    else gate("no_alg2_order_violation")),
        "no_ownership_identity_mismatch": gate("no_identity_mismatch"),
        "no_client_fd_failure": (fd_failures in (0, None)
                                 and not (run / "INVALID").exists()),
        "no_unexpected_connection_failure": conn_failures in (0, None),
    }
    failed = [k for k, v in gates.items() if v is False]
    # A gate whose evidence is missing is not a pass. It means the artifact the
    # gate reads was never produced, which is itself a reason to stop.
    missing = [k for k, v in gates.items() if v is None]

    record = {
        "label": args.label,
        "run": str(run),
        "rc": rc,
        "verdict": "PASS" if not failed and not missing else "FAIL",
        "failed_gates": failed,
        "gates_without_evidence": missing,
        "gates": gates,
        "numbers": {
            "offered_requests": m["offered_requests"],
            "completed": m["completed"],
            "aborted": m["aborted"],
            "client_errors": m["client_errors"],
            "throughput_req_s": m["achieved_throughput_req_s"],
            "ttft_mean_s": m["ttft_mean"], "ttft_p99_s": m["ttft_p99"],
            "tpot_mean_s": m["tpot_mean"], "tpot_p99_s": m["tpot_p99"],
            "e2e_mean_s": m["e2e_mean"], "e2e_p99_s": m["e2e_p99"],
            "joint_slo_attainment": m["joint_slo_attainment"],
            "joint_slo_goodput_req_s": m["goodput_req_s"],
            "migrations_executed": m["migrations_executed"],
            "migrated_weight_bytes": m["weight_bytes"],
            "migrated_kv_bytes": m["kv_bytes"],
            "alg2_order_violations": m["alg2_order_violations"],
        },
    }
    args.out.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({"verdict": record["verdict"], "failed": failed,
                      "missing": missing}))
    return 0 if record["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
