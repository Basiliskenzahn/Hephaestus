"""Speed/accuracy matrix for model settings.

  python matrix.py read   --max-usd 2.0     # read only: model x effort on photo + rough, 8 parts x 1 run
  python matrix.py critic --max-usd 2.5     # critic: model x effort, 1 vs 2 samples, on the injected-error eval
  python matrix.py summary                  # tables from saved results, no API calls

read: one read call per image, scored on compiled geometry (reading-spike-v2 scorer).
critic: per check two critic samples are stored, so "1 sample" (the first) and "2 samples" (consensus) are compared
on the same calls. Items: the 6 injected errors x 2 copies (critic must flag them, alone or together with the
callouts) and the 8 correct parts x 2 copies (false alarms). Latency of a 2-sample check is the slower sample.
"""
import argparse
import glob
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
sys.path.insert(0, os.path.join(ROOT, "experiments", "loop-eval"))

import bench  # noqa: E402  (registers the benchmark parts with the scorer)
import loop_eval  # noqa: E402

from hephaestus import critic as critic_mod  # noqa: E402
from hephaestus import meta_api  # noqa: E402
from hephaestus.compile import compile  # noqa: E402
from hephaestus.drawing import render_sheet  # noqa: E402
from hephaestus.measure import check as callout_check, issues as callout_issues  # noqa: E402
from hephaestus.prompts import READ_PROMPT  # noqa: E402
from hephaestus.spec import ReadOutput, json_schema  # noqa: E402

OUT = os.path.join(HERE, "results", "matrix")
MODELS = ["muse-spark-1.3", "muse-spark-1.3-contributor"]
EFFORTS = ["low", "medium"]
CRITIC_MAX_TOKENS = 8000


def p90(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.9 * (len(xs) - 1))))] if xs else 0


# ---------------------------------------------------------------- read
def read_job(client, model, effort, part, set_name):
    path = os.path.join(OUT, "read", f"{model}_{effort}", f"{part}__{set_name}.json")
    if os.path.exists(path):
        return json.load(open(path))
    image = os.path.join(bench.IMAGE_DIRS[part], bench.scorer.GROUND_TRUTH[part]["images"][set_name])
    rec = {"part": part, "set": set_name, "model": model, "effort": effort}
    try:
        out, info = client.respond(READ_PROMPT, [meta_api.text("Convert this drawing into the part spec and list its "
                                                               "dimensions."), meta_api.image(image)],
                                   json_schema(ReadOutput), "ReadOutput", model=model, effort=effort)
        rec.update(seconds=info["seconds"], output_tokens=info["usage"].get("output_tokens", 0),
                   cost_usd=info["cost_usd"], output=out, score=bench.safe_score(part, out.get("spec")))
    except meta_api.ModelError as e:
        rec.update(error=str(e)[:300], score={"perfect": False, "compiles": False})
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(rec, open(path, "w"), indent=1)
    return rec


def read_jobs():
    parts = sorted(bench.scorer.GROUND_TRUTH)
    return [(m, e, p, s) for m in MODELS for e in EFFORTS for s in ("photo", "rough") for p in parts]


# ---------------------------------------------------------------- critic
def critic_items():
    items = []
    for name in loop_eval.ERRORS:
        part, spec = loop_eval.injected(name)
        items += [{"kind": "error", "name": name, "part": part, "copy": c, "spec": spec} for c in (1, 2)]
    for part in loop_eval.PARTS:
        items += [{"kind": "correct", "name": part, "part": part, "copy": c, "spec": loop_eval.load(part, "spec")}
                  for c in (1, 2)]
    return items


_sheets = {}


def sheet_for(item):
    key = (item["kind"], item["name"])
    if key not in _sheets:
        result = compile(item["spec"])
        rows = callout_check(loop_eval.load(item["part"], "callouts"), result)
        _sheets[key] = (render_sheet(result), bool(callout_issues(rows)))
    return _sheets[key]


def critic_job(client, model, effort, item):
    path = os.path.join(OUT, "critic", f"{model}_{effort}", f"{item['kind']}_{item['name']}.{item['copy']}.json")
    if os.path.exists(path):
        return json.load(open(path))
    _, img_dir, image = loop_eval.PARTS[item["part"]]
    sheet, callouts_fail = sheet_for(item)
    samples = []
    for k in range(2):
        try:
            out, info = critic_mod.critique(client, os.path.join(img_dir, image), sheet, model, effort, CRITIC_MAX_TOKENS)
            samples.append({"out": out, "seconds": info["seconds"], "output_tokens": info["usage"].get("output_tokens", 0),
                            "cost_usd": info["cost_usd"]})
        except meta_api.BudgetExceeded:
            raise
        except meta_api.ModelError as e:
            samples.append({"error": str(e)[:300]})
    rec = {k: item[k] for k in ("kind", "name", "part", "copy")}
    rec.update(model=model, effort=effort, callouts_fail=callouts_fail, samples=samples)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(rec, open(path, "w"), indent=1)
    return rec


def critic_jobs(items):
    return [(m, e, it) for m in MODELS for e in EFFORTS for it in items]


# ---------------------------------------------------------------- summaries
def summarize_read():
    lines = ["| read model | effort | perfect | compiles | median s | p90 s | median out tok | cost / read |",
             "|---|---|---|---|---|---|---|---|"]
    for m in MODELS:
        for e in EFFORTS:
            rs = [json.load(open(p)) for p in glob.glob(os.path.join(OUT, "read", f"{m}_{e}", "*.json"))]
            if not rs:
                continue
            ok = [r for r in rs if "seconds" in r]
            n = len(rs)
            per_set = ", ".join(f"{s} {sum(r['score']['perfect'] for r in rs if r['set'] == s)}/"
                                f"{sum(1 for r in rs if r['set'] == s)}" for s in ("photo", "rough"))
            lines.append(f"| {m} | {e} | {sum(r['score']['perfect'] for r in rs)}/{n} ({per_set}) | "
                         f"{sum(bool(r['score'].get('compiles')) for r in rs)}/{n} | "
                         f"{st.median(r['seconds'] for r in ok):.0f} | {p90([r['seconds'] for r in ok]):.0f} | "
                         f"{st.median(r['output_tokens'] for r in ok):.0f} | ${st.mean(r['cost_usd'] for r in ok):.4f} |")
    return "\n".join(lines)


def _blocks(outs):
    valid = [o for o in outs if o is not None]
    if not valid:
        return False, None
    return bool(critic_mod.blocking(critic_mod.merge(valid))), valid


def summarize_critic():
    lines = ["| critic model | effort | samples | flags injected errors | loop detects (callout or critic) | "
             "false alarms (correct parts) | median s | p90 s | median out tok / sample | cost / check |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    detail = {}
    for m in MODELS:
        for e in EFFORTS:
            rs = [json.load(open(p)) for p in glob.glob(os.path.join(OUT, "critic", f"{m}_{e}", "*.json"))]
            if not rs:
                continue
            for n in (1, 2):
                flagged = detected = fa = 0
                errs = [r for r in rs if r["kind"] == "error"]
                good = [r for r in rs if r["kind"] == "correct"]
                secs, toks, costs, failed = [], [], [], 0
                per_error = {}
                for r in rs:
                    ss = r["samples"][:n]
                    if any("error" in s for s in ss):
                        failed += 1
                    outs = [s.get("out") for s in ss]
                    blk, _ = _blocks(outs)
                    valid = [s for s in ss if "seconds" in s]
                    if valid:
                        secs.append(max(s["seconds"] for s in valid))
                        toks += [s["output_tokens"] for s in valid]
                        costs.append(sum(s["cost_usd"] for s in valid))
                    if r["kind"] == "error":
                        flagged += blk
                        detected += blk or r["callouts_fail"]
                        per_error.setdefault(r["name"], []).append("C" if blk else "-")
                    else:
                        fa += blk
                detail[(m, e, n)] = per_error
                lines.append(f"| {m} | {e} | {n} | {flagged}/{len(errs)} | {detected}/{len(errs)} | {fa}/{len(good)} | "
                             f"{st.median(secs):.0f} | {p90(secs):.0f} | {st.median(toks):.0f} | "
                             f"${st.mean(costs):.4f} |" + (f" ({failed} failed calls)" if failed else ""))
    lines += ["", "Critic flags per injected error (C = flagged, - = not), copies 1 and 2:", "",
              "| config | " + " | ".join(loop_eval.ERRORS) + " |", "|---|" + "---|" * len(loop_eval.ERRORS)]
    for (m, e, n), per in detail.items():
        lines.append(f"| {m.replace('muse-spark-', '')} {e} x{n} | "
                     + " | ".join("".join(per.get(k, [])) for k in loop_eval.ERRORS) + " |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["read", "critic", "summary"])
    ap.add_argument("--max-usd", type=float, default=1.0)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    if args.mode == "summary":
        text = "## Read\n\n" + summarize_read() + "\n\n## Critic\n\n" + summarize_critic()
        os.makedirs(OUT, exist_ok=True)
        open(os.path.join(OUT, "summary.md"), "w").write(text + "\n")
        print(text)
        return
    client = meta_api.Client(budget=meta_api.Budget(args.max_usd))
    lock = threading.Lock()
    jobs = read_jobs() if args.mode == "read" else critic_jobs(critic_items())
    t0 = time.time()

    def do(job):
        try:
            rec = read_job(client, *job) if args.mode == "read" else critic_job(client, *job)
            msg = "ok" if "error" not in rec else rec["error"][:100]
        except meta_api.BudgetExceeded as e:
            msg = f"budget: {e}"
        with lock:
            label = f"{job[0]} {job[1]} " + (f"{job[2]} {job[3]}" if args.mode == "read" else
                                             f"{job[2]['kind']} {job[2]['name']} {job[2]['copy']}")
            print(f"[{time.time() - t0:5.0f}s] {label}: {msg}", flush=True)

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(do, jobs))
    print(f"spent ${client.budget.spent:.4f}")
    print(summarize_read() if args.mode == "read" else summarize_critic())


if __name__ == "__main__":
    main()
