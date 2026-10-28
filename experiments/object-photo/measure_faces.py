"""Overall size of the measured top face vs the truth, per photo (cached SAM only, no calls).

  python measure_faces.py [--dir photos]       # photos/<name>.jpg + <name>.json (synthetic truth)
Not a tuning loop: reports what object_photo.top_face gives as it stands.
"""
import argparse
import glob
import json
import os
import sys

import cv2
import numpy as np
from scipy import ndimage

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from hephaestus import object_photo as op, sam  # noqa: E402


def oriented(m):
    cnts, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    (_, _), (w, h), _ = cv2.minAreaRect(max(cnts, key=cv2.contourArea))
    return sorted([w / op.PX_PER_MM, h / op.PX_PER_MM], reverse=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.join(HERE, "photos"))
    args = ap.parse_args()
    seg = sam.Segmenter(offline=True)
    rows = []
    for jpg in sorted(glob.glob(os.path.join(args.dir, "*.jpg"))):
        t = json.load(open(jpg[:-4] + ".json"))
        data, img = op.load_rgb(jpg)
        quad, _ = op.find_sheet(img, data, seg)
        size, Hs = op.sheet_frame(quad)
        cam = op.camera_from_sheet(Hs, (img.shape[1], img.shape[0]))
        sheet = op.rectify(img, Hs, size)
        pr = op.object_prompt(sheet)
        m = op.clean_mask(op.sam_object(sheet, seg, pr, "box_point"), sheet)
        face = op.top_face(sheet, m)
        dims = {}
        for lab, mm in (("silhouette", m), ("top_face", face)):
            c, _, _, _ = op.parallax_correct(mm, cam, t["thickness_mm"])
            dims[lab] = [round(d, 1) for d in oriented(ndimage.binary_fill_holes(c))]
        true = sorted(t["bbox_mm"][:2], reverse=True)
        err = [round(a - b, 1) for a, b in zip(dims["top_face"], true)]
        rows.append({"name": os.path.basename(jpg)[:-4], "true": true, **dims, "top_face_error": err})
        print(rows[-1], flush=True)
    errs = [abs(e) for r in rows for e in r["top_face_error"]]
    print(f"top face |error|: median {np.median(errs):.1f} mm, max {max(errs):.1f} mm over {len(errs)} dimensions")
    with open(os.path.join(HERE, "results", "faces.json"), "w") as f:
        json.dump(rows, f, indent=1)


if __name__ == "__main__":
    main()
