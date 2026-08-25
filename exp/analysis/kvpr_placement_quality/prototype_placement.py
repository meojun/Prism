#!/usr/bin/env python3
"""Prototype static composition, parsed from its own controller log.

The Prototype runs a different policy and emits no [PAPER-ALG1-V4] trace, so
placement is read from its per-model instance dump:
    Model model_5:
      Instance 0: ACTIVE, ..., gpu_ids: [0], ...
An ACTIVE instance is where the model is resident in that cycle.
"""
import csv, json, re, statistics
from collections import Counter
from pathlib import Path
ROOT = Path("/workspace/prism-exp")
OUT = ROOT / "exp/analysis/kvpr_placement_quality"
PROTO = ROOT / "exp/results/4het-paired/raw/prototype"
MODELS = ("model_3", "model_4", "model_5", "model_6")
BIG = ("model_5", "model_6")
MODEL_RE = re.compile(r"GlobalController\] Model (model_\d+):\s*$")
INST_RE = re.compile(r"Instance (\d+): (ACTIVE|INACTIVE),.*gpu_ids: \[(\d+)\]")
TS_RE = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)")


def placements(log):
    """Yield {model: gpu} snapshots, one per timestamp where all 4 are known."""
    cur, ts, out = {}, None, []
    model = None
    for line in log.open(errors="replace"):
        m = MODEL_RE.search(line)
        if m:
            model = m.group(1)
            t = TS_RE.match(line)
            if t and t.group(1) != ts:
                if len(cur) == 4:
                    out.append((ts, dict(cur)))
                ts, cur = t.group(1), {}
            continue
        if model:
            i = INST_RE.search(line)
            if i and i.group(2) == "ACTIVE":
                cur[model] = int(i.group(3))
    if len(cur) == 4:
        out.append((ts, dict(cur)))
    return out


rows = []
for kind in ("bursty", "steady"):
    for rate in (2, 4, 6, 8, 10):
        for seed in (1, 2):
            run = PROTO / kind / f"rate_{rate}" / f"seed_{seed}"
            log = run / "server-logs/server.log.global_controller.log"
            snaps = placements(log) if log.exists() else []
            shapes, colo, comps = Counter(), Counter(), Counter()
            for _, p in snaps:
                c = Counter(p.values())
                shapes["+".join(str(c.get(g, 0)) for g in range(2))] += 1
                colo[p[BIG[0]] == p[BIG[1]]] += 1
                comps[tuple(sorted(tuple(sorted(m for m in MODELS if p[m] == g))
                                   for g in range(2)))] += 1
            n = len(snaps)
            num = json.loads((run / "VERIFICATION.json").read_text())["numbers"]
            top = comps.most_common(1)[0] if comps else (None, 0)
            rows.append({
                "workload": kind, "rate": rate, "seed": seed, "snapshots": n,
                "dominant_shape": shapes.most_common(1)[0][0] if shapes else None,
                "large_colocated_pct": round(100 * colo[True] / n, 1) if n else None,
                "dominant_composition": " | ".join("+".join(x.split("_")[1] for x in g)
                                                   for g in top[0]) if top[0] else None,
                "dominant_composition_pct": round(100 * top[1] / n, 1) if n else None,
                "migrations": num["migrations_executed"],
                "goodput": round(num["joint_slo_goodput_req_s"], 4),
                "attainment": round(num["joint_slo_attainment"], 4),
            })

with (OUT / "prototype_pairing.csv").open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
print(f"{'cond':>16}{'snaps':>7}{'shape':>7}{'largeLL%':>10}{'dominant composition':>26}{'pct':>7}{'migr':>6}")
for r in rows:
    print(f"{r['workload']}_r{r['rate']}_s{r['seed']:<3}{r['snapshots']:>7}"
          f"{str(r['dominant_shape']):>7}{str(r['large_colocated_pct']):>10}"
          f"{str(r['dominant_composition']):>26}{str(r['dominant_composition_pct']):>7}{r['migrations']:>6}")
ll = [r["large_colocated_pct"] for r in rows if r["large_colocated_pct"] is not None]
if ll:
    print(f"\nPrototype large-large residency over {len(ll)} conditions: "
          f"min={min(ll):.1f}%  median={statistics.median(ll):.1f}%  max={max(ll):.1f}%")
