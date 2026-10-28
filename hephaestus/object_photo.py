"""Photo of a real flat (2.5D) object on an A4 sheet -> measured outline in mm -> parametric part.

Steps (this file):
1. Sheet and scale: SAM 3.1 "sheet of paper" -> 4 corners -> a metric image of the A4 sheet (210 x 297 mm, either
   orientation) at PX_PER_MM. No sheet -> NoSheet("place the object on an A4 sheet").
   The camera is recovered from the sheet's perspective (focal length from the right angle and equal scale of the
   sheet's sides, then position from the homography); if the photo is too straight-on to solve that, a camera
   350 mm above the object is assumed.
2. Object: SAM on the metric sheet, prompted with a box around what differs from blank paper, a positive point on
   the object and negative points in the box corners (paper or shadow); holes are SAM mask holes cleaned up with
   OpenCV (paper-coloured regions inside the outline).
3. Parallax: the top face is closer to the camera than the paper, so it appears enlarged about the camera's nadir
   by D / (D - t). With the thickness t known the outline is scaled back.
The measured primitives and the spec writing are in the second half (measure_outline, write_spec).
"""
import io
import os

import cv2
import numpy as np
from PIL import Image
from scipy import ndimage

from . import meta_api, sam
from .shape_check import jpeg_bytes, load_rgb, paper_threshold, quad_from_mask, quad_ok

A4 = (210.0, 297.0)
PX_PER_MM = 5.0
ASSUMED_HEIGHT_MM = 350.0
PRIOR_FOCAL_35MM = 26.0     # a phone's main camera, 35 mm-equivalent focal length, when the photo has no EXIF


class NoSheet(ValueError):
    pass


class NoObject(ValueError):
    pass


# ---------------------------------------------------------------- 1. sheet, scale, camera
def find_sheet(img, data, seg):
    """Corners (tl, tr, br, bl) of the sheet in the photo and how they were found. Raises NoSheet."""
    quad, method = None, None
    try:
        recs = seg(data, "sheet of paper")["records"]
        if recs:
            rec = max(recs, key=lambda r: (r["box"][2] - r["box"][0]) * (r["box"][3] - r["box"][1]))
            q = quad_from_mask(sam.full_mask(rec))
            if quad_ok(q, img.shape):
                quad, method = q, "sam"
    except (sam.SamError, meta_api.ModelError):
        pass
    if quad is None:
        q = quad_from_mask(paper_threshold(img))
        if quad_ok(q, img.shape):
            quad, method = q, "threshold"
    if quad is None:
        raise NoSheet("no sheet of paper found: place the object on an A4 sheet, photograph it from above with the "
                      "whole sheet in the picture")
    return quad, method


def sheet_frame(quad):
    """(sheet size in mm along tl->tr and tl->bl, homography sheet mm -> photo px)."""
    tl, tr, br, bl = quad
    wpx = (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)) / 2
    hpx = (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)) / 2
    size = (A4[0], A4[1]) if hpx >= wpx else (A4[1], A4[0])
    mm = np.float32([[0, 0], [size[0], 0], [size[0], size[1]], [0, size[1]]])
    return size, cv2.getPerspectiveTransform(mm, quad.astype(np.float32))


def focal_px(image_size, focal_35mm=None):
    """Focal length in px from the 35 mm-equivalent focal length (EXIF FocalLengthIn35mmFilm, else a typical
    phone). Solving it from the sheet alone is ill-conditioned for photos taken nearly straight down (measured:
    1028-3653 px for a true 1300); the parallax correction barely depends on it (a 20 % error in the camera height
    moves a 6 mm thick part's dimensions by ~0.3 %)."""
    w, h = image_size
    return (focal_35mm or PRIOR_FOCAL_35MM) / 43.27 * np.hypot(w, h)       # 43.27 mm = 35 mm film diagonal


def camera_from_sheet(Hs, image_size, focal=None):
    """Camera position in sheet mm (x right, y down the sheet, z towards the camera) from the homography sheet ->
    photo, with square pixels and the principal point at the image centre. None if it cannot be solved."""
    w, h = image_size
    T = np.array([[1, 0, -w / 2], [0, 1, -h / 2], [0, 0, 1.0]])
    Hc = T @ Hs
    f = focal or focal_px(image_size)
    K = np.array([[f, 0, 0], [0, f, 0], [0, 0, 1.0]])
    B = np.linalg.inv(K) @ Hc
    lam = 2 / (np.linalg.norm(B[:, 0]) + np.linalg.norm(B[:, 1]))
    r1, r2, t = lam * B[:, 0], lam * B[:, 1], lam * B[:, 2]
    if t[2] < 0:
        r1, r2, t = -r1, -r2, -t
    R = np.stack([r1, r2, np.cross(r1, r2)], axis=1)
    C = -R.T @ t
    return {"focal_px": round(f, 1), "position_mm": [round(float(x), 2) for x in C],
            "height_mm": round(abs(float(C[2])), 1)}


def rectify(img, Hs, size, ppm=PX_PER_MM):
    """The sheet as a metric image: 1 mm = ppm px, origin at the sheet's top-left corner."""
    S = np.diag([ppm, ppm, 1.0])
    return cv2.warpPerspective(img, S @ np.linalg.inv(Hs), (int(round(size[0] * ppm)), int(round(size[1] * ppm))),
                               flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))


# ---------------------------------------------------------------- 2. the object
def paper_difference(sheet):
    """Where the metric sheet is not blank paper. Paper is the brightest surface, so its (unevenly lit) brightness
    is a max filter over 60 mm, smoothed; anything darker, or of another colour than the paper, differs.
    Shadows count too (darker paper); the object is the strong-contrast core. A white object on white paper
    does not stand out: that is a documented limit."""
    lab = cv2.cvtColor(sheet, cv2.COLOR_RGB2LAB).astype(np.float32)
    L = lab[..., 0]
    k = int(60 * PX_PER_MM) | 1
    bgL = cv2.GaussianBlur(cv2.dilate(L, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))), (0, 0), k / 4)
    paper_ab = np.median(lab[..., 1:].reshape(-1, 2), axis=0)
    chroma = np.linalg.norm(lab[..., 1:] - paper_ab, axis=2)
    diff = np.maximum(bgL - L, 2.0 * chroma)
    margin = int(4 * PX_PER_MM)                       # ignore the sheet's border (rectification seam)
    diff[:margin], diff[-margin:], diff[:, :margin], diff[:, -margin:] = 0, 0, 0, 0
    _, th = cv2.threshold(np.clip(diff * 4, 0, 255).astype(np.uint8), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return diff, th > 0


def object_prompt(sheet):
    """Box (x1, y1, x2, y2), a positive point and negative points for SAM, from the paper difference.
    Raises NoObject if nothing stands out from the paper."""
    diff, mask = paper_difference(sheet)
    mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8)) > 0
    lab, n = ndimage.label(mask)
    if n == 0:
        raise NoObject("nothing on the sheet stands out from the paper")
    sizes = ndimage.sum(mask, lab, index=np.arange(1, n + 1))
    blob = ndimage.binary_fill_holes(lab == 1 + int(np.argmax(sizes)))
    if blob.sum() < (10 * PX_PER_MM) ** 2:
        raise NoObject("the object is too small: it should be at least 10 mm across")
    # the object core: the strongest contrast within the blob (shadows are soft and weak)
    material = blob & (diff > np.percentile(diff[blob], 40))
    material = cv2.morphologyEx(material.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)) > 0
    core = ndimage.binary_fill_holes(material)
    ys, xs = np.nonzero(blob)
    pad = int(3 * PX_PER_MM)
    H, W = mask.shape
    box = (max(0, xs.min() - pad), max(0, ys.min() - pad), min(W - 1, xs.max() + pad), min(H - 1, ys.max() + pad))
    dist = ndimage.distance_transform_edt(material if material.any() else blob)    # on material, not in a hole
    py, px = np.unravel_index(np.argmax(dist), dist.shape)
    neg = []
    # shadow: differs from paper, but weakly, and lies outside the object material: up to 2 negative points at the
    # deepest pixels of its largest regions
    shadow = blob & ~cv2.dilate(material.astype(np.uint8), np.ones((int(3 * PX_PER_MM),) * 2, np.uint8)).astype(bool)
    sl, sn = ndimage.label(shadow)
    if sn:
        ssz = ndimage.sum(shadow, sl, index=np.arange(1, sn + 1))
        for k in np.argsort(ssz)[::-1][:2]:
            if ssz[k] < (5 * PX_PER_MM) ** 2:
                break
            sd = ndimage.distance_transform_edt(sl == k + 1)
            y, x = np.unravel_index(np.argmax(sd), sd.shape)
            neg.append((int(x), int(y)))
    inset = int(2 * PX_PER_MM)
    for cx, cy in ((box[0] + inset, box[1] + inset), (box[2] - inset, box[1] + inset),
                   (box[2] - inset, box[3] - inset), (box[0] + inset, box[3] - inset)):
        if not core[min(cy, H - 1), min(cx, W - 1)]:
            neg.append((int(cx), int(cy)))
    return {"box": tuple(int(v) for v in box), "point": (int(px), int(py)), "negative": neg, "blob": blob,
            "core": core}


def sam_object(sheet, seg, prompt, variant="box_point_neg"):
    """SAM mask of the object on the metric sheet. Variants: "text" (concept prompt "object"), "box_point",
    "box_point_neg" (with negative points in the box corners: paper or shadow)."""
    data = jpeg_bytes(sheet)
    H, W = sheet.shape[:2]
    if variant == "text":
        recs = seg(data, "object")["records"]
        if not recs:
            return None
        # the instance that overlaps the paper-difference core most
        best = max(recs, key=lambda r: (sam.full_mask(r) & prompt["core"]).sum())
        return sam.full_mask(best)
    ob = {"id": 0, "box": prompt["box"], "points": [prompt["point"]]}
    if variant == "box_point_neg":
        ob["negative"] = prompt["negative"]
    recs = seg(data, sam.object_prompt([ob], W, H))["records"]
    return sam.full_mask(recs[0]) if recs else None


def clean_mask(mask, sheet):
    """Largest component; holes = SAM's own holes plus paper-coloured regions enclosed by the outline."""
    lab, n = ndimage.label(mask)
    if n == 0:
        return mask
    sizes = ndimage.sum(mask, lab, index=np.arange(1, n + 1))
    obj = lab == 1 + int(np.argmax(sizes))
    obj = cv2.morphologyEx(obj.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)) > 0
    outline = ndimage.binary_fill_holes(obj)
    # paper inside the outline: bright and close to the paper colour around the object
    lab_img = cv2.cvtColor(sheet, cv2.COLOR_RGB2LAB).astype(np.float32)
    ring = cv2.dilate(outline.astype(np.uint8), np.ones((25, 25), np.uint8)).astype(bool) & ~outline
    paper = np.median(lab_img[ring], axis=0) if ring.any() else np.array([240, 128, 128], np.float32)
    near_paper = np.linalg.norm(lab_img - paper, axis=2) < 18
    holes = outline & ~obj
    cand = outline & near_paper
    cand = cv2.morphologyEx(cand.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)) > 0
    hl, hn = ndimage.label(cand)
    for k in range(1, hn + 1):
        region = hl == k
        if region.sum() >= (1.0 * PX_PER_MM) ** 2 and not (region & ~cv2.erode(outline.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)).any():
            holes |= region
    holes = ndimage.binary_fill_holes(holes) & outline
    return outline & ~holes


# ---------------------------------------------------------------- 3. parallax
def parallax_correct(mask, camera, thickness, ppm=PX_PER_MM):
    """Scale the object's mask back about the camera's nadir: the top face at height t appears enlarged by
    D / (D - t) on the paper plane. `camera` = camera_from_sheet(...) or None (then a camera ASSUMED_HEIGHT_MM
    above the object's centre). Returns (mask, the factor applied, the nadir in px, the height used)."""
    ys, xs = np.nonzero(mask)
    if camera:
        D = camera["height_mm"]
        nadir = np.array(camera["position_mm"][:2]) * ppm
    else:
        D = ASSUMED_HEIGHT_MM
        nadir = np.array([xs.mean(), ys.mean()])
    s = (D - thickness) / D
    M = np.array([[s, 0, (1 - s) * nadir[0]], [0, s, (1 - s) * nadir[1]]], np.float32)
    out = cv2.warpAffine(mask.astype(np.uint8) * 255, M, (mask.shape[1], mask.shape[0]), flags=cv2.INTER_LINEAR) > 127
    return out, s, nadir, D
