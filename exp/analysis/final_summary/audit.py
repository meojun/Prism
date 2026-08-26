#!/usr/bin/env python3
"""Complete offline audit of every authoritative final result (section 2)."""
import csv, hashlib, json, os, re, subprocess, sys
from pathlib import Path
# Repository root is derived from this file, never hard-coded, so the audit
# runs correctly from a clone at any path.
R = Path(__file__).resolve().parents[3]; OUT = R / "exp/analysis/final_summary"
RUNTIME = (R / "patches/lifecycle_containment/WORKTREE_PATCH_SHA256").read_text().strip()
FINAL_TAU = 0.012859417696566448
GATE = R / "exp/scripts/lifecycle_validity_gate.py"
PY_ = os.environ.get("PRISM_PYTHON") or sys.executable


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


FAMILIES = [
    ("tau", R/"exp/results/4het-tau-final/raw", R/"exp/manifests/prism_final/TAU_CALIBRATION_TRACE_MANIFEST.json",
     R/"exp/workloads/4het-cal", [(a,"steady",r,s) for a in ("T0","T1","T2","T3","T4","T5") for r in (8,10) for s in (3,4)]
     + [(a,"bursty",r,s) for a in ("T0","T1","T2","T3","T4","T5") for r in (8,10) for s in (3,4)], 48),
    ("many_model", R/"exp/results/many-model-final/raw", R/"exp/manifests/prism_final/MANY_MODEL_TRACE_MANIFEST.json",
     R/"exp/workloads/many_model_pf",
     [(a,"bursty",r,s) for a in ("prototype","prism") for r in (2,4,6,8,10) for s in (7,8)], 20),
    ("final_4het", R/"exp/results/4het-final/raw", R/"exp/manifests/prism_final/FINAL_4HET_TRACE_MANIFEST.json",
     R/"exp/workloads/4het-final",
     [(a,k,r,s) for a in ("prototype","prism") for k in ("steady","bursty") for r in (2,4,6,8,10) for s in (5,6)], 40),
]

rows, fails = [], []
for fam, raw, manp, wl, conds, expect in FAMILIES:
    man = json.load(open(manp))["files"]
    for arm, k, r, s in conds:
        sub = f"prism-{arm}" if fam == "tau" else arm
        d = raw / sub / k / f"rate_{r}" / f"seed_{s}"
        rec = {"family": fam, "arm": arm, "workload": k, "rate": r, "seed": s,
               "path": str(d.relative_to(R))}
        probs = []
        vf = d / "VERIFICATION.json"
        if not vf.exists():
            probs.append("VERIFICATION.json missing")
            rec.update(rc=None, verdict=None); rows.append({**rec, "status": "FAIL",
                       "problems": "; ".join(probs)}); fails.append(rec["path"]); continue
        v = json.loads(vf.read_text()); n = v["numbers"]
        rec["rc"] = int(v["rc"]); rec["verdict"] = v["verdict"]
        rec["completed"] = n["completed"]; rec["offered"] = n["offered_requests"]
        rec["aborted"] = n["aborted"]; rec["alg2_order_violations"] = n["alg2_order_violations"]
        rec["staged_return_failures"] = n["staged_return_failures"]
        rec["client_errors"] = n["client_errors"]
        if rec["rc"] != 0: probs.append(f"rc={rec['rc']}")
        if v["verdict"] != "PASS": probs.append(f"verdict={v['verdict']}")
        if v.get("failed_gates"): probs.append(f"failed_gates={v['failed_gates']}")
        if n["aborted"]: probs.append(f"aborted={n['aborted']}")
        if n["alg2_order_violations"]: probs.append("alg2 order violation")
        if n["staged_return_failures"]: probs.append("staged return failure")
        # lifecycle
        g = subprocess.run([PY_, str(GATE), str(d)], capture_output=True, text=True)
        rec["lifecycle_gate"] = "PASS" if g.returncode == 0 else "FAIL"
        if g.returncode: probs.append("lifecycle gate FAIL")
        # trace + runtime + config from the run's own command line
        cmd = (d / "SERVER_COMMAND.txt").read_text() if (d / "SERVER_COMMAND.txt").exists() else ""
        stage = (d / "STAGE_CMD.sh").read_text() if (d / "STAGE_CMD.sh").exists() else ""
        tname = f"{k}_r{r}_s{s}.pkl"
        tp = wl / tname
        rec["trace"] = tname
        rec["trace_sha256"] = sha(tp) if tp.exists() else None
        want = man.get(tname, {}).get("sha256")
        rec["trace_sha_matches_manifest"] = rec["trace_sha256"] == want
        if not rec["trace_sha_matches_manifest"]: probs.append("trace SHA != manifest")
        m = re.search(r"--kvpr-tau (\S+)", cmd); rec["tau"] = m.group(1) if m else None
        m = re.search(r"--kvpr-rate-window (\S+)", cmd); rec["window"] = m.group(1) if m else None
        m = re.search(r"--kvpr-migration-cooldown (\S+)", cmd); rec["cooldown"] = m.group(1) if m else None
        m = re.search(r"--policy (\S+)", cmd); rec["policy"] = m.group(1) if m else None
        # KVPR flags are Prism-only. The released prototype runs --policy
        # simple-global and legitimately carries no --kvpr-* arguments, so
        # asserting them there would fail a correct run.
        is_prism = (fam == "tau") or (arm == "prism")
        if is_prism:
            if rec["window"] not in ("60", "60.0"): probs.append(f"window={rec['window']}")
            if rec["cooldown"] not in ("30", "30.0"): probs.append(f"cooldown={rec['cooldown']}")
            if fam != "tau" and (rec["tau"] is None
                                 or abs(float(rec["tau"]) - FINAL_TAU) > 1e-15):
                probs.append(f"tau={rec['tau']} != FINAL_TAU")
        else:
            if rec["window"] or rec["cooldown"] or rec["tau"]:
                probs.append("prototype unexpectedly carries KVPR flags")
        exp_pol = "simple-global" if arm == "prototype" else "kvpr-global-v4"
        if fam != "tau" and rec["policy"] != exp_pol: probs.append(f"policy={rec['policy']}")
        # seed / rate present in the trace path the run actually used
        if tname not in stage and tname not in cmd: probs.append("trace name not in run command")
        # fatal cuda / nccl
        sl = d / "server-logs/server.log"
        txt = sl.read_text(errors="replace") if sl.exists() else ""
        nfatal = len(re.findall(r"torch\.OutOfMemoryError|CUDA out of memory|NCCL error|CUDA error:", txt))
        rec["fatal_cuda_nccl"] = nfatal
        if nfatal: probs.append(f"fatal cuda/nccl x{nfatal}")
        rec["runtime_expected"] = RUNTIME[:16]
        rec["status"] = "PASS" if not probs else "FAIL"
        rec["problems"] = "; ".join(probs)
        rows.append(rec)
        if probs: fails.append(rec["path"])

with (OUT / "FINAL_RESULT_AUDIT.csv").open("w", newline="") as f:
    cols = sorted({k for r in rows for k in r})
    order = ["family","arm","workload","rate","seed","status","rc","verdict","completed",
             "offered","aborted","alg2_order_violations","staged_return_failures",
             "client_errors","lifecycle_gate","fatal_cuda_nccl","tau","window","cooldown",
             "policy","trace","trace_sha256","trace_sha_matches_manifest",
             "runtime_expected","path","problems"]
    cols = [c for c in order if c in cols] + [c for c in cols if c not in order]
    w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)

# trace pairing, byte for byte
pairs = []
for fam, raw, manp, wl, conds, _ in FAMILIES:
    if fam == "tau":
        continue
    seen = {}
    for arm, k, r, s in conds:
        seen.setdefault((k, r, s), {})[arm] = sha(wl / f"{k}_r{r}_s{s}.pkl")
    for kk, v in seen.items():
        ok = v.get("prototype") == v.get("prism") and v.get("prototype") is not None
        pairs.append({"family": fam, "workload": kk[0], "rate": kk[1], "seed": kk[2],
                      "identical": ok, "sha256": v.get("prototype")})
        if not ok: fails.append(f"pairing {fam} {kk}")

for fam, _, _, _, _, expect in FAMILIES:
    got = sum(1 for r in rows if r["family"] == fam and r["status"] == "PASS")
    print(f"  {fam:<12} {got}/{expect} PASS")
print(f"  trace pairing {sum(1 for p in pairs if p['identical'])}/{len(pairs)} byte-identical")
print(f"\nAUDIT = {'PASS' if not fails else 'FAIL'}   ({len(fails)} problems)")
for f in fails[:15]: print("   !", f)
json.dump({"pairs": pairs, "failures": fails}, open(OUT / "audit_pairing.json", "w"), indent=1)
sys.exit(1 if fails else 0)
