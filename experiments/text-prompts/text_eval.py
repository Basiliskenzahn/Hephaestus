"""Text prompts, complete to contradictory: read + full loop (dev preset), 1 run each.

  python text_eval.py --max-usd 0.10        # ~12 dev runs
  python text_eval.py --summarize

Expected behaviour:
- complete: the true part (scored on compiled geometry against the benchmark truth);
- vague but sized: a part that builds, honours the sizes given, and marks its own choices "assumed:" in the
  parameter descriptions;
- no size at all: rejected with a request for one (we never guess the scale);
- contradictory or impossible: since Oct 30 never rejected (contradictions never reject): a part that builds, with
  the conflicting numbers flagged (callout conflict) or the reinterpretation stated as an assumption.
"""
import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "experiments", "reading-spike-v2"))

import score as scorer  # noqa: E402

from hephaestus import meta_api  # noqa: E402
from hephaestus.talos import PRESETS, run  # noqa: E402

with open(os.path.join(ROOT, "experiments", "benchmark", "ground_truth.json")) as f:
    scorer.GROUND_TRUTH.update({k: v for k, v in json.load(f).items() if not k.startswith("_")})
RESULTS = os.path.join(HERE, "results")

PROMPTS = [  # name, kind, prompt, truth key (complete) or sizes that must appear (vague)
    ("plate", "complete", "80×50×6 plate, four Ø6 holes 8 mm from each edge, Ø20 centre bore", "01_plate"),
    ("block", "complete", "A 70 x 40 x 20 mm block with R5 vertical corners and a 1 mm chamfer on the top edges, a Ø12 "
                          "through hole in the centre and two Ø6 through holes 46 mm apart on the long centre line",
     "03_block"),
    ("l_bracket", "complete", "L-bracket 60 mm wide, base and upright both 40 mm, 5 mm thick, upright at the back. Two Ø5 "
                              "through holes in the upright, 15 mm from the left end and 30 mm apart, 28 mm above the "
                              "bottom. A 20 x 6 slot through the base, centred left to right, 17.5 mm from the front, "
                              "running along the width", "02_l_bracket"),
    ("bushing", "complete", "Flanged bushing on a vertical axis: Ø40 x 5 mm flange at the bottom, Ø24 body, 30 mm total "
                            "height, Ø16 bore through, four Ø4 holes through the flange on a Ø32 bolt circle",
     "04_bushing"),
    ("pocket_plate", "complete", "100 x 60 x 10 mm plate with a centred 50 x 30 pocket, 4 mm deep, R3 inside corners, and "
                                 "four Ø6.6 through holes 10 mm from the edges with Ø11 x 6.4 mm deep counterbores",
     "07_pocket_plate"),
    ("shaft_collar", "complete", "Split shaft collar: a Ø40 x 15 mm ring with a Ø20 bore, a 2 mm wide slit through the "
                                 "wall on the right side, and a Ø4 clamp screw hole across the slit, drilled front to "
                                 "back, 35 mm from the left, at mid height", "08_shaft_collar"),
    ("pipe_bracket", "vague", "a small bracket for a 20 mm pipe", [20]),
    ("nema17", "vague", "a mounting plate for a NEMA 17 stepper motor", [31]),
    ("spacer", "vague", "a spacer for M5 screws, 10 mm long", [10]),
    ("washer", "no_size", "a simple washer", None),
    ("cube_hole", "contradictory", "a 50 mm cube with a 60 mm hole through it", None),
    ("edge_holes", "contradictory", "a 40 x 40 x 5 plate with four Ø10 holes 2 mm from each edge", None),
]


PROMPTS_BY_NAME = [(n, truth) for n, _, _, truth in PROMPTS]


def evaluate(name, kind, prompt, truth, events):
    done = events[-1]
    spec = done.get("spec")
    rec = {"name": name, "kind": kind, "prompt": prompt, "status": done.get("status"), "note": done.get("note"),
           "cost_usd": done.get("cost_usd"), "seconds": done.get("seconds"), "corrections": done.get("iterations")}
    if kind == "complete":
        s = scorer.score(truth, spec) if isinstance(spec, dict) else {"perfect": False, "errors": ["no spec"]}
        rec["ok"] = bool(s.get("perfect"))
        rec["detail"] = ", ".join(d["label"] for d in (s.get("failed_dims") or [])) or "; ".join(s.get("errors") or [])
    elif kind == "vague":
        params = (spec or {}).get("parameters", {}) if isinstance(spec, dict) else {}
        values = [p.get("value") for p in params.values() if isinstance(p.get("value"), (int, float))]
        assumed = [k for k, p in params.items() if "assumed" in (p.get("description") or "").lower()]
        has_sizes = all(any(abs(v - t) < 1.5 for v in values) for t in truth)
        rec["ok"] = done.get("status") not in ("rejected", "error") and bool(assumed) and has_sizes
        rec["detail"] = f"{len(assumed)}/{len(params)} parameters marked assumed; given sizes kept: {has_sizes}"
    elif kind == "no_size":
        rec["ok"] = done.get("status") == "rejected" and bool(done.get("note"))
        rec["detail"] = (done.get("note") or "")[:160]
    else:
        read = next((e for e in events if e["event"] == "spec"), {})
        conflicts = [c["label"] for c in read.get("callouts") or [] if c.get("conflict")]
        desc = ((spec or {}).get("description") or "") if isinstance(spec, dict) else ""
        stated = bool(re.search(r"assum|cannot|can't|instead|to fit", desc, re.I))
        rec["ok"] = done.get("status") not in ("rejected", "error") and bool(conflicts or stated)
        rec["detail"] = (f"conflict flagged on {', '.join(conflicts)}; " if conflicts else "") + desc[:140]
    return rec


def summarize():
    recs = [json.load(open(os.path.join(RESULTS, f"{n}.json"))) for n, *_ in PROMPTS
            if os.path.exists(os.path.join(RESULTS, f"{n}.json"))]
    lines = ["| prompt | kind | expected | status | result | detail | cost |", "|---|---|---|---|---|---|---|"]
    expect = {"complete": "the true part", "vague": "a part + assumptions", "no_size": "ask for a size",
              "contradictory": "best guess + conflict flagged"}
    for r in recs:
        lines.append(f"| {r['prompt'][:70]} | {r['kind']} | {expect[r['kind']]} | {r['status']} | "
                     f"{'ok' if r['ok'] else '**fail**'} | {r['detail'][:140]} | ${r['cost_usd'] or 0:.4f} |")
    lines.append(f"\n{sum(r['ok'] for r in recs)}/{len(recs)} as expected; cost ${sum(r['cost_usd'] or 0 for r in recs):.3f}.")
    text = "\n".join(lines)
    with open(os.path.join(RESULTS, "summary.md"), "w") as f:
        f.write(text + "\n")
    print(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-usd", type=float, default=0.10)
    ap.add_argument("--only")
    ap.add_argument("--summarize", action="store_true")
    args = ap.parse_args()
    if args.summarize:
        return summarize()
    os.environ.setdefault("SPEND_SCRIPT", "experiments/text-prompts/text_eval.py")
    budget = meta_api.Budget(args.max_usd)
    client = meta_api.Client(PRESETS["dev"].read_model, budget=budget)
    os.makedirs(RESULTS, exist_ok=True)

    def go(item):
        name, kind, prompt, truth = item
        if args.only and name not in args.only.split(","):
            return
        t0 = time.time()
        try:
            events = list(run(prompt=prompt, client=client, settings=PRESETS["dev"], max_iters=3))
        except Exception as e:  # noqa: BLE001 - a crash is a result here
            events = [{"event": "done", "status": "crash", "note": f"{type(e).__name__}: {e}", "spec": None}]
        rec = evaluate(name, kind, prompt, truth, events)
        rec["events"] = events
        with open(os.path.join(RESULTS, f"{name}.json"), "w") as f:
            json.dump(rec, f, indent=1)
        print(f"{name}: {rec['status']} ok={rec['ok']} {rec['detail'][:120]} ({time.time() - t0:.0f} s)", flush=True)

    with ThreadPoolExecutor(4) as pool:
        list(pool.map(go, PROMPTS))
    print(f"spent ${budget.spent:.4f}")
    summarize()


if __name__ == "__main__":
    main()
