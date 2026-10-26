"""Talos reading spike: can Muse Spark turn a dimensioned sketch into a correct part spec?

Usage:
  export MODEL_API_KEY=...
  python spike.py                       # all sketches, 1 run each, cheap contributor model
  python spike.py --model muse-spark-1.3 --effort high
  python spike.py --runs 3              # repeat to check consistency
  python spike.py --dry-run             # build the requests, no API call
  python spike.py --score results/<model>/01_plate.run1.json   # re-score a saved output

Results land in results/<model>/, with a summary table printed at the end.
"""
import argparse
import base64
import json
import os
import sys
import time

import numpy as np
import requests

from schema import PART_SPEC, SYSTEM_PROMPT

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://api.meta.ai/v1/responses"
# USD per 1M tokens (input, cached input, output), from dev.meta.ai pricing, Oct 2026
PRICES = {
    "muse-spark-1.3": (1.25, 0.15, 4.25),
    "muse-spark-1.3-contributor": (0.10, 0.002, 0.20),
    "muse-spark-1.2": (1.25, 0.15, 4.25),
    "muse-spark-1.2-contributor": (0.10, 0.002, 0.20),
}
POS_TOL = 0.5   # mm, positions and dimensions
DIA_TOL = 0.2   # mm, diameters/widths/radii


# ---------------------------------------------------------------- API call
def build_request(image_path, model, effort):
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return {
        "model": model,
        "reasoning": {"effort": effort},
        "instructions": SYSTEM_PROMPT,
        "input": [{
            "type": "message", "role": "user",
            "content": [
                {"type": "input_text", "text": "Convert this sketch into the part spec."},
                {"type": "input_image", "image_url": f"data:image/png;base64,{b64}"},
            ],
        }],
        "text": {"format": {"type": "json_schema", "name": "PartSpec", "schema": PART_SPEC}},
    }


def call(body):
    key = os.environ.get("MODEL_API_KEY")
    if not key:
        sys.exit("MODEL_API_KEY is not set")
    t0 = time.time()
    r = requests.post(API, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=300)
    if r.status_code != 200:
        sys.exit(f"API error {r.status_code}: {r.text[:800]}")
    data = r.json()
    text = None
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    text = c["text"]
    if text is None:
        sys.exit(f"No output_text in response: {json.dumps(data)[:800]}")
    return json.loads(text), data.get("usage", {}), time.time() - t0


def cost(model, usage):
    p_in, p_cached, p_out = PRICES.get(model, PRICES["muse-spark-1.3"])
    inp = usage.get("input_tokens", 0)
    cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
    out = usage.get("output_tokens", 0)
    return ((inp - cached) * p_in + cached * p_cached + out * p_out) / 1e6


# ---------------------------------------------------------------- scoring
def union_volume_iou(a_bodies, b_bodies, step=0.5):
    boxes = [(b["x0"], b["y0"], b["z0"], b["x0"] + b["dx"], b["y0"] + b["dy"], b["z0"] + b["dz"])
             for b in a_bodies + b_bodies]
    if not boxes:
        return 0.0
    lo = np.min([bx[:3] for bx in boxes], axis=0)
    hi = np.max([bx[3:] for bx in boxes], axis=0)
    axes = [np.arange(lo[i] + step / 2, hi[i], step) for i in range(3)]
    X, Y, Z = np.meshgrid(*axes, indexing="ij")

    def occ(bodies):
        m = np.zeros(X.shape, bool)
        for b in bodies:
            m |= ((X >= b["x0"]) & (X <= b["x0"] + b["dx"]) & (Y >= b["y0"]) &
                  (Y <= b["y0"] + b["dy"]) & (Z >= b["z0"]) & (Z <= b["z0"] + b["dz"]))
        return m

    a, b = occ(a_bodies), occ(b_bodies)
    union = (a | b).sum()
    return float((a & b).sum() / union) if union else 0.0


def match(pred, truth, same):
    """Greedy one-to-one matching. Returns (correct, missing, extra)."""
    left = list(pred)
    correct = 0
    for t in truth:
        hit = next((p for p in left if same(p, t)), None)
        if hit is not None:
            left.remove(hit)
            correct += 1
    return correct, len(truth) - correct, len(left)


def close(a, b, tol):
    return abs(a - b) <= tol


def score(pred, truth):
    ov = sum(close(pred["overall"][k], truth["overall"][k], POS_TOL) for k in "xyz")
    iou = union_volume_iou(pred["bodies"], truth["bodies"])
    hole = lambda p, t: (p["axis"] == t["axis"] and close(p["u"], t["u"], POS_TOL)
                         and close(p["v"], t["v"], POS_TOL) and close(p["diameter"], t["diameter"], DIA_TOL))
    slot = lambda p, t: (p["axis"] == t["axis"] and close(p["u"], t["u"], POS_TOL)
                         and close(p["v"], t["v"], POS_TOL) and close(p["length"], t["length"], POS_TOL)
                         and close(p["width"], t["width"], DIA_TOL) and p["long_direction"] == t["long_direction"])
    edge = lambda key: (lambda p, t: p["edges"] == t["edges"] and close(p[key], t[key], DIA_TOL))
    feats = {
        "holes": match(pred["holes"], truth["holes"], hole),
        "slots": match(pred["slots"], truth["slots"], slot),
        "fillets": match(pred["fillets"], truth["fillets"], edge("radius")),
        "chamfers": match(pred["chamfers"], truth["chamfers"], edge("size")),
    }
    n_truth = sum(len(truth[k]) for k in feats)
    n_ok = sum(v[0] for v in feats.values())
    n_extra = sum(v[2] for v in feats.values())
    perfect = ov == 3 and iou > 0.99 and n_ok == n_truth and n_extra == 0
    return {"overall_ok": f"{ov}/3", "body_iou": round(iou, 3), "features_ok": f"{n_ok}/{n_truth}",
            "extra_features": n_extra, "perfect": perfect,
            "detail": {k: dict(zip(("ok", "missing", "extra"), v)) for k, v in feats.items()}}


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="muse-spark-1.3-contributor")
    ap.add_argument("--effort", default="medium", choices=["low", "medium", "high"])
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--only", help="run a single sketch, e.g. 02_l_bracket.png")
    ap.add_argument("--max-usd", type=float, default=1.0, help="stop if spend in this run exceeds this")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--score", help="re-score a saved result JSON instead of calling the API")
    args = ap.parse_args()

    truth_all = json.load(open(os.path.join(HERE, "ground_truth.json")))

    if args.score:
        saved = json.load(open(args.score))
        print(json.dumps(score(saved["spec"], truth_all[saved["sketch"]]), indent=2))
        return

    sketches = [args.only] if args.only else sorted(truth_all)
    outdir = os.path.join(HERE, "results", args.model)
    os.makedirs(outdir, exist_ok=True)
    spent, rows = 0.0, []

    for name in sketches:
        body = build_request(os.path.join(HERE, "sketches", name), args.model, args.effort)
        if args.dry_run:
            print(f"{name}: request ok, {len(json.dumps(body)) // 1024} KB")
            continue
        for run in range(1, args.runs + 1):
            if spent > args.max_usd:
                print(f"Stopping: spent ${spent:.3f} > --max-usd {args.max_usd}")
                break
            spec, usage, secs = call(body)
            c = cost(args.model, usage)
            spent += c
            s = score(spec, truth_all[name])
            json.dump({"sketch": name, "model": args.model, "effort": args.effort, "spec": spec,
                       "usage": usage, "cost_usd": c, "seconds": round(secs, 1), "score": s},
                      open(os.path.join(outdir, f"{name[:-4]}.run{run}.json"), "w"), indent=2)
            rows.append((name, run, s, usage.get("output_tokens", 0), c, secs))
            print(f"{name} run {run}: perfect={s['perfect']} overall {s['overall_ok']} "
                  f"iou {s['body_iou']} features {s['features_ok']} extra {s['extra_features']} "
                  f"| {usage.get('output_tokens', 0)} out tok, ${c:.4f}, {secs:.0f}s")
            if spec.get("uncertainties"):
                print(f"   model notes: {spec['uncertainties'][:200]}")

    if rows:
        perfect = sum(r[2]["perfect"] for r in rows)
        print(f"\n{perfect}/{len(rows)} perfect | total ${spent:.4f} | results in {outdir}")


if __name__ == "__main__":
    main()
