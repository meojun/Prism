#!/usr/bin/env python3
"""Section 3 -- artifact and run integrity for the OLD/NEW pairs."""
import json, csv, hashlib, re
from pathlib import Path
ROOT = Path("/workspace/prism-exp")
OUT = ROOT / "exp/analysis/estimator_correction/bad_placement_forensic"
ARMS = {"OLD": (ROOT / "exp/results/4het-paired/raw/prism", "6618671"),
        "NEW": (ROOT / "exp/results/4het-estimator-correction/raw/prism-estimator",
                "6618671+estimator 2b5430c1b04b")}
CONDS = [("steady", 8, 1), ("steady", 8, 2), ("steady", 10, 1), ("steady", 10, 2)]
MANIFEST = json.load(open(ROOT / "exp/results/4het-paired/WORKLOAD_MANIFEST.json"))


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def trace_of(run):
    """The trace file the run's own command line records -- not an assumption."""
    cmd = (run / "STAGE_CMD.sh")
    txt = cmd.read_text() if cmd.exists() else ""
    m = re.search(r"(\S*exp/workloads/4het/\S+\.pkl)", txt)
    return m.group(1) if m else None


rows = []
for arm, (base, rt) in ARMS.items():
    for kind, rate, seed in CONDS:
        run = base / kind / f"rate_{rate}" / f"seed_{seed}"
        v = json.loads((run / "VERIFICATION.json").read_text())
        n = v["numbers"]
        tp = trace_of(run)
        rows.append({
            "arm": arm, "cond": f"{kind}_r{rate}_s{seed}", "runtime": rt,
            "trace_path": tp, "trace_sha256": sha(tp) if tp and Path(tp).exists() else None,
            "manifest_sha256": MANIFEST["files"][f"{kind}_r{rate}_s{seed}.pkl"]["sha256"],
            "rc": int(v["rc"]), "verdict": v["verdict"],
            "failed_gates": len(v.get("failed_gates") or []),
            "offered": n["offered_requests"], "completed": n["completed"],
            "aborted": n["aborted"], "client_errors": n["client_errors"],
            "alg2_order_violations": n["alg2_order_violations"],
            "staged_return_failures": n["staged_return_failures"],
            "migrations": n["migrations_executed"],
            "controller_log": str((run / "server-logs/server.log.global_controller.log")
                                  .relative_to(ROOT)),
        })

with (OUT / "run_integrity.csv").open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

fail = []
for kind, rate, seed in CONDS:
    c = f"{kind}_r{rate}_s{seed}"
    o = next(r for r in rows if r["arm"] == "OLD" and r["cond"] == c)
    nw = next(r for r in rows if r["arm"] == "NEW" and r["cond"] == c)
    if o["trace_sha256"] != nw["trace_sha256"]:
        fail.append(f"{c}: OLD/NEW traces differ")
    if o["trace_sha256"] != o["manifest_sha256"]:
        fail.append(f"{c}: trace does not match the frozen manifest")
for r in rows:
    if r["rc"] != 0 or r["verdict"] != "PASS" or r["failed_gates"]:
        fail.append(f"{r['arm']} {r['cond']}: rc={r['rc']} verdict={r['verdict']} "
                    f"failed_gates={r['failed_gates']}")
    if r["alg2_order_violations"] or r["staged_return_failures"]:
        fail.append(f"{r['arm']} {r['cond']}: Alg2/staged-return violations non-zero")

# client_errors of 1-2 requests appear in BOTH arms and predate this phase; they
# are inside the accepted gate suite, so they are reported, not treated as a
# failure that would invalidate the OLD/NEW comparison.
noted = [f"{r['arm']} {r['cond']}: client_errors={r['client_errors']}"
         for r in rows if r["client_errors"]]

print(f"{'arm':>4}{'cond':>16}{'rc':>4}{'verdict':>9}{'compl/offered':>16}"
      f"{'abort':>7}{'alg2v':>7}{'migr':>6}  trace_sha")
for r in rows:
    print(f"{r['arm']:>4}{r['cond']:>16}{r['rc']:>4}{r['verdict']:>9}"
          f"{r['completed']:>9}/{r['offered']:<6}{r['aborted']:>7}"
          f"{r['alg2_order_violations']:>7}{r['migrations']:>6}  {r['trace_sha256'][:12]}")
print("\nOLD vs NEW share the identical canonical trace in all 4 conditions: "
      f"{'YES' if not any('traces differ' in x for x in fail) else 'NO'}")
print(f"\nRUN_INTEGRITY = {'PASS' if not fail else 'FAIL'}")
for x in fail:
    print("  !", x)
if noted:
    print("\nNoted (present in both arms, pre-existing, inside the accepted gates):")
    for x in noted:
        print("  -", x)
json.dump({"rows": rows, "failures": fail, "noted": noted}, open(OUT / "run_integrity.json", "w"), indent=1)
