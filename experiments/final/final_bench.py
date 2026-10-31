"""Final benchmark of the shipping product: live preset, every check on, 1 run each (Nov 2026).

  python final_bench.py --batch printed --max-usd 2.35     # 8 parts x clean / photo / rough
  python final_bench.py --batch hand --max-usd 1.70        # 8 hand sketches x scan / photo
  python final_bench.py --batch text --max-usd 0.72        # the 12 text prompts
  python final_bench.py --batch injected --max-usd 0.65    # 10 injected errors + the 8 correct parts
  python final_bench.py --summarize                        # results/summary.md from the saved runs, no API calls

Per run: the full event stream (results/<batch>/<name>.json) and
- perfect read (the first spec), perfect after the loop (the returned spec), perfect after one tap per flagged
  number (each "confirm?"/red number set to its second reading or to a true value, whichever makes the part
  right; the user's tap, simulated with the ground truth; no model call);
- flags shown: soft ("confirm?") and red (another check backs the other value), as the UI shows them
  (reread.strength); flags on numbers the read got right are false flags;
- time to the first part (first compiled event) and to the verified part (done, if pass), cost (all calls: read,
  re-read, SAM, critic, corrections).
Scored on compiled geometry with the reading-spike-v2 scorer, against the benchmark ground truth. SAM is called
fresh for every run (no shared cache), as on the server.
"""
import argparse
import copy
import glob
import importlib.util
import json
import os
import statistics as st
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "experiments", "reading-spike-v2"))
sys.path.insert(0, os.path.join(ROOT, "experiments", "loop-eval"))
sys.path.insert(0, os.path.join(ROOT, "experiments", "text-prompts"))

import score as scorer  # noqa: E402

from hephaestus import meta_api, reread, sam  # noqa: E402
from hephaestus.talos import PRESETS, run  # noqa: E402

with open(os.path.join(ROOT, "experiments", "benchmark", "ground_truth.json")) as f:
    scorer.GROUND_TRUTH.update({k: v for k, v in json.load(f).items() if not k.startswith("_")})
import loop_eval  # noqa: E402  (after the ground truth is complete)
import text_eval  # noqa: E402


def _load_shape_eval():
    spec = importlib.util.spec_from_file_location("shape_eval", os.path.join(ROOT, "experiments", "shape-check", "eval.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RESULTS = os.path.join(HERE, "results")
SETTINGS = PRESETS["live"]
PARTS = sorted(k for k in scorer.GROUND_TRUTH)                       # 01_plate ... 08_shaft_collar
SHORT = {k: k.split("_", 1)[1] for k in PARTS}
PRINTED_DIR = {k: os.path.join(ROOT, "experiments", "reading-spike" if k[:2] in ("01", "02", "03") else "benchmark",
                               "sketches") for k in PARTS}
HAND_DIR = os.path.join(ROOT, "experiments", "hand-sketches", "sketches")
WRITTEN_EXTRA = {"07_pocket_plate": [80, 40]}     # written on the drawings, expressed as insets in the true callouts


def true_values(key):
    with open(os.path.join(ROOT, "examples", "callouts", SHORT[key] + ".json")) as f:
        return [c["value"] for c in json.load(f) if isinstance(c.get("value"), (int, float))] + WRITTEN_EXTRA.get(key, [])


def perfect(key, spec):
    if not isinstance(spec, dict):
        return False
    try:
        return bool(scorer.score(key, spec).get("perfect"))
    except Exception:  # noqa: BLE001 - a spec that breaks the scorer is not perfect
        return False


def failed(key, spec):
    if not isinstance(spec, dict):
        return "no part"
    try:
        s = scorer.score(key, spec)
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}"
    return ", ".join(d["label"] for d in (s.get("failed_dims") or [])) or "; ".join(map(str, s.get("errors") or []))[:120]


def after_taps(key, spec, flagged):
    """One tap per flagged number: its second reading or a true value, whichever makes the part right."""
    if not isinstance(spec, dict):
        return False
    spec = copy.deepcopy(spec)
    for n in flagged:
        p = n.get("parameter")
        if not p or not isinstance(spec.get("parameters", {}).get(p, {}).get("value"), (int, float)):
            continue
        for v in [n.get("second"), *sorted(set(true_values(key)))]:
            if v is None:
                continue
            trial = copy.deepcopy(spec)
            trial["parameters"][p]["value"] = v
            if perfect(key, trial):
                spec = trial
                break
    return perfect(key, spec)


def flags(events, key):
    """The number chips as the UI shows them: soft / red, and which of them flag a number the read got right."""
    nums = next((e for e in events if e["event"] == "numbers"), None)
    if not nums or not nums.get("available"):
        return {"available": False, "soft": 0, "red": 0, "false": 0, "caught": 0, "flagged": []}
    spec0 = next((e for e in events if e["event"] == "spec" and e["iteration"] == 0), {})
    checks0 = next((e for e in events if e["event"] == "checks" and e["iteration"] == 0), {})
    shape0 = next((e for e in events if e["event"] == "shape_check" and e["iteration"] == 0), None)
    truth = true_values(key) if key else []
    out = {"available": True, "soft": 0, "red": 0, "false": 0, "caught": 0, "flagged": [],
           "seconds": nums.get("seconds"), "cost_usd": nums.get("cost_usd")}
    for n in nums["numbers"]:
        s = reread.strength(n, spec0.get("callouts"), checks0.get("callouts"), shape0)
        if not s:
            continue
        out[s] += 1
        wrong = isinstance(n.get("value"), (int, float)) and not any(abs(n["value"] - t) < 1e-6 for t in truth)
        out["caught" if wrong else "false"] += 1
        out["flagged"].append({**n, "strength": s, "misread": wrong})
    return out


def timing(events):
    first = next((e["t"] for e in events if e["event"] == "compiled" and e.get("ok")), None)
    done = events[-1]
    return first, (done.get("seconds") if done.get("status") in ("pass", "unverified") else None)


def save(batch, name, rec):
    d = os.path.join(RESULTS, batch)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{name}.json"), "w") as f:
        json.dump(rec, f, indent=1)


def image_run(batch, name, key, image, client, budget):
    seg = sam.Segmenter(budget=budget, cache_dir=os.path.join(RESULTS, "sam", name))
    events = list(run(image, client=client, settings=SETTINGS, segmenter=seg))
    specs = [e for e in events if e["event"] == "spec"]
    done = events[-1]
    fl = flags(events, key)
    first, verified = timing(events)
    rec = {"name": name, "batch": batch, "key": key, "image": os.path.relpath(image, ROOT), "status": done["status"],
           "note": done.get("note"), "corrections": done.get("iterations"),
           "read_perfect": perfect(key, specs[0]["spec"]) if specs else False,
           "final_perfect": perfect(key, done.get("spec")), "failed": failed(key, done.get("spec")),
           "taps_perfect": after_taps(key, done.get("spec"), fl["flagged"]), "flags": fl,
           "first_part_s": first, "verified_s": verified, "seconds": done.get("seconds"), "cost_usd": done.get("cost_usd"),
           "events": events}
    save(batch, name, rec)
    return rec


def jobs(batch):
    if batch == "printed":
        return [(f"{k}__{v}", k, os.path.join(PRINTED_DIR[k], scorer.GROUND_TRUTH[k]["images"][v]))
                for v in ("clean", "photo", "rough") for k in PARTS]
    if batch == "hand":
        return [(f"{k}__{v}", k, os.path.join(HAND_DIR, f"{k}.png" if v == "scan" else f"{k}__photo.jpg"))
                for v in ("scan", "photo") for k in PARTS]
    if batch == "text":
        return list(text_eval.PROMPTS)
    if batch == "injected":
        shape_eval = _load_shape_eval()
        errs = [(n, p) for n, (p, _, _) in shape_eval.ERRORS.items()]
        return [("error", n, p) for n, p in errs] + [("correct", None, SHORT[k]) for k in PARTS]
    raise ValueError(batch)


def text_run(item, client):
    name, kind, prompt, truth = item
    events = list(run(prompt=prompt, client=client, settings=SETTINGS))
    rec = text_eval.evaluate(name, kind, prompt, truth, events)
    first, verified = timing(events)
    rec.update(batch="text", first_part_s=first, verified_s=verified, events=events,
               read_perfect=(perfect(truth, next((e["spec"] for e in events if e["event"] == "spec"), None))
                             if kind == "complete" else None))
    save("text", name, rec)
    return rec


def injected_run(item, client, budget):
    kind, err, part = item
    shape_eval = _load_shape_eval()
    key = next(k for k in PARTS if SHORT[k] == part)
    with open(os.path.join(ROOT, "examples", f"{part}.json")) as f:
        spec = json.load(f)
    if kind == "error":
        shape_eval.ERRORS[err][2](spec)
    with open(os.path.join(ROOT, "examples", "callouts", f"{part}.json")) as f:
        callouts = json.load(f)
    with open(os.path.join(ROOT, "experiments", "shape-check", "grounding", f"{key}.json")) as f:
        views = json.load(f)["views"]
    image = os.path.join(PRINTED_DIR[key], scorer.GROUND_TRUTH[key]["images"]["clean"])
    name = err or f"correct_{part}"
    seg = sam.Segmenter(budget=budget, cache_dir=os.path.join(RESULTS, "sam", name))
    events = list(run(image, client=client, settings=SETTINGS, segmenter=seg,
                      start={"spec": spec, "callouts": callouts, "views": views}))
    first = next(e for e in events if e["event"] == "checks")
    shape = next((e for e in events if e["event"] == "shape_check"), {})
    sources = sorted({i["source"] for i in first["issues"]})
    done = events[-1]
    rec = {"name": name, "batch": "injected", "kind": kind, "part": part, "key": key,
           "description": shape_eval.ERRORS[err][1] if err else "correct part",
           "caught_by": sources, "first_issues": first["issues"], "shape_available": shape.get("available"),
           "fixed": perfect(key, done.get("spec")), "status": done["status"], "corrections": done.get("iterations"),
           "false_alarm": bool(first["issues"]) if kind == "correct" else None,
           "seconds": done.get("seconds"), "cost_usd": done.get("cost_usd"), "events": events}
    save("injected", name, rec)
    return rec


# ------------------------------------------------------------------ summary
def _med(xs):
    xs = [x for x in xs if x is not None]
    return f"{st.median(xs):.0f} s" if xs else "-"


def _p90(xs):
    xs = sorted(x for x in xs if x is not None)
    return f"{xs[min(len(xs) - 1, int(round(0.9 * (len(xs) - 1))))]:.0f} s" if xs else "-"


def load(batch):
    return [json.load(open(p)) for p in sorted(glob.glob(os.path.join(RESULTS, batch, "*.json")))]


def summarize():
    lines = ["# Final benchmark (live preset, 1 run each)", ""]
    groups = [("printed", "clean", "printed drawing, clean"), ("printed", "photo", "printed drawing, phone photo"),
              ("printed", "rough", "printed drawing, rough pencil photo"), ("hand", "scan", "hand sketch, scan"),
              ("hand", "photo", "hand sketch, phone photo")]
    lines += ["| input | runs | perfect read | perfect after loop | perfect after one tap per flagged number | "
              "flags per run (soft / red) | false flags | first part (median / p90) | verified part (median / p90) | "
              "cost per run |", "|---|---|---|---|---|---|---|---|---|---|"]
    every = []
    for batch, variant, label in groups:
        rs = [r for r in load(batch) if r["name"].endswith("__" + variant)]
        if not rs:
            continue
        every += rs
        n = len(rs)
        lines.append(
            f"| {label} | {n} | {sum(r['read_perfect'] for r in rs)}/{n} | {sum(r['final_perfect'] for r in rs)}/{n} | "
            f"{sum(r['taps_perfect'] for r in rs)}/{n} | {sum(r['flags']['soft'] for r in rs) / n:.1f} / "
            f"{sum(r['flags']['red'] for r in rs) / n:.1f} | {sum(r['flags']['false'] for r in rs)} | "
            f"{_med(r['first_part_s'] for r in rs)} / {_p90(r['first_part_s'] for r in rs)} | "
            f"{_med(r['verified_s'] for r in rs)} / {_p90(r['verified_s'] for r in rs)} | "
            f"${st.mean(r['cost_usd'] or 0 for r in rs):.3f} |")
    if every:
        lines += ["", "Per run:", "", "| run | status | read | loop | taps | flags (soft/red; caught/false) | failed (after loop) | "
                  "first part | verified | cost |", "|---|---|---|---|---|---|---|---|---|---|"]
        for r in every:
            fl = r["flags"]
            lines.append(f"| {r['name']} | {r['status']} | {'✓' if r['read_perfect'] else '✗'} | "
                         f"{'✓' if r['final_perfect'] else '✗'} | {'✓' if r['taps_perfect'] else '✗'} | "
                         f"{fl['soft']}/{fl['red']}; {fl['caught']}/{fl['false']} | {r['failed'] or '-'} | "
                         f"{r['first_part_s'] or '-'} | {r['verified_s'] or '-'} | ${r['cost_usd'] or 0:.3f} |")
        rr = [r["flags"].get("seconds") for r in every if r["flags"].get("seconds")]
        rc = [r["flags"].get("cost_usd") for r in every if r["flags"].get("cost_usd")]
        if rr:
            lines += ["", f"Number re-read: median {st.median(rr):.1f} s, ${st.mean(rc):.4f} per run (in parallel "
                          "with the first compile and checks)."]
    tr = load("text")
    if tr:
        lines += ["", "## Text prompts", "", "| prompt | kind | status | as expected | detail | first part | cost |",
                  "|---|---|---|---|---|---|---|"]
        for r in tr:
            lines.append(f"| {r['prompt'][:60]} | {r['kind']} | {r['status']} | {'yes' if r['ok'] else '**no**'} | "
                         f"{(r['detail'] or '')[:110]} | {r['first_part_s'] or '-'} | ${r['cost_usd'] or 0:.3f} |")
        lines.append(f"\n{sum(r['ok'] for r in tr)}/{len(tr)} as expected.")
    ir = load("injected")
    if ir:
        lines += ["", "## Injected errors (all checks on)", "", "| error | part | caught by | fixed | corrections | status | cost |",
                  "|---|---|---|---|---|---|---|"]
        for r in [r for r in ir if r["kind"] == "error"]:
            lines.append(f"| {r['description']} | {r['part']} | {', '.join(r['caught_by']) or '**none**'} | "
                         f"{'yes' if r['fixed'] else 'no'} | {r['corrections']} | {r['status']} | ${r['cost_usd'] or 0:.3f} |")
        cs = [r for r in ir if r["kind"] == "correct"]
        lines += ["", f"False alarms on the 8 correct parts: {sum(r['false_alarm'] for r in cs)}/{len(cs)}"]
        lines += [f"- {r['part']}: " + "; ".join(i["message"][:140] for i in r["first_issues"]) for r in cs if r["false_alarm"]]
    allr = load("printed") + load("hand") + tr + ir
    lines += ["", f"Total: {len(allr)} runs, ${sum(r.get('cost_usd') or 0 for r in allr):.2f}."]
    text = "\n".join(lines)
    with open(os.path.join(RESULTS, "summary.md"), "w") as f:
        f.write(text + "\n")
    print(text)


def rescore_text():
    """Re-evaluate saved text runs with the current text_eval expectations (no API calls)."""
    for path in glob.glob(os.path.join(RESULTS, "text", "*.json")):
        r = json.load(open(path))
        r.update({k: v for k, v in text_eval.evaluate(r["name"], r["kind"], r["prompt"],
                                                        dict(text_eval.PROMPTS_BY_NAME)[r["name"]], r["events"]).items()
                  if k in ("ok", "detail")})
        json.dump(r, open(path, "w"), indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", choices=["printed", "hand", "text", "injected"])
    ap.add_argument("--max-usd", type=float, default=0.5)
    ap.add_argument("--only", help="comma-separated run names")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--summarize", action="store_true")
    args = ap.parse_args()
    if args.summarize:
        rescore_text()
        return summarize()
    os.environ.setdefault("SPEND_SCRIPT", f"experiments/final/final_bench.py --batch {args.batch}")
    budget = meta_api.Budget(args.max_usd)
    client = meta_api.Client(SETTINGS.read_model, budget=budget)
    lock = threading.Lock()
    items = jobs(args.batch)

    def name_of(it):
        return it[0] if args.batch in ("printed", "hand", "text") else (it[1] or f"correct_{it[2]}")
    if args.only:
        items = [it for it in items if name_of(it) in args.only.split(",")]
    # runs already saved are kept (1 run each): a stopped batch resumes where it was
    items = [it for it in items if not os.path.exists(os.path.join(RESULTS, args.batch, f"{name_of(it)}.json"))]

    def go(it):
        t0 = time.time()
        try:
            if args.batch in ("printed", "hand"):
                r = image_run(args.batch, it[0], it[1], it[2], client, budget)
                msg = (f"{r['status']} read {r['read_perfect']} loop {r['final_perfect']} taps {r['taps_perfect']} "
                       f"flags {r['flags']['soft']}/{r['flags']['red']} first {r['first_part_s']} s")
            elif args.batch == "text":
                r = text_run(it, client)
                msg = f"{r['status']} ok={r['ok']} {r['detail'][:80]}"
            else:
                r = injected_run(it, client, budget)
                msg = f"caught by {r['caught_by']} fixed={r['fixed']} {r['status']}"
            msg += f" ${r['cost_usd'] or 0:.3f} {time.time() - t0:.0f} s"
        except Exception as e:  # noqa: BLE001 - keep the batch going; the run is reported as failed
            msg = f"FAILED {type(e).__name__}: {str(e)[:200]}"
        with lock:
            print(f"{name_of(it)}: {msg} | spent ${budget.spent:.3f}", flush=True)

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(go, items))
    print(f"\nbatch {args.batch}: spent ${budget.spent:.4f}")


if __name__ == "__main__":
    main()
