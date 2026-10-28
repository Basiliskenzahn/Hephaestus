"""Shape check evaluation: false alarms on the correct parts, catches on injected errors. No new reads.

  python eval.py --max-usd 0.30            # grounding + SAM calls, all cached (grounding/, experiments/sam_cache/)
  python eval.py --offline                 # re-score from the caches only (threshold tuning)

8 benchmark parts x 3 inputs (clean drawing, phone photo, rough hand sketch). Per image:
1. view boxes: one dev-preset call with VIEWS_PROMPT (in the product they come with the read), cached;
2. the sheet and the view outlines (hephaestus.shape_check.InputShape), SAM responses cached;
3. compare with the ground-truth spec (must pass) and with every injected-error spec of that part.
Writes results/results.json, results/summary.md and overlays to out/<image>/.
"""
import argparse
import copy
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "experiments", "loop-eval"))

from hephaestus import meta_api, sam, shape_check  # noqa: E402
from hephaestus.compile import compile  # noqa: E402
from hephaestus.prompts import VIEWS_PROMPT  # noqa: E402
from hephaestus.spec import ViewsOutput, json_schema  # noqa: E402
from hephaestus.talos import PRESETS  # noqa: E402
from loop_eval import ERRORS as LOOP_ERRORS  # noqa: E402

PARTS = {"01_plate": "plate", "02_l_bracket": "l_bracket", "03_block": "block", "04_bushing": "bushing",
         "05_enclosure": "enclosure", "06_angled_bracket": "angled_bracket", "07_pocket_plate": "pocket_plate",
         "08_shaft_collar": "shaft_collar"}
VARIANTS = ("clean", "photo", "rough")
GROUNDING = os.path.join(HERE, "grounding")
RESULTS = os.path.join(HERE, "results")
OUT = os.path.join(HERE, "out")


def _mirror_profile(s):
    body = next(f for f in s["features"] if f["id"] == "body")
    body["profile"]["points"] = [[0, 0], ["length", 0], ["length", "height"], ["length - flat_len", "height"],
                                 [0, "low_h"]]


def _holes_opposite(s):
    next(f for f in s["features"] if f["id"] == "top_hole")["center"][0] = "length - hole_x"


ERRORS = {k: (part, desc, fn) for k, (part, desc, fn) in LOOP_ERRORS.items()}
ERRORS.update({
    "mirror_profile": ("angled_bracket", "slope profile mirrored (flat top at the right)", _mirror_profile),
    "holes_opposite": ("angled_bracket", "vertical hole pair moved to the right end", _holes_opposite),
})


def image_path(part, variant):
    d = os.path.join(ROOT, "experiments", "reading-spike" if part[:2] in ("01", "02", "03") else "benchmark",
                     "sketches")
    return os.path.join(d, part + (".png" if variant == "clean" else f"__{variant}.jpg"))


def load_spec(name):
    with open(os.path.join(ROOT, "examples", name + ".json")) as f:
        return json.load(f)


def ground(path, client, offline):
    """View boxes for an image (cached): one dev-preset call."""
    cache = os.path.join(GROUNDING, os.path.basename(path).rsplit(".", 1)[0] + ".json")
    if os.path.exists(cache):
        with open(cache) as f:
            return json.load(f)
    if offline:
        raise RuntimeError(f"no cached grounding for {path}")
    s = PRESETS["dev"]
    out, info = client.respond(VIEWS_PROMPT, [meta_api.text("Locate the views on this drawing."),
                                              meta_api.image(path)],
                               json_schema(ViewsOutput), "ViewsOutput", model=s.read_model, effort="low")
    rec = {"views": out.get("views") or [], "cost_usd": info["cost_usd"], "seconds": info["seconds"],
           "model": info.get("model")}
    os.makedirs(GROUNDING, exist_ok=True)
    with open(cache, "w") as f:
        json.dump(rec, f, indent=1)
    return rec


def prepare(part, variant, client, seg, offline):
    path = image_path(part, variant)
    g = ground(path, client, offline)
    out_dir = os.path.join(OUT, f"{part}__{variant}")
    t0 = time.time()
    inp = shape_check.InputShape(path, seg, out_dir=out_dir).start_sheet()
    inp.start_views(g["views"])
    try:
        inp.wait(120)
    except shape_check.Unavailable:
        pass
    return inp, g, round(time.time() - t0, 2)


def case(inp, result, out_dir):
    r = shape_check.compare(inp, result, out_dir)
    return {"available": r["available"], "note": r["note"], "issues": [i["message"] for i in r["issues"]],
            "advisories": r["advisories"],
            "views": [{k: v.get(k) for k in ("view", "status", "orientation", "orientation_gap", "aspect", "holes",
                                              "note")} for v in r["views"]]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-usd", type=float, default=0.30)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--parts", default=",".join(PARTS))
    ap.add_argument("--variants", default=",".join(VARIANTS))
    args = ap.parse_args()
    shape_check.HOLE_CHECK = True          # off in the product; evaluated here
    os.environ.setdefault("SPEND_SCRIPT", "experiments/shape-check/eval.py")
    budget = meta_api.Budget(args.max_usd)
    client = meta_api.Client(PRESETS["dev"].read_model, budget=budget)
    parts = [p for p in PARTS if p in args.parts.split(",")]
    variants = args.variants.split(",")

    compiled = {}
    for part in parts:
        name = PARTS[part]
        compiled[(part, None)] = compile(load_spec(name))
        for err, (p, _, fn) in ERRORS.items():
            if p == name:
                s = load_spec(name)
                fn(s)
                compiled[(part, err)] = compile(s)

    jobs = [(p, v) for p in parts for v in variants]
    segs = {j: sam.Segmenter(budget=budget, offline=args.offline) for j in jobs}
    with ThreadPoolExecutor(4) as ex:
        prepared = dict(zip(jobs, ex.map(lambda j: prepare(*j, client, segs[j], args.offline), jobs)))

    rows = []
    for (part, variant), (inp, g, secs) in prepared.items():
        out_dir = os.path.join(OUT, f"{part}__{variant}")
        for (p, err), result in compiled.items():
            if p != part:
                continue
            rec = case(inp, result, os.path.join(out_dir, err or "truth"))
            rec.update(part=part, variant=variant, error=err, sheet=inp.sheet, views_read=g["views"],
                       prep_seconds=secs, sam=shape_check._sam(inp))
            rows.append(rec)
            flag = "FLAG" if rec["issues"] else ("adv " if rec["advisories"] else "ok  ")
            print(f"{part:18} {variant:5} {str(err):16} {flag} "
                  + " | ".join(f"{v['view']}:{v['status']} gap={v.get('orientation_gap')} asp={v.get('aspect')}"
                               for v in rec["views"]) + (f"  [{rec['note']}]" if rec["note"] else ""), flush=True)
            for m in rec["issues"] + rec["advisories"]:
                print("      -", m)
    os.makedirs(RESULTS, exist_ok=True)
    with open(os.path.join(RESULTS, "results.json"), "w") as f:
        json.dump(rows, f, indent=1)
    calls = sum(s.calls for s in segs.values())
    cost = sum(s.cost_usd for s in segs.values())
    print(f"\nSAM: {calls} requests, ${cost:.4f} new; budget spent ${budget.spent:.4f}")
    summarize(rows)


def unmatched(rec):
    """Holes without a partner, over all views (drawing vs part)."""
    n = 0
    for v in rec["views"]:
        h = v.get("holes")
        if h:
            _, a, b = shape_check.match_holes(h["drawing"], h["part"])
            n += len(a) + len(b)
    return n


def summarize(rows):
    lines = ["# Shape check eval", "",
             f"Thresholds: orientation margin {shape_check.ORIENT_MARGIN}, aspect tolerance "
             f"{shape_check.ASPECT_TOL}, holes {'blocking' if shape_check.HOLES_BLOCKING else 'advisory'}.", "",
             "## Correct parts (false alarms)", "",
             "| part | " + " | ".join(VARIANTS) + " |", "|---|" + "---|" * len(VARIANTS)]
    truth = [r for r in rows if r["error"] is None]

    def cell(r):
        if r is None:
            return ""
        if not r["available"]:
            return f"n/a ({r['note']})"
        views = ", ".join(f"{v['view']} {v.get('orientation_gap', '')}" for v in r["views"] if v["status"] != "unavailable")
        s = "**FLAG**" if r["issues"] else ("holes adv." if r["advisories"] else "pass")
        return f"{s} ({views})"

    for part in PARTS:
        rs = {r["variant"]: r for r in truth if r["part"] == part}
        if rs:
            lines.append(f"| {part} | " + " | ".join(cell(rs.get(v)) for v in VARIANTS) + " |")
    n = len(truth)
    lines += ["", f"Blocking false alarms: {sum(bool(r['issues']) for r in truth)}/{n}; hole advisories on correct "
                  f"parts: {sum(bool(r['advisories']) for r in truth)}/{n}; unavailable: "
                  f"{sum(not r['available'] for r in truth)}/{n}.", "",
              "## Injected errors (blocking issue = caught)", "",
              "| injected error | part | " + " | ".join(VARIANTS) + " |", "|---|---|" + "---|" * len(VARIANTS)]
    for err, (p, desc, _) in ERRORS.items():
        rs = {r["variant"]: r for r in rows if r["error"] == err}
        if not rs:
            continue

        def c(r):
            if r is None:
                return ""
            if not r["available"]:
                return "n/a"
            if r["issues"]:
                return "**caught**"
            t = next(x for x in truth if x["part"] == r["part"] and x["variant"] == r["variant"])
            d = unmatched(r) - unmatched(t)
            return f"missed (holes: +{d} mismatch)" if d > 0 else "missed"
        lines.append(f"| {desc} | {p} | " + " | ".join(c(rs.get(v)) for v in VARIANTS) + " |")
    gaps_t = [v["orientation_gap"] for r in truth for v in r["views"] if v.get("orientation_gap") is not None]
    flips = ("upright_at_front", "mirror_profile")
    gaps_e = [max(v["orientation_gap"] for v in r["views"] if v.get("orientation_gap") is not None)
              for r in rows if r["error"] in flips]
    asp_t = [max(v["aspect"]["drawing"] / v["aspect"]["part"], v["aspect"]["part"] / v["aspect"]["drawing"])
             for r in truth for v in r["views"] if v.get("aspect")]
    lines += ["", f"Orientation gap (best flip minus as-is IoU): correct parts max {max(gaps_t):.3f}; injected flips "
                  f"min {min(gaps_e):.3f}. Aspect mismatch on correct parts: max {max(asp_t):.2f}x, median "
                  f"{sorted(asp_t)[len(asp_t) // 2]:.2f}x.",
              "Holes: '+n mismatch' = n more unmatched holes than the correct part on the same image (advisory only).",
              "", "## Messages on injected errors", ""]
    for r in rows:
        if r["error"] and (r["issues"] or r["advisories"]):
            for m in r["issues"]:
                lines.append(f"- {r['error']} / {r['variant']}: {m}")
            for m in r["advisories"]:
                lines.append(f"- {r['error']} / {r['variant']} (advisory): {m}")
    with open(os.path.join(RESULTS, "summary.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
