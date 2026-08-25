#!/usr/bin/env python3
"""Where the Final Prism sweep is, and how much longer it has.

The estimate starts from the wall times this stack actually recorded on the
previous server (a run is server start + a 420 s trace + drain, and the drain
grows with the rate), and switches to this server's own measured times as soon
as it has any -- scaling the remaining conditions by how this box compares.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / "exp/results/final-evaluation"
PROGRESS = EVAL / "FINAL_SWEEP_PROGRESS.jsonl"

ORDER = [("bursty", 2), ("bursty", 4), ("bursty", 8), ("bursty", 14),
         ("bursty", 20), ("steady", 4), ("steady", 8), ("steady", 20)]
SEEDS = (1, 2, 3)
# seconds, from the previous server's heartbeats: r2 ~616 s, r20 ~790-860 s
PRIOR = {2: 630, 4: 660, 8: 700, 14: 780, 20: 860}


def plan():
    return [(w, r, s) for (w, r) in ORDER for s in SEEDS]


def main():
    runs = []
    if PROGRESS.is_file():
        for line in PROGRESS.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    runs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    done = [r for r in runs if r.get("ok")]
    failed = [r for r in runs if not r.get("ok")]
    full = plan()
    remaining = full[len(runs):]

    # Calibrate the prior against what this box has actually done.
    scale, basis = 1.0, "previous server's recorded wall times"
    if done:
        num = sum(r["seconds"] for r in done)
        den = sum(PRIOR[r["rate"]] for r in done)
        if den:
            scale = num / den
            basis = f"this server's {len(done)} completed run(s)"
    eta_s = sum(PRIOR[r] * scale for (_, r, _) in remaining)

    print(f"Final Prism sweep -- {len(done)}/24 complete"
          + (f", {len(failed)} failed" if failed else ""))
    stop = EVAL / "STOP"
    if stop.is_file():
        print(f"  STOP in force: {stop.read_text().strip()}")
    if done:
        print(f"  mean wall so far : {sum(r['seconds'] for r in done)/len(done)/60:.1f} min/run")
    print(f"  estimate basis   : {basis} (scale {scale:.2f})")

    # What is running right now, if anything.
    cur = None
    if remaining:
        w, r, s = remaining[0]
        d = EVAL / "05-final-c/raw" / w / f"rate_{r}" / f"seed_{s}"
        st = d / "monitor/status.json"
        if st.is_file():
            try:
                rec = json.loads(st.read_text())
                age = time.time() - rec.get("heartbeat_timestamp", 0)
                cur = (f"{w} r{r} s{s}: {rec.get('state')} / "
                       f"{rec.get('current_phase')} "
                       f"({rec.get('phase_elapsed_s', 0):.0f}s in phase, "
                       f"heartbeat {age:.0f}s ago)")
            except Exception:                              # noqa: BLE001
                cur = f"{w} r{r} s{s}: status unreadable"
        else:
            cur = f"{w} r{r} s{s}: not started"
    print(f"  running now      : {cur or 'nothing -- sweep finished'}")
    if remaining:
        print(f"  remaining        : {len(remaining)} runs, "
              f"~{eta_s/3600:.1f} h (~{time.strftime('%H:%M UTC', time.gmtime(time.time()+eta_s))})")

    if runs:
        print("\n  completed:")
        for r in runs:
            n = r.get("numbers") or {}
            g = n.get("joint_slo_goodput_req_s")
            g = f"{g:.4f}" if isinstance(g, (int, float)) else "?"
            head = (f"    [{r['idx']:2d}/24] {r['workload']:6s} r{r['rate']:<2d} "
                    f"s{r['seed']}  {r['seconds']/60:5.1f} min  "
                    f"{'OK  ' if r.get('ok') else 'FAIL'}")
            print(f"{head}  completed={n.get('completed')}/"
                  f"{n.get('offered_requests')}  goodput={g}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
