"""Steps 1-3 on the synthetic photos: sheet -> metric image -> camera -> object mask (3 SAM prompt variants) ->
parallax correction. Scored against the true footprint (the placed part's top-view silhouette).

  python spike.py --max-usd 0.10        # ~32 SAM requests, cached in experiments/sam_cache/
"""
import argparse
import glob
import json
import os
import sys
import time

import cv2
import numpy as np
from PIL import Image
from scipy import ndimage

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from hephaestus import meta_api, object_photo as op, sam  # noqa: E402
from hephaestus.render import rasterize  # noqa: E402
from make_objects import mesh, place  # noqa: E402

OUT = os.path.join(HERE, "out")
VARIANTS = ("box_point", "box_point_neg")    # the concept prompt "object" found nothing in all 8 photos


def truth_mask(t, size, flip):
    """The true footprint in the metric sheet frame (two candidate orientations of the sheet)."""
    v, faces, _ = mesh(t["part"])
    v = place(v, t["rot_deg"], t["at_mm"])
    W, H = int(round(size[0] * op.PX_PER_MM)), int(round(size[1] * op.PX_PER_MM))
    # world (sheet centre, y up) -> metric (top-left corner, y down)
    x, y = v[:, 0], v[:, 1]
    mx, my = (x + 105, 148.5 - y) if not flip else (105 - x, y + 148.5)
    tri = np.stack([mx, my], -1)[faces]
    m = rasterize(tri, (0, 0), 1 / op.PX_PER_MM, W, H)       # row 0 at y = 0
    return m


def iou(a, b):
    return float((a & b).sum() / max((a | b).sum(), 1))


def boundary_error_mm(a, b):
    """Mean and max distance (mm) from a's boundary to b's boundary."""
    ea = a & ~ndimage.binary_erosion(a)
    eb = b & ~ndimage.binary_erosion(b)
    if not ea.any() or not eb.any():
        return None, None
    d = ndimage.distance_transform_edt(~eb)[ea] / op.PX_PER_MM
    return round(float(d.mean()), 2), round(float(d.max()), 2)


def holes(mask):
    lab, n = ndimage.label(~mask)
    border = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])))
    return sum(1 for k in range(1, n + 1) if k not in border and (lab == k).sum() > (1.0 * op.PX_PER_MM) ** 2)


def overlay(sheet, mask, truth, prompt, path):
    img = (sheet.astype(np.float32) * 0.7 + 255 * 0.3).astype(np.uint8)
    for m, col in ((truth, (40, 170, 60)), (mask, (212, 68, 28))):
        cnts, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(img, cnts, -1, col, 1)
    b = prompt["box"]
    cv2.rectangle(img, b[:2], b[2:], (60, 60, 60), 1)
    cv2.circle(img, prompt["point"], 4, (40, 110, 220), -1)
    for q in prompt["negative"]:
        cv2.circle(img, q, 4, (200, 0, 0), 2)
    x1, y1, x2, y2 = b
    crop = img[max(0, y1 - 40):y2 + 40, max(0, x1 - 40):x2 + 40]
    Image.fromarray(crop).save(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-usd", type=float, default=0.10)
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()
    os.environ.setdefault("SPEND_SCRIPT", "experiments/object-photo/spike.py")
    seg = sam.Segmenter(budget=meta_api.Budget(args.max_usd), offline=args.offline)
    os.makedirs(OUT, exist_ok=True)
    rows = []
    for jpg in sorted(glob.glob(os.path.join(HERE, "photos", "*.jpg"))):
        name = os.path.basename(jpg)[:-4]
        t = json.load(open(jpg[:-4] + ".json"))
        data, img = op.load_rgb(jpg)
        row = {"name": name}
        t0 = time.time()
        quad, method = op.find_sheet(img, data, seg)
        size, Hs = op.sheet_frame(quad)
        cam = op.camera_from_sheet(Hs, (img.shape[1], img.shape[0]))
        sheet = op.rectify(img, Hs, size)
        row.update(sheet=method, sheet_mm=size, sheet_s=round(time.time() - t0, 2),
                   camera_height=cam and cam["height_mm"], true_height=t["camera_height_mm"],
                   focal=cam and cam["focal_px"], true_focal=t["focal_px"])
        Image.fromarray(sheet).save(os.path.join(OUT, f"{name}__sheet.jpg"))
        prompt = op.object_prompt(sheet)
        truths = [truth_mask(t, size, flip) for flip in (False, True)]
        thick = t["thickness_mm"]
        for v in VARIANTS:
            t1 = time.time()
            m = op.sam_object(sheet, seg, prompt, v)
            if m is None:
                row[v] = None
                continue
            m = op.clean_mask(m, sheet)
            corr, s, nadir, D = op.parallax_correct(m, cam, thick)
            tr = max(truths, key=lambda g: iou(corr, g))
            row[v] = {"iou_raw": round(iou(m, tr), 3), "iou_corrected": round(iou(corr, tr), 3),
                      "edge_mm": boundary_error_mm(corr, tr), "holes": holes(corr), "true_holes": holes(tr),
                      "scale": round(s, 4), "seconds": round(time.time() - t1, 2)}
            overlay(sheet, corr, tr, prompt, os.path.join(OUT, f"{name}__{v}.png"))
        rows.append(row)
        print(json.dumps(row), flush=True)
    json.dump(rows, open(os.path.join(OUT, "spike.json"), "w"), indent=1)
    print(f"SAM requests {seg.calls}, new ${seg.cost_usd:.4f}")


if __name__ == "__main__":
    main()
