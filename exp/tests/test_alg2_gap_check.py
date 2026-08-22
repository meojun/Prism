#!/usr/bin/env python3
"""The D3 gate must tell a retired sequence apart from a lost one.

Retirement skips a sequence on purpose: when a request leaves a GPU before it
was admitted, the frontier steps over its sequence so the GPU does not wait for
work that will never arrive. The observed admit/start stream then has a hole in
it, and that hole is correct.

A hole nothing accounts for is the opposite -- a sequence that was issued and
then vanished -- and must still fail. These tests build both shapes and run the
real gate over them.

This is a checker change only; nothing about runtime behaviour is asserted here.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_alg2_interaction.py"

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


def runtime_line(event, seq, gpu=0, model="model_1", rid="r", ok=True, t=1000.0):
    return "[PAPER-ALG2-RUNTIME] " + json.dumps({
        "event": event, "gpu_id": gpu, "alg2_seq": seq,
        "expected_rid": rid, "expected_model": model,
        "actual_rids": [rid], "actual_alg2_seqs": [seq] if seq else [],
        "actual_model": model, "event_time": t, "order_ok": ok,
    }, sort_keys=True)


def handoff_retire(seqs, gpu=0, model="model_1", drained_admit=(), drained_start=()):
    return "[PAPER-ALG2-HANDOFF] " + json.dumps({
        "event": "migrated_away", "gpu_id": gpu, "model": model,
        "reason": "kv-stash",
        "reported": [f"r{s}" for s in seqs],
        "retired": [{"rid": f"r{s}", "seq": s} for s in seqs],
        "dequeued_before_dispatch": [],
        "drained_admit_seqs": list(drained_admit),
        "drained_start_seqs": list(drained_start),
        "next_backend_admit_seq": 0, "next_prefill_start_seq": 0,
        "outstanding_after": 0,
    }, sort_keys=True)


def build_run(scheduler_lines, tmp):
    """A run directory with just what the gate reads."""
    run = Path(tmp)
    logs = run / "server-logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "server.log.gpu_scheduler.log").write_text(
        "\n".join(scheduler_lines) + "\n")
    (logs / "server.log").write_text("")
    (logs / "stdout.log").write_text("")
    (logs / "bench.log").write_text("")
    (logs / "server.log.global_controller.log").write_text("")
    (run / "pipeline.rc").write_text("0\n")
    (run / "monitor").mkdir(exist_ok=True)
    (run / "monitor" / "status.json").write_text(json.dumps({"state": "COMPLETE"}))
    (run / "result_e2e_2gpu_1.0x_1rep.json").write_text(
        json.dumps({"completed": 1, "aborted": 0}))
    return run


def gate(scheduler_lines):
    with tempfile.TemporaryDirectory() as tmp:
        run = build_run(scheduler_lines, tmp)
        out = run / "report.json"
        subprocess.run(
            [sys.executable, str(CHECKER), "--run", str(run), "--out", str(out)],
            capture_output=True, text=True)
        report = json.loads(out.read_text())
    return {c["check"]: c for c in report["checks"]}


def admitted(seqs, gpu=0):
    lines = []
    for seq in seqs:
        lines.append(runtime_line("backend_admit", seq, gpu=gpu, rid=f"r{seq}"))
        lines.append(runtime_line("prefill_start", seq, gpu=gpu, rid=f"r{seq}"))
    return lines


def test_a_contiguous_stream_passes():
    print("no gap at all")
    checks = gate(admitted([1, 2, 3, 4]))
    check("a contiguous stream has no gap",
          checks["sequence_tokens_have_no_gaps"]["pass"])


def test_a_gap_a_retirement_explains_passes():
    print("a gap the retirement record explains")
    lines = admitted([1, 2]) + [handoff_retire([3])] + admitted([4])
    checks = gate(lines)
    detail = checks["sequence_tokens_have_no_gaps"]["detail"]
    check("the retired sequence is not counted against the run",
          checks["sequence_tokens_have_no_gaps"]["pass"])
    check("and the retirement evidence is reported",
          detail["retirements_seen"].get("0", detail["retirements_seen"].get(0)))


def test_a_gap_nothing_explains_still_fails():
    print("a gap nothing explains")
    lines = admitted([1, 2]) + admitted([4])          # 3 issued, then vanished
    checks = gate(lines)
    row = checks["sequence_tokens_have_no_gaps"]
    check("an unexplained missing sequence still fails", not row["pass"])
    check("and the gate names the sequence that went missing",
          any(3 in g["unexplained_seqs"] for g in row["detail"]["gaps"]))


def test_a_partly_explained_gap_fails():
    print("a gap only partly explained")
    # 3 retired, 4 and 5 not.
    lines = admitted([1, 2]) + [handoff_retire([3])] + admitted([6])
    checks = gate(lines)
    row = checks["sequence_tokens_have_no_gaps"]
    check("one retirement does not excuse three missing sequences",
          not row["pass"])
    gaps = row["detail"]["gaps"]
    check("the explained one is counted as explained",
          any(g["explained_by_retirement"] == 1 for g in gaps))
    check("and only the rest are reported missing",
          any(sorted(g["unexplained_seqs"]) == [4, 5] for g in gaps))


def test_a_drained_sequence_also_explains_a_gap():
    print("a gap the frontier drain explains")
    lines = admitted([1, 2]) + [
        handoff_retire([], drained_admit=[3], drained_start=[3])
    ] + admitted([4])
    check("a drained sequence explains its gap too",
          gate(lines)["sequence_tokens_have_no_gaps"]["pass"])


def test_each_gpu_is_judged_on_its_own_retirements():
    print("retirements do not transfer between GPUs")
    lines = (admitted([1, 2], gpu=0) + admitted([4], gpu=0)
             + [handoff_retire([3], gpu=1)])
    check("GPU1's retirement does not excuse GPU0's gap",
          not gate(lines)["sequence_tokens_have_no_gaps"]["pass"])


def main():
    test_a_contiguous_stream_passes()
    test_a_gap_a_retirement_explains_passes()
    test_a_gap_nothing_explains_still_fails()
    test_a_partly_explained_gap_fails()
    test_a_drained_sequence_also_explains_a_gap()
    test_each_gpu_is_judged_on_its_own_retirements()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for name in FAIL:
            print(f"  FAILED: {name}")
        raise SystemExit(1)
    print("ALL ALG2 GAP CHECK TESTS PASSED")


if __name__ == "__main__":
    main()
