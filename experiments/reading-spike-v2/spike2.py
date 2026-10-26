"""Reading spike v2: can Muse Spark fill in part spec v1 from a dimensioned drawing?

Usage:
  python spike2.py --set clean --runs 3            # contributor model, medium effort
  python spike2.py --set photo --runs 3
  python spike2.py --only 02_l_bracket --set rough
  python spike2.py --rescore                       # re-score saved results (no API calls)

Output schema: hephaestus.spec.json_schema() as non-strict json_schema (Meta rejects strict: true, see
results/schema_probe/). Images come from ../reading-spike/sketches/. Results land in results/<model>/.
"""
import argparse
import base64
import glob
import json
import os
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "experiments", "reading-spike"))

from spike import API, cost, read_stream  # noqa: E402

from hephaestus.compile import SpecError, compile  # noqa: E402
from hephaestus.spec import json_schema  # noqa: E402
from prompt import FEWSHOT_SPEC, SYSTEM_PROMPT  # noqa: E402
from score import GROUND_TRUTH, score  # noqa: E402

SKETCHES = os.path.join(ROOT, "experiments", "reading-spike", "sketches")
SETS = ["clean", "photo", "rough"]


def build_request(image_path, model, effort):
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    mime = "image/jpeg" if image_path.lower().endswith((".jpg", ".jpeg")) else "image/png"
    return {
        "model": model, "reasoning": {"effort": effort}, "instructions": SYSTEM_PROMPT, "stream": True,
        "input": [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "Convert this drawing into the part spec."},
            {"type": "input_image", "image_url": f"data:{mime};base64,{b64}"}]}],
        "text": {"format": {"type": "json_schema", "name": "PartSpec", "schema": json_schema()}},
    }


def call(body):
    """Returns (output text or None, usage, seconds, error or None). Never exits: one failed call is a data point."""
    key = os.environ.get("MODEL_API_KEY")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    t0 = time.time()
    try:
        r = requests.post(API, headers=headers, json=body, timeout=(15, 120), stream=True)
        if r.status_code != 200:
            return None, {}, time.time() - t0, f"HTTP {r.status_code}: {r.text[:500]}"
        data = read_stream(r.iter_lines())
    except (requests.RequestException, SystemExit) as e:
        return None, {}, time.time() - t0, f"{type(e).__name__}: {e}"
    text = next((c["text"] for item in data.get("output", []) if item.get("type") == "message"
                 for c in item.get("content", []) if c.get("type") == "output_text"), None)
    return text, data.get("usage", {}), time.time() - t0, None if text else "no output_text"


def score_output(part, text):
    try:
        spec = json.loads(text)
    except (TypeError, json.JSONDecodeError) as e:
        return None, {"compiles": False, "errors": [f"output is not JSON: {e}"], "perfect": False}
    return spec, score(part, spec)


def line(name, run, s, usage, c, secs):
    if not s.get("compiles"):
        detail = f"FAIL: {s['errors'][0][:150]}"
    else:
        failed = ", ".join(d["label"] for d in s["failed_dims"]) or "-"
        xp = s["x_param"]
        detail = (f"bbox {'ok' if s['bbox_ok'] else s['bbox']} iou {s['iou']} dims {s['dims_ok']} (failed: {failed}) "
                  f"| params {s['params']['parameters']}, exprs {s['params']['computed_exprs']}, "
                  f"x-param {xp.get('param')} {'ok' if xp['ok'] else 'no'}")
    return (f"{name} run {run}: perfect={s['perfect']} {detail} | "
            f"{usage.get('output_tokens', 0)} out tok, ${c:.4f}, {secs:.0f}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="muse-spark-1.3-contributor")
    ap.add_argument("--effort", default="medium", choices=["low", "medium", "high"])
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--set", default="clean", choices=SETS + ["all"])
    ap.add_argument("--only", help="one part, e.g. 02_l_bracket")
    ap.add_argument("--max-usd", type=float, default=1.0, help="stop before spending more than this in this run")
    ap.add_argument("--rescore", action="store_true", help="re-score saved results, no API calls")
    args = ap.parse_args()

    compile(FEWSHOT_SPEC)       # the few-shot in the prompt must itself be a valid spec
    outdir = os.path.join(HERE, "results", args.model)

    if args.rescore:
        for path in sorted(glob.glob(os.path.join(outdir, "*.json"))):
            rec = json.load(open(path))
            if rec.get("output_text"):
                _, rec["score"] = score_output(rec["part"], rec["output_text"])
                json.dump(rec, open(path, "w"), indent=2)
            print(line(rec["image"], rec["run"], rec["score"], rec.get("usage", {}), rec.get("cost_usd", 0),
                       rec.get("seconds", 0)))
        return

    os.makedirs(outdir, exist_ok=True)
    sets = SETS if args.set == "all" else [args.set]
    parts = [args.only] if args.only else sorted(GROUND_TRUTH)
    spent, rows = 0.0, []
    for set_name in sets:
        for part in parts:
            image = GROUND_TRUTH[part]["images"][set_name]
            body = build_request(os.path.join(SKETCHES, image), args.model, args.effort)
            for run in range(1, args.runs + 1):
                if spent >= args.max_usd:
                    print(f"Stopping: spent ${spent:.4f} >= --max-usd {args.max_usd}")
                    break
                text, usage, secs, err = call(body)
                c = cost(args.model, usage)
                spent += c
                if err:
                    s, spec = {"compiles": False, "errors": [f"API: {err}"], "perfect": False}, None
                else:
                    spec, s = score_output(part, text)
                rec = {"part": part, "set": set_name, "image": image, "run": run, "model": args.model,
                       "effort": args.effort, "spec": spec, "output_text": text, "usage": usage, "cost_usd": c,
                       "seconds": round(secs, 1), "score": s}
                stem = os.path.splitext(image)[0]
                json.dump(rec, open(os.path.join(outdir, f"{stem}.run{run}.json"), "w"), indent=2)
                rows.append((set_name, s, usage, c, secs))
                print(line(image, run, s, usage, c, secs), flush=True)
    if rows:
        print()
        for set_name in sets:
            r = [x for x in rows if x[0] == set_name]
            if r:
                print(f"{set_name}: {sum(x[1]['perfect'] for x in r)}/{len(r)} perfect, "
                      f"{sum(x[1].get('compiles', False) for x in r)}/{len(r)} compile, "
                      f"${sum(x[3] for x in r):.4f}, avg {sum(x[4] for x in r) / len(r):.0f}s, "
                      f"avg {sum(x[2].get('output_tokens', 0) for x in r) / len(r):.0f} out tok")
        print(f"total ${spent:.4f} | results in {outdir}")


if __name__ == "__main__":
    main()
