#!/usr/bin/env python3
"""Model configuration gate: is this server about to run the same models,
revisions, tokenizers, precision and assignment the handoff manifest records?

handoff_preflight.py checks that the six revisions are in the cache. This adds
what the instruction asks for on top: the exact tokenizer revision, the
dtype/precision each model will actually load at, the placement configuration
and the SLO base, all against FINAL_BASELINE_MANIFEST.json rather than against
whatever Hugging Face serves as `main` today.

Exit non-zero on any mismatch. No benchmark should start on a failure here.
"""
import argparse
import hashlib
import json
import pickle
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path,
                    default=ROOT / "exp/FINAL_BASELINE_MANIFEST.json")
    ap.add_argument("--workloads", type=Path,
                    default=ROOT / "exp/workloads/final-evaluation")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "exp/results/final-evaluation/MODEL_CONFIG_PREFLIGHT.json")
    args = ap.parse_args()

    man = json.loads(args.manifest.read_text())
    rows = []

    def add(name, ok, detail):
        rows.append({"check": name, "pass": bool(ok), "detail": detail})
        return ok

    # --- the placement / model-config file itself -------------------------
    mc = man["model_config"]
    p = ROOT / mc["path"]
    got = sha256_file(p) if p.is_file() else None
    add("model config file matches the manifest", got == mc["sha256"],
        f"{mc['path']}: {(got or 'absent')[:12]} vs {mc['sha256'][:12]}")

    # --- assignment: model -> gpu, tp_size, memory pool -------------------
    if p.is_file():
        live = json.loads(p.read_text())
        want = mc["content"]
        add("model assignment configuration is unchanged", live == want,
            "same model_name / model_path / tp_size / init_placements"
            if live == want else "placement configuration differs")

    # --- the SLO base -----------------------------------------------------
    slo = man["slo"]
    sp = ROOT / slo["path"]
    got = sha256_file(sp) if sp.is_file() else None
    add("SLO base matches the manifest", got == slo["sha256"],
        f"{slo['path']}: {(got or 'absent')[:12]} vs {slo['sha256'][:12]}")

    # --- the six models: id, revision, tokenizer revision, dtype ----------
    TOKENIZER_FILES = ("tokenizer.json", "tokenizer_config.json")
    for name, info in sorted(man["models"].items()):
        snap = Path(info["hf_snapshot"])
        want_rev = snap.name
        add(f"{name} revision {want_rev[:12]} present", snap.is_dir(),
            f"{info['model_path']} @ {want_rev}")
        if not snap.is_dir():
            continue
        # The tokenizer is not versioned separately: it is the tokenizer that
        # ships inside this snapshot, so pinning the revision pins it -- but
        # only if the files are actually there.
        present = [f for f in TOKENIZER_FILES if (snap / f).is_file()]
        add(f"{name} tokenizer files come from revision {want_rev[:12]}",
            "tokenizer_config.json" in present,
            f"{present} under {want_rev[:12]}")
        cfg = snap / "config.json"
        dtype = None
        if cfg.is_file():
            c = json.loads(cfg.read_text())
            dtype = c.get("torch_dtype")
        add(f"{name} loads at bfloat16", dtype == "bfloat16",
            f"config.json torch_dtype = {dtype} (no --dtype flag is passed, so "
            "the checkpoint's own dtype is what the server loads)")

    # --- workload model assignment ---------------------------------------
    # The traces are (prefix, requests) tuples of request objects, and the
    # manifest records both the model set and a wall-clock-independent
    # fingerprint of the arrival sequence. The fingerprint is the strong check:
    # it covers the model of every request, its prompt and output length and
    # its two SLOs, in arrival order.
    sys.path.insert(0, str(ROOT / "exp/scripts"))
    from final_freeze_canonical import fingerprint  # noqa: E402

    detail = man["workloads"]["detail"]
    bad = []
    for fname, meta in sorted(detail.items()):
        wp = args.workloads / fname
        if not wp.is_file():
            bad.append(f"{fname}: absent")
            continue
        try:
            _prefix, trace = pickle.loads(wp.read_bytes())
        except Exception as e:                            # noqa: BLE001
            bad.append(f"{fname}: unreadable ({type(e).__name__}: {e})")
            continue
        names = Counter(r.model for r in trace)
        if sorted(names) != sorted(meta["models"]):
            bad.append(f"{fname}: models {sorted(names)} != {sorted(meta['models'])}")
        elif sum(names.values()) != meta["requests"]:
            bad.append(f"{fname}: {sum(names.values())} requests != {meta['requests']}")
        elif fingerprint(trace) != meta["sequence_fingerprint"]:
            bad.append(f"{fname}: arrival-sequence fingerprint differs")
    add("every canonical workload carries the recorded six-model assignment "
        "and arrival sequence", not bad,
        bad or f"{len(detail)} workloads: model mix and sequence fingerprint "
               "match the manifest")

    failed = [r for r in rows if not r["pass"]]
    out = {"manifest": str(args.manifest), "checks": rows,
           "failed": [r["check"] for r in failed],
           "verdict": "PASS" if not failed else "FAIL"}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2))
    for r in rows:
        print(f"  {'PASS' if r['pass'] else 'FAIL'}  {r['check']}: {r['detail']}")
    print(f"\n{out['verdict']} -- {len(rows) - len(failed)}/{len(rows)} checks")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
