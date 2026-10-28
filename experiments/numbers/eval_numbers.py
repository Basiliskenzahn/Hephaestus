"""Does the independent re-read flag the misread numbers, without flagging the correct ones? (dev preset)

  python eval_numbers.py --max-usd 0.15          # read + re-read per image: 16 hand sketches + 24 benchmark images
  python eval_numbers.py --summarize

Per image: one read (spec + callouts with text boxes), one re-read of every written number.
- misread: a written callout whose value appears nowhere in the part's true callouts (a 40 read as 60, a 5 as 3,
  a crossed-out 10 instead of the 8). A swap of two true numbers (15/10) is not a digit error and cannot be seen
  by this measure, nor by a re-read.
- caught = misread and flagged uncertain; false flag = correct and flagged.
- "perfect after taps": the user taps each flagged number and picks the right value (one of the two readings, or
  types the true one), simulated with the ground truth; the read's spec is re-scored after setting each flagged
  callout's parameter. No model call: the edit sets a parameter and recompiles.
"""
import argparse
import copy
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

from hephaestus import meta_api, reread  # noqa: E402
from hephaestus.prompts import READ_PROMPT  # noqa: E402
from hephaestus.spec import ReadOutput, json_schema  # noqa: E402
from hephaestus.talos import PRESETS  # noqa: E402

with open(os.path.join(ROOT, "experiments", "benchmark", "ground_truth.json")) as f:
    scorer.GROUND_TRUTH.update({k: v for k, v in json.load(f).items() if not k.startswith("_")})
RESULTS = os.path.join(HERE, "results")
PART = {k: k.split("_", 1)[1] for k in scorer.GROUND_TRUTH}          # 01_plate -> plate


def images():
    out = [("hand", p) for p in sorted(glob.glob(os.path.join(ROOT, "experiments", "hand-sketches", "sketches", "*.*")))]
    for d in ("reading-spike", "benchmark"):
        out += [("benchmark", p) for p in sorted(glob.glob(os.path.join(ROOT, "experiments", d, "sketches", "0*.*")))]
    return out


# numbers written on a drawing that the true callouts express differently (the pocket plate's hole spacings 80 and
# 40 are written; its true callouts give the 10 mm insets)
WRITTEN_EXTRA = {"07_pocket_plate": [80, 40]}


def true_values(key):
    with open(os.path.join(ROOT, "examples", "callouts", PART[key] + ".json")) as f:
        return [c["value"] for c in json.load(f) if isinstance(c.get("value"), (int, float))] + WRITTEN_EXTRA.get(key, [])


def perfect(key, spec):
    try:
        return bool(scorer.score(key, spec).get("perfect"))
    except Exception:  # noqa: BLE001 - a broken spec is simply not perfect
        return False


def after_taps(key, spec, flagged):
    """Greedy: for each flagged callout with a parameter, the value (first, second, or a true value) that makes the
    part perfect, else keep the first reading."""
    spec = copy.deepcopy(spec)
    for n in flagged:
        p = n.get("parameter")
        if not p or p not in spec.get("parameters", {}) or not isinstance(spec["parameters"][p].get("value"), (int, float)):
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


def one(group, path, client):
    base = os.path.basename(path).rsplit(".", 1)[0]
    key = base.split("__")[0]
    name = f"{group}__{base}"
    cache = os.path.join(RESULTS, f"{name}.json")
    if os.path.exists(cache):
        return json.load(open(cache))
    s = PRESETS["dev"]
    read_cache = os.path.join(RESULTS, "reads", f"{name}.json")      # reads are kept; re-reads are cheap to redo
    if os.path.exists(read_cache):
        out, info, read_s = (lambda d: (d["out"], d["info"], d["seconds"]))(json.load(open(read_cache)))
    else:
        t0 = time.time()
        out, info = client.respond(READ_PROMPT, [meta_api.text("Convert this drawing into the part spec and list its "
                                                               "dimensions."), meta_api.image(path)],
                                   json_schema(ReadOutput), "ReadOutput", model=s.read_model, effort=s.read_effort)
        read_s = time.time() - t0
        os.makedirs(os.path.dirname(read_cache), exist_ok=True)
        with open(read_cache, "w") as f:
            json.dump({"out": out, "info": {"cost_usd": info["cost_usd"]}, "seconds": read_s}, f)
    callouts = out.get("callouts") or []
    rr = reread.reread(client, path, callouts, model=s.read_model)
    truth = true_values(key)
    rows = []
    for n in rr["numbers"]:
        written = bool(reread.numbers(n.get("label"))) and n.get("text_box") is not None
        wrong = isinstance(n.get("value"), (int, float)) and not any(abs(n["value"] - t) < 1e-6 for t in truth)
        rows.append({**n, "written": written, "misread": bool(wrong and reread.numbers(n.get("label")))})
    flagged = [r for r in rows if r["status"] == "uncertain"]
    rec = {"name": name, "group": group, "key": key, "input_kind": out.get("input_kind"),
           "read_perfect": perfect(key, out.get("spec")), "after_taps": after_taps(key, out.get("spec"), flagged),
           "numbers": rows, "misread": sum(r["misread"] for r in rows),
           "caught": sum(r["misread"] and r["status"] == "uncertain" for r in rows),
           "false_flags": sum((not r["misread"]) and r["status"] == "uncertain" for r in rows),
           "not_located": sum(bool(reread.numbers(r.get("label"))) and r["status"] == "not_located" for r in rows),
           "read_cost": round(info["cost_usd"], 6), "read_seconds": round(read_s, 1),
           "reread_cost": rr["cost_usd"], "reread_seconds": rr["seconds"], "crops": sum(c["crops"] for c in rr["calls"]),
           "spec": out.get("spec")}
    os.makedirs(RESULTS, exist_ok=True)
    with open(cache, "w") as f:
        json.dump(rec, f, indent=1)
    return rec


def rescore(r):
    """Recount misreads from the stored numbers (the truth lists can change without new calls)."""
    truth = true_values(r["key"])
    for n in r["numbers"]:
        wrong = isinstance(n.get("value"), (int, float)) and not any(abs(n["value"] - t) < 1e-6 for t in truth)
        n["misread"] = bool(wrong and reread.numbers(n.get("label")))
    r["misread"] = sum(n["misread"] for n in r["numbers"])
    r["caught"] = sum(n["misread"] and n["status"] == "uncertain" for n in r["numbers"])
    r["false_flags"] = sum((not n["misread"]) and n["status"] == "uncertain" for n in r["numbers"])
    return r


def summarize():
    recs = [rescore(json.load(open(p))) for p in sorted(glob.glob(os.path.join(RESULTS, "*__0*.json")))]
    lines = []
    for group in ("hand", "benchmark"):
        rs = [r for r in recs if r["group"] == group]
        if not rs:
            continue
        lines += [f"## {'Hand sketches' if group == 'hand' else 'Benchmark drawings (clean, photo, rough)'}", "",
                  "| image | read perfect | perfect after taps | misread numbers | flagged: misread / correct | not located | re-read |",
                  "|---|---|---|---|---|---|---|"]
        for r in rs:
            mis = ", ".join(f"{n['label']} (re-read: {n['reread']})" for n in r["numbers"] if n["misread"]) or "-"
            lines.append(f"| {r['name']} | {'yes' if r['read_perfect'] else 'no'} | {'yes' if r['after_taps'] else 'no'} | "
                         f"{mis} | {r['caught']}/{r['misread']} / {r['false_flags']} | {r['not_located']} | "
                         f"{r['crops']} crops, {r['reread_seconds']} s, ${r['reread_cost']:.4f} |")
        n = len(rs)
        lines += ["", f"{n} images: read perfect {sum(r['read_perfect'] for r in rs)}/{n}, perfect after taps "
                      f"{sum(r['after_taps'] for r in rs)}/{n}; misread numbers caught {sum(r['caught'] for r in rs)}/"
                      f"{sum(r['misread'] for r in rs)}; false flags {sum(r['false_flags'] for r in rs)} "
                      f"(max {max(r['false_flags'] for r in rs)} per image, {sum(r['false_flags'] for r in rs) / n:.1f} "
                      f"per image); numbers not located {sum(r['not_located'] for r in rs)}; re-read median "
                      f"{sorted(r['reread_seconds'] for r in rs)[n // 2]} s, ${sum(r['reread_cost'] for r in rs) / n:.4f} "
                      f"per image", ""]
    text = "\n".join(lines)
    with open(os.path.join(RESULTS, "summary.md"), "w") as f:
        f.write(text + "\n")
    print(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-usd", type=float, default=0.15)
    ap.add_argument("--only")
    ap.add_argument("--summarize", action="store_true")
    args = ap.parse_args()
    if args.summarize:
        return summarize()
    os.environ.setdefault("SPEND_SCRIPT", "experiments/numbers/eval_numbers.py")
    client = meta_api.Client(PRESETS["dev"].read_model, budget=meta_api.Budget(args.max_usd))
    jobs = [j for j in images() if not args.only or os.path.basename(j[1]).rsplit(".", 1)[0] in args.only.split(",")]

    def go(job):
        try:
            r = one(*job, client)
            print(f"{r['name']}: read {r['read_perfect']} taps {r['after_taps']} misread {r['misread']} caught "
                  f"{r['caught']} false {r['false_flags']} ({r['crops']} crops, {r['reread_seconds']} s)", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"{os.path.basename(job[1])}: FAILED {type(e).__name__}: {str(e)[:200]}", flush=True)

    with ThreadPoolExecutor(4) as pool:
        list(pool.map(go, jobs))
    print(f"spent ${client.budget.spent:.4f}")
    summarize()


if __name__ == "__main__":
    main()
