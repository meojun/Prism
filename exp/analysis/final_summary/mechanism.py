#!/usr/bin/env python3
"""Section 7 mechanism analysis, offline, existing telemetry only."""
import csv, json, statistics, sys
from pathlib import Path
R=Path("/workspace/prism-exp"); OUT=R/"exp/analysis/final_summary"
sys.path.insert(0,str(R/"exp/analysis/estimator_correction/bad_placement_forensic"))
sys.path.insert(0,str(R/"exp/analysis/kvpr_placement_quality"))
sys.path.insert(0,str(R/"exp/analysis/planner_oscillation"))
from forensic import kvpr_of, colocated, BIG            # noqa
from quality import enumerate_valid                     # noqa
from solve_rates import cycles, solve                   # noqa
f=lambda x: float(x) if x not in ("","None",None) else None

mm=list(csv.DictReader(open(R/"exp/analysis/many_model_prism_favorable/run_manifest.csv")))
hh=list(csv.DictReader(open(R/"exp/analysis/final_4het/run_manifest.csv")))
mmp=list(csv.DictReader(open(R/"exp/analysis/many_model_prism_favorable/paired_goodput.csv")))

rows=[]
print("A/B/C  many-model: migration cost vs benefit by load band\n")
print(f"{'rate':>5}{'delta%':>9}{'migr':>7}{'GB moved':>10}{'GB/goodput-pt':>15}{'TPOT p50 P->S ms':>20}{'TTFT p99 P->S s':>20}")
for r in (2,4,6,8,10):
    d=statistics.mean(f(x["delta_pct"]) for x in mmp if int(x["rate"])==r)
    ms=[x for x in mm if x["arm"]=="prism" and int(x["rate"])==r]
    ps=[x for x in mm if x["arm"]=="prototype" and int(x["rate"])==r]
    gb=statistics.mean(f(x["migration_bytes"]) for x in ms)/2**30
    mig=statistics.mean(int(x["migrations"]) for x in ms)
    tp_p,tp_m=(statistics.mean(f(x["tpot_ms_p50"]) for x in ps),statistics.mean(f(x["tpot_ms_p50"]) for x in ms))
    tt_p,tt_m=(statistics.mean(f(x["ttft_s_p99"]) for x in ps),statistics.mean(f(x["ttft_s_p99"]) for x in ms))
    print(f"{r:>5}{d:>+8.1f}%{mig:>7.1f}{gb:>10.1f}{(gb/abs(d) if d else 0):>15.2f}"
          f"{tp_p:>12.1f} ->{tp_m:>5.1f}{tt_p:>13.2f} ->{tt_m:>5.2f}")
    rows.append({"regime":"many_model","rate":r,"delta_pct":round(d,2),"migrations":round(mig,1),
                 "gb_moved":round(gb,2),"tpot_p50_proto_ms":round(tp_p,2),"tpot_p50_prism_ms":round(tp_m,2),
                 "ttft_p99_proto_s":round(tt_p,3),"ttft_p99_prism_s":round(tt_m,3)})

print("\nD  final 4-HET: does Prism actually sit in large-large co-residency?")
print(f"{'workload':>9}{'rate':>5}{'large-large residency %':>25}{'migr':>7}{'delta%':>9}")
hp=list(csv.DictReader(open(R/"exp/analysis/final_4het/paired_goodput.csv")))
for k in ("steady","bursty"):
    for r in (2,4,6,8,10):
        fr=[]
        for s in (5,6):
            d=R/f"exp/results/4het-final/raw/prism/{k}/rate_{r}/seed_{s}/server-logs/server.log.global_controller.log"
            cs=cycles(d); n=co=0
            for c in cs:
                cur=c.get("current_placement") or {}
                if len(cur)<4: continue
                n+=1; co+=colocated(cur)
            if n: fr.append(100*co/n)
        mig=statistics.mean(int(x["migrations"]) for x in hh if x["arm"]=="prism" and x["workload"]==k and int(x["rate"])==r)
        dl=statistics.mean(f(x["delta_pct"]) for x in hp if x["workload"]==k and int(x["rate"])==r)
        ll=statistics.mean(fr) if fr else None
        print(f"{k:>9}{r:>5}{(f'{ll:.1f}%' if ll is not None else '-'):>25}{mig:>7.1f}{dl:>+8.1f}%")
        rows.append({"regime":"final_4het","workload":k,"rate":r,
                     "large_large_residency_pct":round(ll,1) if ll is not None else None,
                     "migrations":round(mig,1),"delta_pct":round(dl,2)})
with (OUT/"mechanism_evidence.csv").open("w",newline="") as fh:
    cols=sorted({k for x in rows for k in x})
    w=csv.DictWriter(fh,fieldnames=cols); w.writeheader(); w.writerows(rows)
print("\nwrote mechanism_evidence.csv")
