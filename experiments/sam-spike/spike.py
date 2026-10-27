"""SAM 3.1 feasibility spike on line drawings: paper rectification and per-view part masks.

  python spike.py --max-usd 0.10      # ~15 SAM calls at $0.0025, all cached in experiments/sam_cache/

For plate, L-bracket and angled bracket, clean drawing + fake phone photo:
1. photos: SAM "sheet of paper" -> paper mask -> 4 corners -> homography to the clean sheet size (vs a brightness
   threshold fallback);
2. each view: SAM with a box prompt, and box + a point inside the part, vs a classical fill of closed contours;
   every mask is scored by IoU against the true part's silhouette placed in the same view box.
Overlays go to out/.
"""
import argparse
import json
import logging
import os
import sys

import cv2
import numpy as np
from PIL import Image

logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from hephaestus import meta_api, sam  # noqa: E402
from hephaestus.compile import compile  # noqa: E402
from views import pixel_views  # noqa: E402

PARTS = {"01_plate": "plate", "02_l_bracket": "l_bracket", "06_angled_bracket": "angled_bracket"}
OUT = os.path.join(HERE, "out")


def sketch_path(part, variant):
    d = os.path.join(ROOT, "experiments", "reading-spike" if part[:2] in ("01", "02") else "benchmark", "sketches")
    return os.path.join(d, part + (".png" if variant == "clean" else f"__{variant}.jpg"))


def encode(img_rgb):
    ok, buf = cv2.imencode(".png", cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR))
    return buf.tobytes()


# ---------------------------------------------------------------- paper
def quad_from_mask(mask):
    m = mask.astype(np.uint8) * 255
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    hull = cv2.convexHull(max(cnts, key=cv2.contourArea))
    peri = cv2.arcLength(hull, True)
    for eps in np.linspace(0.01, 0.08, 15):
        approx = cv2.approxPolyDP(hull, eps * peri, True)
        if len(approx) == 4:
            return order(approx.reshape(4, 2).astype(np.float32))
    return order(cv2.boxPoints(cv2.minAreaRect(hull)).astype(np.float32))


def order(pts):
    s, d = pts.sum(1), np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], np.float32)


def paper_classical(img):
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    _, th = cv2.threshold(cv2.GaussianBlur(gray, (9, 9), 0), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
    return th > 0


def rectify(img, quad, size):
    W, H = size
    M = cv2.getPerspectiveTransform(quad, np.float32([[0, 0], [W - 1, 0], [W - 1, H - 1], [0, H - 1]]))
    return cv2.warpPerspective(img, M, (W, H), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))


# ---------------------------------------------------------------- views
def truth_masks(name, views, shape):
    """The true part's silhouette per view, placed in the view box (box minus the 1.5 mm pad), as image masks."""
    r = compile(os.path.join(ROOT, "examples", name + ".json"))
    out = {}
    for v, b in views.items():
        sil = r.silhouettes[v]
        lo, hi = np.array(r.metrics["bbox"]["min"]), np.array(r.metrics["bbox"]["max"])
        i, j = {"top": (0, 1), "front": (0, 2), "side": (1, 2)}[v]
        # crop the mask to the part bbox in this view
        r0, c0 = sil.to_px(lo[i], hi[j])
        r1, c1 = sil.to_px(hi[i] - 1e-6, lo[j] + 1e-6)
        crop = sil.mask[r0:r1 + 1, c0:c1 + 1]
        x1, y1, x2, y2 = b["box"]
        pad = 1.5 * (x2 - x1) / (hi[i] - lo[i] + 3)          # 1.5 mm in pixels
        px1, py1, px2, py2 = [int(round(t)) for t in (x1 + pad, y1 + pad, x2 - pad, y2 - pad)]
        m = np.zeros(shape, bool)
        res = np.asarray(Image.fromarray(crop.astype(np.uint8) * 255).resize((px2 - px1, py2 - py1), Image.NEAREST)) > 127
        m[py1:py2, px1:px2] = res
        out[v] = m
    return out


def classical_view(img, box):
    """Fill the closed outline inside the box: ink -> close gaps -> background = flood from the box border;
    enclosed small regions (circles, slots) are holes."""
    x1, y1, x2, y2 = box
    gray = cv2.cvtColor(img[y1:y2 + 1, x1:x2 + 1], cv2.COLOR_RGB2GRAY)
    ink = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 12) > 0
    ink = cv2.morphologyEx(ink.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)) > 0
    n, lab = cv2.connectedComponents((~ink).astype(np.uint8), connectivity=4)
    border = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])))
    regions = [(k, (lab == k).sum()) for k in range(1, n) if k not in border]
    filled = ~np.isin(lab, list(border))
    if regions:
        big = max(a for _, a in regions)
        for k, a in regions:
            if a < 0.08 * big:                                # small enclosed region: a hole
                filled[lab == k] = False
    out = np.zeros(img.shape[:2], bool)
    out[y1:y2 + 1, x1:x2 + 1] = filled
    return out


def iou(a, b, box):
    x1, y1, x2, y2 = box
    a, b = a[y1:y2 + 1, x1:x2 + 1], b[y1:y2 + 1, x1:x2 + 1]
    u = (a | b).sum()
    return float((a & b).sum() / u) if u else 0.0


def overlay(img, masks, path, boxes=(), points=(), quad=None):
    """masks: [(mask, (r, g, b))] tinted over a pale image, with outlines."""
    base = (img.astype(np.float32) * 0.55 + 255 * 0.45)
    for m, col in masks:
        base[m] = base[m] * 0.45 + np.array(col) * 0.55
    out = base.astype(np.uint8)
    for m, col in masks:
        cnts, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(out, cnts, -1, col, 2)
    for b in boxes:
        cv2.rectangle(out, b[:2], b[2:], (40, 40, 40), 1)
    for p in points:
        cv2.circle(out, p, 6, (20, 150, 40), -1)
    if quad is not None:
        cv2.polylines(out, [quad.astype(np.int32)], True, (220, 60, 20), 3)
    Image.fromarray(out).save(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-usd", type=float, default=0.10)
    ap.add_argument("--offline", action="store_true", help="cache only")
    args = ap.parse_args()
    budget = meta_api.Budget(args.max_usd)
    os.environ.setdefault("SPEND_SCRIPT", "experiments/sam-spike/spike.py")
    results, calls = [], []
    for part, name in PARTS.items():
        views = pixel_views(part)
        clean = np.asarray(Image.open(sketch_path(part, "clean")).convert("RGB"))
        H, W = clean.shape[:2]
        truth = truth_masks(name, views, (H, W))
        for variant in ("clean", "photo"):
            row = {"part": part, "variant": variant}
            if variant == "clean":
                img = clean
            else:
                photo = np.asarray(Image.open(sketch_path(part, "photo")).convert("RGB"))
                pb = open(sketch_path(part, "photo"), "rb").read()
                res = sam.segment(pb, "sheet of paper", budget=budget, offline=args.offline)
                calls.append(res)
                recs = res["records"]
                row["paper_objects"] = len(recs)
                quads = {}
                if recs:
                    paper = sam.full_mask(max(recs, key=lambda r: (r["box"][2] - r["box"][0]) * (r["box"][3] - r["box"][1])))
                    quads["sam"] = quad_from_mask(paper)
                quads["classical"] = quad_from_mask(paper_classical(photo))
                overlay(photo, [(paper, (230, 120, 30))] if recs else [], os.path.join(OUT, f"{part}__photo_paper.png"),
                        quad=quads.get("sam"))
                # judge rectification by how well the views line up afterwards: rectify with each, measure ink IoU
                rect = {k: rectify(photo, q, (W, H)) for k, q in quads.items() if q is not None}
                def ink(im):
                    g = cv2.cvtColor(im, cv2.COLOR_RGB2GRAY)
                    return cv2.dilate((cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 12) > 0).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
                clean_ink = ink(clean)
                row["rectify_ink_iou"] = {k: round(float((ink(v) & clean_ink).sum() / (ink(v) | clean_ink).sum()), 3) for k, v in rect.items()}
                best = "sam" if "sam" in rect else "classical"
                img = rect[best]
                Image.fromarray(img).save(os.path.join(OUT, f"{part}__photo_rectified.png"))
            data = encode(img)
            objs_box = [{"id": k, "box": views[v]["box"]} for k, v in enumerate(views)]
            objs_pt = [{"id": k, "box": views[v]["box"], "points": [views[v]["point"]]} for k, v in enumerate(views)]
            masks = {}
            for label, objs in (("box", objs_box), ("box+point", objs_pt)):
                res = sam.segment(data, sam.object_prompt(objs, W, H), budget=budget, offline=args.offline)
                calls.append(res)
                by_id = {r["id"]: sam.full_mask(r) for r in res["records"]}
                masks[label] = {v: by_id.get(k, np.zeros((H, W), bool)) for k, v in enumerate(views)}
            masks["classical"] = {v: classical_view(img, views[v]["box"]) for v in views}
            row["iou"] = {label: {v: round(iou(m[v], truth[v], views[v]["box"]), 3) for v in views} for label, m in masks.items()}
            colours = [(30, 110, 220), (220, 60, 30), (40, 160, 70)]
            for label in masks:
                overlay(img, [(masks[label][v], colours[k % 3]) for k, v in enumerate(views)],
                        os.path.join(OUT, f"{part}__{variant}__{label.replace('+', '_')}.png"),
                        boxes=[views[v]["box"] for v in views],
                        points=[views[v]["point"] for v in views] if label == "box+point" else ())
            results.append(row)
            print(json.dumps(row), flush=True)
    fresh = [c for c in calls if not c["cached"]]
    secs = [c["seconds"] for c in calls]
    print(f"\nSAM calls: {len(calls)} ({len(fresh)} new, ${sum(c['cost_usd'] for c in fresh):.4f}); "
          f"latency median {np.median(secs):.1f} s, max {max(secs):.1f} s; usage example: {calls[0]['usage']}")
    json.dump(results, open(os.path.join(OUT, "results.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
