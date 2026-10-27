"""Does the Talos loop catch and fix known errors? Inject one error into a correct spec and start the loop at "check".

  python loop_eval.py --runs 2 --max-usd 0.5          # all injected errors + false-alarm checks
  python loop_eval.py --only rotate_slot --runs 1
  python loop_eval.py --false-alarms-only --parts plate,l_bracket

Each case starts from the true spec with one error, the true callouts (a correct reading) and the real clean drawing.
"Detected" = the first check reports it (by callout, critic or both). "Fixed" = the final spec scores perfect against
ground truth with the reading-spike-v2 scorer (compiled geometry, not spec text). False alarms: the unmodified true
specs must pass the callout check and the critic.
"""
import argparse
import copy
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "experiments", "reading-spike-v2"))

from hephaestus import meta_api  # noqa: E402
from hephaestus.talos import check, run  # noqa: E402
from score import GROUND_TRUTH, score  # noqa: E402

SKETCHES = os.path.join(ROOT, "experiments", "reading-spike", "sketches")
PARTS = {}          # short name -> (ground-truth key, image dir, clean image); extended by benchmark parts
for key, gt in GROUND_TRUTH.items():
    PARTS[os.path.basename(gt["spec"])[:-5]] = (key, SKETCHES, gt["images"]["clean"])
with open(os.path.join(ROOT, "experiments", "benchmark", "ground_truth.json")) as f:
    _bench = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
GROUND_TRUTH.update(_bench)
for key, gt in _bench.items():
    PARTS[os.path.basename(gt["spec"])[:-5]] = (key, os.path.join(ROOT, "experiments", "benchmark", "sketches"),
                                                gt["images"]["clean"])


def load(name, kind):
    path = os.path.join(ROOT, "examples", *(["callouts"] if kind == "callouts" else []), f"{name}.json")
    with open(path) as f:
        return json.load(f)


def _set(spec, fid, **kw):
    f = next(f for f in spec["features"] if f["id"] == fid)
    f.update(kw)


def _drop(spec, fid):
    spec["features"] = [f for f in spec["features"] if f["id"] != fid]


def _param(spec, name, value):
    spec["parameters"][name]["value"] = value


def _swap_xy(spec):
    w, d = spec["parameters"]["width"]["value"], spec["parameters"]["depth"]["value"]
    _param(spec, "width", d)
    _param(spec, "depth", w)


ERRORS = {
    "rotate_slot": ("l_bracket", "base slot turned 90 degrees", lambda s: _set(s, "base_slot", angle=90)),
    "move_hole": ("plate", "centre bore moved 5 mm along X", lambda s: _set(s, "bore", center=["width / 2 + 5", "depth / 2"])),
    "drop_hole": ("block", "the second side hole is missing", lambda s: _drop(s, "side_holes")),
    "wrong_diameter": ("block", "bore Ø10 instead of Ø12", lambda s: _param(s, "bore_d", 10)),
    "chamfer_for_fillet": ("block", "vertical corners chamfered instead of R5",
                           lambda s: [_drop(s, "corners"), s["features"].insert(4, {"id": "corners", "type": "chamfer",
                                                                                    "size": "corner_r",
                                                                                    "edges": "vertical_outer"})]),
    "swap_xy": ("plate", "plate 50 x 80 instead of 80 x 50", _swap_xy),
}


def injected(name):
    part, _, fn = ERRORS[name]
    spec = load(part, "spec")
    fn(spec)
    return part, spec


def run_case(name, run_no, client, outdir):
    part, desc, _ = ERRORS[name]
    gt_key, img_dir, image = PARTS[part]
    _, spec = injected(name)
    start = {"spec": spec, "callouts": load(part, "callouts")}
    events = list(run(os.path.join(img_dir, image), max_iters=3, client=client, start=start))
    first = next(e for e in events if e["event"] == "checks")
    sources = {i["source"] for i in first["issues"]}
    detected = ("both" if {"callout", "critic"} <= sources else "callout" if "callout" in sources
                else "critic" if "critic" in sources else "compile" if "compile" in sources else "none")
    done = events[-1]
    s = score(gt_key, done["spec"])
    rec = {"error": name, "part": part, "description": desc, "run": run_no, "detected_by": detected,
           "first_issues": first["issues"], "fixed": bool(s["perfect"]), "status": done["status"],
           "corrections": done["iterations"], "cost_usd": done["cost_usd"], "seconds": done["seconds"],
           "changelogs": [e["changelog"] for e in events if e["event"] == "spec" and e["iteration"] > 0],
           "final_score": {k: s.get(k) for k in ("iou", "dims_ok", "failed_dims", "errors")}, "events": events}
    with open(os.path.join(outdir, f"{name}.run{run_no}.json"), "w") as f:
        json.dump(rec, f, indent=1)
    return rec


def false_alarm(part, run_no, client, outdir):
    gt_key, img_dir, image = PARTS[part]
    t0 = time.time()
    _, checks, issues = check(load(part, "spec"), load(part, "callouts"), os.path.join(img_dir, image), client)
    rec = {"part": part, "run": run_no, "clean": not issues, "issues": issues, "critic": checks["critic"],
           "critic_call": checks["critic_call"], "seconds": round(time.time() - t0, 1)}
    with open(os.path.join(outdir, f"false_alarm_{part}.run{run_no}.json"), "w") as f:
        json.dump(rec, f, indent=1)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--only", help="comma-separated error names")
    ap.add_argument("--parts", help="comma-separated parts for the false-alarm check (default: all known)")
    ap.add_argument("--false-alarms-only", action="store_true")
    ap.add_argument("--no-false-alarms", action="store_true")
    ap.add_argument("--max-usd", type=float, default=0.5)
    ap.add_argument("--model", default=meta_api.DEFAULT_MODEL)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    budget = meta_api.Budget(args.max_usd)
    client = meta_api.Client(args.model, budget=budget)
    outdir = os.path.join(HERE, "results", args.model)
    os.makedirs(outdir, exist_ok=True)
    lock = threading.Lock()
    jobs = []
    if not args.false_alarms_only:
        names = args.only.split(",") if args.only else list(ERRORS)
        jobs += [("case", n, r) for n in names for r in range(1, args.runs + 1)]
    if not args.no_false_alarms:
        parts = args.parts.split(",") if args.parts else list(PARTS)
        jobs += [("fa", p, r) for p in parts for r in range(1, args.runs + 1)]

    def do(job):
        kind, name, r = job
        try:
            rec = run_case(name, r, client, outdir) if kind == "case" else false_alarm(name, r, client, outdir)
        except meta_api.ModelError as e:
            rec = {"error" if kind == "case" else "part": name, "run": r, "failed": str(e)}
        with lock:
            if "failed" in rec:
                print(f"{kind} {name} run {r}: FAILED {rec['failed'][:200]}", flush=True)
            elif kind == "case":
                print(f"{name} run {r}: detected by {rec['detected_by']}, fixed={rec['fixed']} ({rec['status']}, "
                      f"{rec['corrections']} corrections, ${rec['cost_usd']:.4f}, {rec['seconds']:.0f}s)", flush=True)
            else:
                print(f"false-alarm {name} run {r}: clean={rec['clean']} "
                      + "; ".join(i["message"] for i in rec["issues"])[:300], flush=True)
        return rec

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(do, jobs))
    print(f"\nspent ${budget.spent:.4f} | results in {outdir}")


if __name__ == "__main__":
    main()
