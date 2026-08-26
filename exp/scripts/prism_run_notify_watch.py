#!/usr/bin/env python3
"""Out-of-band enriched run notifications.

Watches the active stages' PROGRESS.jsonl files and, for every newly completed
run, computes TTFT/TPOT SLO attainment and p50/p95/p99 from that run's own
request dump, then pushes one line-per-metric message via notify.sh.

Deliberately a separate process: it never edits or is edited into any script the
pipeline is currently executing. If it dies, the experiment is unaffected.
"""
import json, subprocess, sys, time
from pathlib import Path
R = Path("/workspace/prism-exp")
NOTIFY = R / "exp/scripts/notify.sh"
STAGES = [("mm-pilot",   R / "exp/results/many-model-pilot"),
          ("mm-final",   R / "exp/results/many-model-final"),
          ("final4het",  R / "exp/results/4het-final"),
          ("tau",        R / "exp/results/4het-tau-final")]
SEEN = R / "exp/state/.notify_enriched_seen"


def pctl(v, q):
    v = sorted(x for x in v if x is not None)
    if not v:
        return None
    k = (len(v) - 1) * q / 100
    lo = int(k); hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def latency_block(run_dir):
    dumps = list(Path(run_dir).glob("requests/*_output_requests.json"))
    if not dumps:
        return None
    try:
        rs = [r for r in json.load(dumps[0].open())
              if isinstance(r, dict) and r.get("success")]
    except Exception:
        return None
    if not rs:
        return None
    n = len(rs)
    tok = sum(1 for r in rs if r.get("ttft") is not None
              and r.get("slo_ttft") is not None and r["ttft"] <= r["slo_ttft"])
    pok = sum(1 for r in rs if r.get("tpot") is not None
              and r.get("slo_tpot") is not None and r["tpot"] <= r["slo_tpot"])
    jok = sum(1 for r in rs if r.get("ttft") is not None and r.get("slo_ttft") is not None
              and r["ttft"] <= r["slo_ttft"] and r.get("tpot") is not None
              and r.get("slo_tpot") is not None and r["tpot"] <= r["slo_tpot"])
    ttft = [r.get("ttft") for r in rs]
    tpot = [r.get("tpot") for r in rs]
    f = lambda v, s: "-" if v is None else f"{v*s:.1f}"
    return (f"SLO attain  TTFT {100*tok/n:.1f}%  TPOT {100*pok/n:.1f}%  JOINT {100*jok/n:.1f}%\n"
            f"TTFT s      p50 {f(pctl(ttft,50),1)}  p95 {f(pctl(ttft,95),1)}  p99 {f(pctl(ttft,99),1)}\n"
            f"TPOT ms     p50 {f(pctl(tpot,50),1000)}  p95 {f(pctl(tpot,95),1000)}  p99 {f(pctl(tpot,99),1000)}")


def main():
    seen = set(SEEN.read_text().split()) if SEEN.exists() else set()
    while True:
        for stage, out in STAGES:
            pj = out / "PROGRESS.jsonl"
            if not pj.exists():
                continue
            for line in pj.read_text().splitlines():
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                d = r.get("run_dir")
                if not d:
                    continue
                key = f"{stage}:{d}"
                if key in seen or not r.get("ok"):
                    continue
                n = r.get("numbers") or {}
                lat = latency_block(d)
                if lat is None:
                    continue
                arm = r.get("arm", "?")
                cond = f"{r.get('workload')} r{r.get('rate')} s{r.get('seed')}"
                msg = (f"✅ {stage} | {arm} | {cond}\n"
                       f"goodput {n.get('joint_slo_goodput_req_s', 0):.4f} req/s   "
                       f"thr {n.get('throughput_req_s', 0):.2f}   "
                       f"migr {n.get('migrations_executed', 0)}\n"
                       f"{lat}\n"
                       f"completed {n.get('completed')}/{n.get('offered_requests')}  "
                       f"aborted {n.get('aborted')}")
                subprocess.run([str(NOTIFY), f"enriched-{abs(hash(key)) % 10**10}", msg],
                               capture_output=True)
                seen.add(key)
                SEEN.write_text("\n".join(sorted(seen)))
        time.sleep(30)


if __name__ == "__main__":
    sys.exit(main())
