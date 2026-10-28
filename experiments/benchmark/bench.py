"""Benchmark: reading only vs the full Talos loop, on all 8 parts.

  python bench.py --sets clean,photo --runs 2 --max-usd 2.0
  python bench.py --summarize          # rebuild the tables from saved results, no API calls

Each run is one full loop (read, then check/correct up to --max-iters times). "Loop off" is the spec from the read
alone (iteration 0); "loop on" is the spec the loop returns. Both are scored with the reading-spike-v2 scorer on
compiled geometry against the true spec and the written drawing dimensions.
"""
import argparse
import glob
import json
import os
import statistics as st
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "experiments", "reading-spike-v2"))

import score as scorer  # noqa: E402

from hephaestus import meta_api  # noqa: E402
from hephaestus.talos import PRESETS, run  # noqa: E402

with open(os.path.join(HERE, "ground_truth.json")) as f:
    NEW = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
scorer.GROUND_TRUTH.update(NEW)
IMAGE_DIRS = {k: os.path.join(ROOT, "experiments", "reading-spike", "sketches") for k in scorer.GROUND_TRUTH}
IMAGE_DIRS.update({k: os.path.join(HERE, "sketches") for k in NEW})


def safe_score(part, spec):
    if not isinstance(spec, dict):
        return {"compiles": False, "perfect": False, "errors": ["no spec"]}
    s = scorer.score(part, spec)
    return {k: s.get(k) for k in ("compiles", "perfect", "iou", "bbox_ok", "dims_ok", "failed_dims", "errors",
                                  "params", "x_param")}


def one(part, set_name, run_no, client, max_iters, outdir, settings=None):
    image = os.path.join(IMAGE_DIRS[part], scorer.GROUND_TRUTH[part]["images"][set_name])
    events = list(run(image, max_iters=max_iters, client=client, settings=settings))
    specs = [e for e in events if e["event"] == "spec"]
    checks = [e for e in events if e["event"] == "checks"]
    done = events[-1]
    read = specs[0]["call"] if specs and specs[0].get("call") else {}
    rec = {
        "part": part, "set": set_name, "run": run_no, "status": done["status"], "note": done.get("note"),
        "corrections": done.get("iterations", 0), "final_iteration": done.get("final_iteration"),
        "cost_off": read.get("cost_usd"), "cost_on": done.get("cost_usd"),
        "seconds_off": read.get("seconds"), "seconds_on": done.get("seconds"),
        "off": safe_score(part, specs[0]["spec"] if specs else None),
        "on": safe_score(part, done.get("spec")),
        "iterations": [{"iteration": c["iteration"], "issues": c["issues"],
                        "critic_seconds": (c.get("critic_call") or {}).get("seconds")} for c in checks],
        "corrections_log": [{"iteration": s["iteration"], "changelog": s["changelog"], **(s.get("call") or {})}
                            for s in specs[1:]],
        "events": events,
    }
    with open(os.path.join(outdir, f"{part}__{set_name}.run{run_no}.json"), "w") as f:
        json.dump(rec, f, indent=1)
    return rec


def summarize(outdir):
    recs = [json.load(open(p)) for p in sorted(glob.glob(os.path.join(outdir, "*.run*.json")))]
    recs = [r for r in recs if "off" in r]
    lines = ["| set | runs | perfect, loop off | perfect, loop on | compiles off / on | avg corrections | "
             "cost off / on (avg) | latency off / on (median) |", "|---|---|---|---|---|---|---|---|"]
    for set_name in sorted({r["set"] for r in recs}):
        rs = [r for r in recs if r["set"] == set_name]
        n = len(rs)
        lines.append(
            f"| {set_name} | {n} | {sum(r['off']['perfect'] for r in rs)}/{n} | {sum(r['on']['perfect'] for r in rs)}/{n} | "
            f"{sum(bool(r['off']['compiles']) for r in rs)} / {sum(bool(r['on']['compiles']) for r in rs)} | "
            f"{st.mean(r['corrections'] for r in rs):.1f} | "
            f"${st.mean(r['cost_off'] or 0 for r in rs):.4f} / ${st.mean(r['cost_on'] or 0 for r in rs):.4f} | "
            f"{st.median(r['seconds_off'] or 0 for r in rs):.0f} s / {st.median(r['seconds_on'] or 0 for r in rs):.0f} s |")
    lines += ["", "| part | set | off: perfect runs | on: perfect runs | corrections | still failing (loop on) |",
              "|---|---|---|---|---|---|"]
    for part in sorted({r["part"] for r in recs}):
        for set_name in sorted({r["set"] for r in recs}):
            rs = [r for r in recs if r["part"] == part and r["set"] == set_name]
            if not rs:
                continue
            fails = sorted({d["label"] for r in rs for d in (r["on"].get("failed_dims") or [])})
            errs = [e for r in rs for e in (r["on"].get("errors") or [])]
            lines.append(f"| {part} | {set_name} | {sum(r['off']['perfect'] for r in rs)}/{len(rs)} | "
                         f"{sum(r['on']['perfect'] for r in rs)}/{len(rs)} | {', '.join(str(r['corrections']) for r in rs)} | "
                         f"{', '.join(fails) or (errs[0][:60] if errs else '-')} |")
    text = "\n".join(lines)
    with open(os.path.join(outdir, "summary.md"), "w") as f:
        f.write(text + "\n")
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", default="clean,photo")
    ap.add_argument("--parts", help="comma-separated, default all 8")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--max-iters", type=int, default=3)
    ap.add_argument("--max-usd", type=float, default=2.0)
    ap.add_argument("--model", default=meta_api.DEFAULT_MODEL, help="results folder name")
    ap.add_argument("--preset", default="benchmark", help="talos.PRESETS key")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--summarize", action="store_true")
    ap.add_argument("--rescore", action="store_true", help="re-score saved runs (e.g. after a ground-truth fix), no API calls")
    ap.add_argument("--tag", default="", help="results subfolder, e.g. round2")
    ap.add_argument("--read-effort", help="override the preset's read effort (low/medium/high)")
    args = ap.parse_args()
    outdir = os.path.join(HERE, "results", args.model, args.tag) if args.tag else os.path.join(HERE, "results", args.model)
    os.makedirs(outdir, exist_ok=True)
    if args.rescore:
        for path in sorted(glob.glob(os.path.join(outdir, "*.run*.json"))):
            rec = json.load(open(path))
            specs = [e for e in rec["events"] if e["event"] == "spec"]
            rec["off"] = safe_score(rec["part"], specs[0]["spec"] if specs else None)
            rec["on"] = safe_score(rec["part"], rec["events"][-1].get("spec"))
            json.dump(rec, open(path, "w"), indent=1)
        args.summarize = True
    if args.summarize:
        print(summarize(outdir))
        return
    budget = meta_api.Budget(args.max_usd)
    client = meta_api.Client(args.model, budget=budget)
    parts = args.parts.split(",") if args.parts else sorted(scorer.GROUND_TRUTH)
    jobs = [(p, s, r) for s in args.sets.split(",") for p in parts for r in range(1, args.runs + 1)]
    lock = threading.Lock()
    settings = PRESETS[args.preset]
    if args.read_effort:
        settings = replace(settings, read_effort=args.read_effort)

    def do(job):
        part, set_name, r = job
        try:
            rec = one(part, set_name, r, client, args.max_iters, outdir, settings)
            msg = (f"off perfect={rec['off']['perfect']} on perfect={rec['on']['perfect']} {rec['status']} "
                   f"{rec['corrections']} corr ${rec['cost_on']:.4f} {rec['seconds_on']:.0f}s")
        except Exception as e:  # noqa: BLE001 - keep the benchmark going; record nothing for this run
            msg = f"FAILED {type(e).__name__}: {e}"[:300]
        with lock:
            print(f"{part} {set_name} run {r}: {msg}", flush=True)

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(do, jobs))
    print(f"\nspent ${budget.spent:.4f}\n")
    print(summarize(outdir))


if __name__ == "__main__":
    main()
