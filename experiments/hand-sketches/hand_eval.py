"""Read + full Talos loop on the hand sketches (dev preset), scored on compiled geometry against the benchmark truth.

  python hand_eval.py --max-usd 0.25 [--only 01_plate__photo]
  python hand_eval.py --summarize

Per run: read perfect, final perfect, stop status, failure type, cost, and shape-check false alarms: a blocking
shape issue on an iteration whose spec is perfect (the drawing is not to scale, so the check must stay quiet).
"""
import argparse
import glob
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "experiments", "reading-spike-v2"))

import score as scorer  # noqa: E402

from hephaestus import meta_api, sam  # noqa: E402
from hephaestus.talos import PRESETS, run  # noqa: E402

with open(os.path.join(ROOT, "experiments", "benchmark", "ground_truth.json")) as f:
    scorer.GROUND_TRUTH.update({k: v for k, v in json.load(f).items() if not k.startswith("_")})
RESULTS = os.path.join(HERE, "results")


def safe_score(part, spec):
    if not isinstance(spec, dict):
        return {"perfect": False, "compiles": False, "errors": ["no spec"]}
    s = scorer.score(part, spec)
    return {k: s.get(k) for k in ("perfect", "compiles", "iou", "dims_ok", "failed_dims", "errors")}


def one(image, client, seg):
    name = os.path.basename(image).rsplit(".", 1)[0]
    part = name.split("__")[0]
    t0 = time.time()
    events = list(run(image, client=client, settings=PRESETS["dev"], max_iters=3, segmenter=seg))
    specs = [e for e in events if e["event"] == "spec"]
    shapes = {e["iteration"]: e for e in events if e["event"] == "shape_check"}
    done = events[-1]
    false_alarms, shape_avail = [], []
    for s in specs:
        sc = shapes.get(s["iteration"])
        if sc is None:
            continue
        shape_avail.append(sc.get("available"))
        if sc.get("issues") and safe_score(part, s["spec"])["perfect"]:
            false_alarms.append({"iteration": s["iteration"], "issues": [i["message"] for i in sc["issues"]]})
    rec = {"name": name, "part": part, "status": done.get("status"), "note": done.get("note"),
           "read": safe_score(part, specs[0]["spec"]) if specs else None, "final": safe_score(part, done.get("spec")),
           "corrections": done.get("iterations"), "cost_usd": done.get("cost_usd"), "seconds": round(time.time() - t0),
           "first_issues": next(([i["message"] for i in e["issues"]] for e in events if e["event"] == "checks"), []),
           "shape_available": shape_avail, "shape_false_alarms": false_alarms, "events": events}
    os.makedirs(RESULTS, exist_ok=True)
    with open(os.path.join(RESULTS, f"{name}.json"), "w") as f:
        json.dump(rec, f, indent=1)
    return rec


def summarize():
    recs = [json.load(open(p)) for p in sorted(glob.glob(os.path.join(RESULTS, "*__*.json")) +
                                               glob.glob(os.path.join(RESULTS, "0*.json")))]
    recs = {r["name"]: r for r in recs}.values()
    lines = ["| sketch | read perfect | after loop | status | corrections | failed dimensions / errors | shape check | cost |",
             "|---|---|---|---|---|---|---|---|"]
    for r in sorted(recs, key=lambda r: r["name"]):
        fails = r["final"].get("failed_dims") or r["final"].get("errors") or []
        fails = ", ".join(d["label"] if isinstance(d, dict) else str(d)[:60] for d in fails) or "-"
        shape = ("FALSE ALARM" if r["shape_false_alarms"] else
                 "ok" if any(r["shape_available"]) else "unavailable")
        lines.append(f"| {r['name']} | {'yes' if r['read'] and r['read']['perfect'] else 'no'} | "
                     f"{'yes' if r['final']['perfect'] else 'no'} | {r['status']} | {r['corrections']} | {fails} | "
                     f"{shape} | ${r['cost_usd']:.4f} |")
    recs = list(recs)
    n = len(recs)
    lines += ["", f"Perfect: read {sum(bool(r['read'] and r['read']['perfect']) for r in recs)}/{n}, after the loop "
                  f"{sum(r['final']['perfect'] for r in recs)}/{n}. Shape-check false alarms (blocking issue on a "
                  f"perfect spec): {sum(bool(r['shape_false_alarms']) for r in recs)}/{n}; shape check available on "
                  f"{sum(any(r['shape_available']) for r in recs)}/{n}. Cost ${sum(r['cost_usd'] or 0 for r in recs):.3f}."]
    text = "\n".join(lines)
    with open(os.path.join(RESULTS, "summary.md"), "w") as f:
        f.write(text + "\n")
    print(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-usd", type=float, default=0.25)
    ap.add_argument("--only")
    ap.add_argument("--summarize", action="store_true")
    args = ap.parse_args()
    if args.summarize:
        return summarize()
    os.environ.setdefault("SPEND_SCRIPT", "experiments/hand-sketches/hand_eval.py")
    budget = meta_api.Budget(args.max_usd)
    client = meta_api.Client(PRESETS["dev"].read_model, budget=budget)
    images = sorted(glob.glob(os.path.join(HERE, "sketches", "*.png")) + glob.glob(os.path.join(HERE, "sketches", "*.jpg")))
    if args.only:
        images = [i for i in images if os.path.basename(i).rsplit(".", 1)[0] in args.only.split(",")]

    def go(img):
        try:
            r = one(img, client, sam.Segmenter(budget=budget))
            print(f"{r['name']}: read {r['read']['perfect']} final {r['final']['perfect']} {r['status']} "
                  f"${r['cost_usd']:.4f} {r['seconds']} s false alarms {len(r['shape_false_alarms'])}", flush=True)
        except Exception as e:  # noqa: BLE001 - keep the batch going
            print(f"{os.path.basename(img)}: FAILED {type(e).__name__}: {e}"[:300], flush=True)

    with ThreadPoolExecutor(4) as pool:
        list(pool.map(go, images))
    print(f"spent ${budget.spent:.4f}")
    summarize()


if __name__ == "__main__":
    main()
