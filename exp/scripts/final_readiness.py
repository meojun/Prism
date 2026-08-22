#!/usr/bin/env python3
"""Stage 3: everything must be frozen and no correctness blocker may remain."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()
    out = args.out_dir

    checks = []

    def check(name, ok, detail):
        checks.append({"check": name, "pass": bool(ok), "detail": detail})

    env_path = out / "FINAL_ENVIRONMENT.json"
    env = json.loads(env_path.read_text()) if env_path.exists() else {}
    check("environment recorded", bool(env), {"file": str(env_path)})
    check("runtime unchanged since the freeze",
          env.get("runtime_freeze", {}).get("runtime_unchanged_since_freeze"),
          env.get("runtime_freeze"))
    check("experiment repository clean at preflight",
          env.get("freeze", {}).get("git_clean"),
          {"dirty": env.get("freeze", {}).get("git_dirty_files")})

    ci = out / "01-ci-profile/prefill_speed_final_a100.json"
    ci_data = json.loads(ci.read_text()) if ci.exists() else {}
    check("c_i frozen for all six models", len(ci_data) == 6, ci_data)

    tau_path = out / "02-tau-calibration/FROZEN_TAU.json"
    tau = json.loads(tau_path.read_text()) if tau_path.exists() else {}
    check("tau frozen", "tau" in tau,
          {k: tau.get(k) for k in ("tau", "tau_label", "calibration_seeds")})
    check("tau calibrated on held-out seeds only",
          set(tau.get("calibration_seeds", [])).isdisjoint({"1", "2", "3"}),
          {"calibration_seeds": tau.get("calibration_seeds")})
    check("tau was selected against the frozen c_i",
          tau.get("c_i") == ci_data, {"same": tau.get("c_i") == ci_data})

    d3 = ROOT / "exp/results/final-baseline-ready/d3_run8_interaction.json"
    d3_data = json.loads(d3.read_text()) if d3.exists() else {}
    check("D3 interaction gate PASS", d3_data.get("verdict") == "PASS",
          {"verdict": d3_data.get("verdict"), "run": d3_data.get("run")})
    failed = [c["check"] for c in d3_data.get("checks", []) if not c.get("pass")]
    check("no failing D3 check", not failed, {"failing": failed})

    wl = ROOT / "exp/workloads/final-evaluation/WORKLOAD_HASHES.json"
    wl_data = json.loads(wl.read_text()) if wl.exists() else {}
    check("workload hashes recorded", len(wl_data) > 0, {"files": len(wl_data)})

    blockers = [c["check"] for c in checks if not c["pass"]]
    verdict = "READY" if not blockers else "NOT READY"

    lines = [
        "# Baseline readiness", "",
        "```text", f"VERDICT: {verdict}",
        f"Known implementation blockers remaining: {len(blockers)} / {len(checks)}",
        "```", "",
        "| check | result |", "|---|---|",
    ]
    for c in checks:
        lines.append(f"| {c['check']} | {'PASS' if c['pass'] else '**FAIL**'} |")
    lines += [
        "", "## Frozen inputs", "", "```text",
        f"runtime commit      {env.get('runtime_freeze', {}).get('commit')}",
        f"source patch sha    {env.get('runtime_freeze', {}).get('patch_sha256_now')}",
        f"tau                 {tau.get('tau')}  ({tau.get('tau_label')})",
        f"c_i file            {ci}",
        "```", "",
        "## c_i (tokens/s, this A100 pair, frozen code)", "", "```json",
        json.dumps(ci_data, indent=2, sort_keys=True), "```", "",
        f"Recorded {datetime.now(timezone.utc).isoformat()}.", "",
    ]
    if blockers:
        lines += ["## Blockers", ""] + [f"- {b}" for b in blockers] + [""]

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(lines))
    (args.report.parent / "readiness.json").write_text(
        json.dumps({"verdict": verdict, "checks": checks}, indent=2) + "\n")
    for c in checks:
        print(f"  {'PASS' if c['pass'] else 'FAIL'}  {c['check']}")
    print(f"\nVERDICT: {verdict}")
    return 0 if verdict == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
