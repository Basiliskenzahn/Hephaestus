"""Shape check: the drawing's views segmented by SAM 3.1, compared with the compiled part's silhouettes.

Independent of the reading model: it compares pixels of the drawing with pixels of the part, so it catches errors
the callouts cannot express (an upright at the front instead of the back, a mirrored profile) without asking a
model's opinion.

Per run, once (`InputShape`):
1. the sheet: for a photo, SAM "sheet of paper" -> 4 corners -> homography -> straightened sheet (a brightness
   threshold is the fallback); a scan is used as is. Starts in parallel with the read.
2. the views: the read gives a box per view (`views`, 0-1000 normalised). SAM segments every view in one request,
   box prompt first, then box + a positive point at the deepest pixel of that first mask. The largest component,
   holes filled, is the view's outline. SAM does not cut through-holes, so holes come from the ink: closed regions
   inside the outline (see `closed_regions`).
Per iteration (`compare`), each view against the part's silhouette, both cropped to their own bounding box:
- aspect ratio: flagged when width:height differs by more than ASPECT_TOL (45 %; blocking);
- orientation: IoU of the drawing's outline with the part's, as is and mirrored left-right, mirrored up-down and
  rotated 180 degrees. Flagged only when a transformed version beats the as-is IoU by ORIENT_MARGIN: the decision is
  relative, never an absolute IoU threshold, because SAM outlines of correct parts score only 0.8-0.95 (blocking);
- holes: closed regions in the drawing vs closed regions in the part's own line drawing (same detector), by count
  and rough position (advisory unless HOLES_BLOCKING).
Anything unusable (no views, SAM error, a mask that is not a part) makes the check unavailable, never fails a run.
Thresholds come from experiments/shape-check (false alarms on 8 correct parts x 3 input types, injected errors).
"""
import io
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
from PIL import Image
from scipy import ndimage

from . import meta_api, sam

VIEW_NAMES = ("top", "front", "side")
AXES = {"top": ("X", "Y"), "front": ("X", "Z"), "side": ("Y", "Z")}
# words for (low, high) along the view's right and up directions
WORDS = {"top": (("left", "right"), ("front", "back")), "front": (("left", "right"), ("bottom", "top")),
         "side": (("front", "back"), ("bottom", "top"))}
GRID = 96               # orientation compare resolution
WORK = 400              # longest side of the outline crop for hole detection, px
ORIENT_MARGIN = 0.10    # a mirrored/rotated outline must beat as-is IoU by this much (tuned in experiments/shape-check)
ASPECT_TOL = 1.45       # width:height may differ by up to 45 % (correct parts reach 1.29 on photos and sketches)
HOLE_MIN_FRAC = 0.002   # closed region area / outline area: smaller is text or arrowheads
HOLE_MAX_FRAC = 0.30    # larger is a face of the part (a step, a slope) rather than a hole
HOLE_MATCH = 0.15       # hole centres match within this fraction of the outline's size
HOLES_BLOCKING = False  # holes stay advisory unless the eval shows zero false alarms
MIN_FILL = 0.12         # a view outline covering less of its box is not a part
MIN_SPAN = 0.5          # ... nor one spanning less than half of the box's width or height


class Unavailable(RuntimeError):
    """The shape check cannot run for this input; the loop logs it and continues."""


# ---------------------------------------------------------------- images
def load_rgb(image):
    data = image if isinstance(image, (bytes, bytearray)) else open(image, "rb").read()
    img = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))
    return bytes(data), img


def jpeg_bytes(img):
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "JPEG", quality=90)
    return buf.getvalue()


def looks_like_scan(img):
    """True when the image border is plain white paper (a scan or an exported drawing, nothing to straighten)."""
    h, w = img.shape[:2]
    b = max(4, int(0.02 * min(h, w)))
    band = np.concatenate([img[:b].reshape(-1, 3), img[-b:].reshape(-1, 3), img[:, :b].reshape(-1, 3),
                           img[:, -b:].reshape(-1, 3)])
    bright = (band.min(axis=1) > 200) & (band.max(axis=1).astype(int) - band.min(axis=1) < 30)
    return bright.mean() > 0.9


def order_quad(pts):
    s, d = pts.sum(1), np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], np.float32)


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
            return order_quad(approx.reshape(4, 2).astype(np.float32))
    return order_quad(cv2.boxPoints(cv2.minAreaRect(hull)).astype(np.float32))


def paper_threshold(img):
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    _, th = cv2.threshold(cv2.GaussianBlur(gray, (9, 9), 0), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats((th > 0).astype(np.uint8))
    if n < 2:
        return th > 0
    return lab == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))


def quad_ok(quad, shape):
    if quad is None:
        return False
    area = cv2.contourArea(quad.astype(np.float32))
    return cv2.isContourConvex(quad.astype(np.int32)) and 0.15 < area / (shape[0] * shape[1]) < 0.97


def rectify(img, quad):
    """Warp the sheet inside `quad` (tl, tr, br, bl) to a rectangle sized by its mean side lengths."""
    tl, tr, br, bl = quad
    w = (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)) / 2
    h = (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)) / 2
    scale = min(1.0, 1600 / max(w, h))
    W, H = int(round(w * scale)), int(round(h * scale))
    M = cv2.getPerspectiveTransform(quad.astype(np.float32),
                                    np.float32([[0, 0], [W - 1, 0], [W - 1, H - 1], [0, H - 1]]))
    return cv2.warpPerspective(img, M, (W, H), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255)), M


# ---------------------------------------------------------------- closed regions (holes)
def ink_mask(gray, close=0):
    """Dark lines on paper. `close` > 0 bridges gaps up to that many px (it also joins dashed hidden lines)."""
    ink = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 12) > 0
    if close:
        ink = cv2.morphologyEx(ink.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((close, close), np.uint8)) > 0
    return ink


def closed_regions(ink, outline, r=3, raw=None, solid=0.85):
    """Hole-like closed regions: background regions enclosed by ink, inside the outline.

    Regions split only by thin lines (centre-line crosses, the rings of a counterbore) are merged into one by
    dilating them by `r` px. Kept if the merged region is compact (solidity >= 0.8, circularity >= 0.5) and its
    area is between HOLE_MIN_FRAC (text loops like 0, 8, Ø, arrowheads) and HOLE_MAX_FRAC (faces) of the outline.
    With `raw` (the ink before closing gaps), a region's boundary must also be at least `solid` ink in it: closing
    seals the small gaps of sketchy outlines, but also dashed hidden lines, whose regions have gappy boundaries.
    Sharp-cornered rectangles are dropped too: at working resolution the strip between two hidden lines is closed
    and solid; holes and slots have round ends.
    Returns [{"u", "v" (centre, 0-1 of the outline's box, v up), "area" (fraction of outline), "circ"}]."""
    H, W = ink.shape
    out_area = max(int(outline.sum()), 1)
    lab, n = ndimage.label(~ink, structure=[[0, 1, 0], [1, 1, 1], [0, 1, 0]])
    if n == 0:
        return []
    border = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    sizes = ndimage.sum(np.ones_like(lab), lab, index=np.arange(1, n + 1))
    inside = ndimage.sum(outline, lab, index=np.arange(1, n + 1))
    keep = [k + 1 for k in range(n) if (k + 1) not in border and sizes[k] <= HOLE_MAX_FRAC * out_area
            and inside[k] >= 0.9 * sizes[k]]
    if not keep:
        return []
    cand = np.isin(lab, keep)
    disk = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    grown = cv2.dilate(cand.astype(np.uint8), disk) > 0
    glab, gn = ndimage.label(grown)
    holes = []
    for g in range(1, gn + 1):
        region = ndimage.binary_fill_holes(glab == g)
        region = cv2.erode(region.astype(np.uint8), disk) > 0
        area = int(region.sum())
        if not HOLE_MIN_FRAC * out_area <= area <= HOLE_MAX_FRAC * out_area:
            continue
        cnts, _ = cv2.findContours(region.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        c = max(cnts, key=cv2.contourArea)
        hull = cv2.contourArea(cv2.convexHull(c))
        peri = cv2.arcLength(c, True)
        solidity = area / hull if hull else 0
        circ = 4 * np.pi * area / peri ** 2 if peri else 0
        if solidity < 0.8 or circ < 0.5:
            continue
        rect = cv2.minAreaRect(c)
        if len(cv2.approxPolyDP(c, 0.03 * peri, True)) == 4 and area / max(rect[1][0] * rect[1][1], 1) > 0.9:
            continue                     # sharp-cornered rectangle: the strip between hidden lines, not a hole
        if raw is not None:
            ring = cv2.dilate(region.astype(np.uint8), disk) > 0
            edge = ring & ~(cv2.erode(ring.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0)
            near = cv2.dilate(raw.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
            if edge.any() and near[edge].mean() < solid:
                continue
        ys, xs = np.nonzero(region)
        holes.append({"u": round(float(xs.mean() / W), 3), "v": round(float(1 - ys.mean() / H), 3),
                      "area": round(area / out_area, 4), "circ": round(float(circ), 2),
                      "px": [int(xs.mean()), int(ys.mean())], "r_px": float(np.sqrt(area / np.pi))})
    return holes


def match_holes(a, b):
    """Pairs of hole indices (i in a, j in b) with centres within HOLE_MATCH; returns (pairs, unmatched a, b)."""
    from scipy.optimize import linear_sum_assignment
    if not a or not b:
        return [], list(range(len(a))), list(range(len(b)))
    cost = np.array([[np.hypot(p["u"] - q["u"], p["v"] - q["v"]) for q in b] for p in a])
    rows, cols = linear_sum_assignment(cost)
    pairs = [(i, j) for i, j in zip(rows, cols) if cost[i, j] <= HOLE_MATCH]
    return pairs, [i for i in range(len(a)) if i not in {p[0] for p in pairs}], \
        [j for j in range(len(b)) if j not in {p[1] for p in pairs}]


def where(view, u, v):
    """Rough position words for a point (0-1, v up) in a view, e.g. 'back-left'."""
    (l, r), (lo, hi) = WORDS[view]
    h = l if u < 0.36 else r if u > 0.64 else ""
    vv = lo if v < 0.36 else hi if v > 0.64 else ""
    if not h and not vv:
        return "centre"
    return "-".join(w for w in (vv, h) if w)


def ink_bounded(mask, gray, box):
    """The SAM outline cut back to what the drawing's lines enclose. SAM leaks into labels and dimension areas next
    to the part; those are open on paper, so the ink fill (closed regions + their lines) excludes them. If the ink
    fill keeps less than 60 % of the outline (a sketch with gaps in its outline), the SAM outline is kept as is."""
    if os.environ.get("SHAPE_NO_INK_BOUND") or not mask.any():
        return mask
    x1, y1, x2, y2 = box
    ink = ink_mask(gray[y1:y2 + 1, x1:x2 + 1], close=5)
    fill = ndimage.binary_fill_holes(ink)
    fill = cv2.dilate(fill.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    out = mask.copy()
    out[y1:y2 + 1, x1:x2 + 1] &= fill
    out[:y1] = False
    out[y2 + 1:] = False
    out[:, :x1] = False
    out[:, x2 + 1:] = False
    return out if out.sum() >= 0.6 * mask.sum() else mask


def trim(mask):
    """Cut thin leaks of a SAM outline along extension and dimension lines (an opening a few % of the outline's
    smaller side, far thinner than any wall), keeping the largest piece."""
    if not mask.any():
        return mask
    _, (x1, y1, x2, y2) = crop_to_bbox(mask)
    k = max(5, int(0.05 * min(x2 - x1, y2 - y1))) | 1
    opened = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN,
                              cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))) > 0
    lab, n = ndimage.label(opened)
    if n == 0:
        return mask
    sizes = ndimage.sum(opened, lab, index=np.arange(1, n + 1))
    return lab == 1 + int(np.argmax(sizes))


def refine_box(gray, box, grow=0.15):
    """Snap a view box to the part's outline: the bbox of the ink components that look like outline loops (wide AND
    tall: at least 40 % of the box both ways, so dimension and extension lines don't count) and lie mostly in the box
    grown by `grow`. Model boxes are often shifted by 5-15 %; the outline is the one big closed drawing in them.
    Returns the box unchanged if nothing qualifies."""
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1 + 1, y2 - y1 + 1
    H, W = gray.shape
    gx1, gy1 = max(0, int(x1 - grow * bw)), max(0, int(y1 - grow * bh))
    gx2, gy2 = min(W - 1, int(x2 + grow * bw)), min(H - 1, int(y2 + grow * bh))
    ink = ink_mask(gray[gy1:gy2 + 1, gx1:gx2 + 1], close=3)
    lab, n = ndimage.label(ink, structure=np.ones((3, 3)))
    found = []
    for k, sl in enumerate(ndimage.find_objects(lab)):
        h, w = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if w < 0.4 * bw or h < 0.4 * bh:
            continue
        cx1, cy1, cx2, cy2 = sl[1].start + gx1, sl[0].start + gy1, sl[1].stop - 1 + gx1, sl[0].stop - 1 + gy1
        inter = max(0, min(cx2, x2) - max(cx1, x1)) * max(0, min(cy2, y2) - max(cy1, y1))
        if inter >= 0.5 * w * h:
            found.append((cx1, cy1, cx2, cy2))
    if not found:
        return box
    f = np.array(found)
    nb = [int(f[:, 0].min()), int(f[:, 1].min()), int(f[:, 2].max()), int(f[:, 3].max())]
    px, py = 0.02 * (nb[2] - nb[0]) + 3, 0.02 * (nb[3] - nb[1]) + 3
    return [int(max(0, nb[0] - px)), int(max(0, nb[1] - py)), int(min(W - 1, nb[2] + px)), int(min(H - 1, nb[3] + py))]


def interior_point(gray, box):
    """A point on the part's face in a view box: the deepest pixel of the largest region enclosed by ink (the face
    between the outline and its holes), or None. SAM needs it: with a box alone it segments the line strokes."""
    x1, y1, x2, y2 = box
    crop = gray[y1:y2 + 1, x1:x2 + 1]
    if crop.size == 0:
        return None
    ink = ink_mask(crop, close=5)
    lab, n = ndimage.label(~ink, structure=[[0, 1, 0], [1, 1, 1], [0, 1, 0]])
    if n == 0:
        return None
    border = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])).tolist())
    sizes = ndimage.sum(np.ones_like(lab), lab, index=np.arange(1, n + 1))
    cands = [(sizes[k - 1], k) for k in range(1, n + 1) if k not in border and sizes[k - 1] >= 0.05 * crop.size]
    if not cands:
        return None
    region = lab == max(cands)[1]
    dist = ndimage.distance_transform_edt(region)
    y, x = np.unravel_index(np.argmax(dist), dist.shape)
    return int(x + x1), int(y + y1)


# ---------------------------------------------------------------- outlines
def crop_to_bbox(mask):
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None, None
    x1, x2, y1, y2 = xs.min(), xs.max(), ys.min(), ys.max()
    return mask[y1:y2 + 1, x1:x2 + 1], (int(x1), int(y1), int(x2), int(y2))


def grid(mask):
    return cv2.resize(mask.astype(np.float32), (GRID, GRID), interpolation=cv2.INTER_AREA) > 0.5


def iou(a, b):
    u = (a | b).sum()
    return float((a & b).sum() / u) if u else 0.0


TRANSFORMS = {"as_is": lambda m: m, "mirror_lr": np.fliplr, "mirror_ud": np.flipud, "rot180": lambda m: m[::-1, ::-1]}


def _side(profile, words):
    """Which end of a 1-D material profile holds its maximum: words[0], words[1] or None (no clear end)."""
    p = np.asarray(profile, float)
    top = np.nonzero(p >= 0.85 * p.max())[0] / max(len(p) - 1, 1)
    c = top.mean()
    return words[0] if c < 0.4 else words[1] if c > 0.6 else None


def orientation_message(view, kind, drawn, part):
    """Feedback for a mirrored or rotated outline, e.g. 'side view: the drawing has its tallest section at the back
    (right), the part has it at the front (left)'."""
    (l, r), (lo, hi) = WORDS[view]
    ax_r, ax_u = AXES[view]
    if kind in ("mirror_lr", "rot180"):
        d, p = _side(drawn.sum(axis=0), (l, r)), _side(part.sum(axis=0), (l, r))
        pos = {l: "left", r: "right"}
        if d and p and d != p:
            what = f"its tallest section at the {d}" + (f" ({pos[d]})" if d != pos[d] else "")
            msg = (f"{view} view: the drawing has {what}, the part has it at the {p}"
                   + (f" ({pos[p]})" if p != pos[p] else ""))
        else:
            dm = drawn[:, : drawn.shape[1] // 2].sum() > drawn[:, drawn.shape[1] // 2:].sum()
            msg = (f"{view} view: the drawing has more material on the {l if dm else r} side, the part on the "
                   f"{r if dm else l} side")
        tail = ("the part's outline is this view's outline rotated 180 degrees" if kind == "rot180" else
                f"the part's outline is the drawing's mirrored along {ax_r}")
        return f"{msg}; {tail}. Fix the positions along {ax_r}" + (f" and {ax_u}." if kind == "rot180" else ".")
    d, p = _side(drawn[::-1].sum(axis=1), (lo, hi)), _side(part[::-1].sum(axis=1), (lo, hi))
    if d and p and d != p:
        msg = f"{view} view: the drawing is widest at the {d}, the part at the {p}"
    else:
        dm = drawn[drawn.shape[0] // 2:].sum() > drawn[: drawn.shape[0] // 2].sum()
        msg = f"{view} view: the drawing has more material at the {lo if dm else hi}, the part at the {hi if dm else lo}"
    return f"{msg}; the part's outline is the drawing's mirrored along {ax_u}. Fix the positions along {ax_u}."


def _fmt_ratio(a):
    return f"{a:.2f} : 1" if a >= 1 else f"1 : {1 / a:.2f}"


# ---------------------------------------------------------------- the part
def part_lines(result, view, size_px, pad=6):
    """The part's visible edges in a view, drawn 2 px wide on a canvas whose part box is `size_px` (w, h)."""
    from .drawing import AXES_2D, hlr_lines
    i, j = AXES_2D[view]
    lo, hi = (np.array(result.metrics["bbox"][k]) for k in ("min", "max"))
    w_mm, h_mm = max(hi[i] - lo[i], 1e-6), max(hi[j] - lo[j], 1e-6)
    W, H = size_px
    sx, sy = (W - 1) / w_mm, (H - 1) / h_mm
    canvas = np.zeros((H + 2 * pad, W + 2 * pad), np.uint8)
    visible, _ = hlr_lines(result.solid, view)
    for line in visible:
        p = np.asarray(line)
        pts = np.c_[(p[:, 0] - lo[i]) * sx + pad, (H - 1) - (p[:, 1] - lo[j]) * sy + pad]
        cv2.polylines(canvas, [np.round(pts).astype(np.int32)], False, 1, 2)
    ink = canvas > 0
    lab, _ = ndimage.label(~ink, structure=[[0, 1, 0], [1, 1, 1], [0, 1, 0]])
    outside = np.isin(lab, np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])))
    return ink[pad:-pad, pad:-pad], ~outside[pad:-pad, pad:-pad]


def part_outline(result, view):
    """The part's silhouette in a view, holes filled (SAM outlines have none), cropped to its bbox."""
    sil = result.silhouettes[view].mask
    m, _ = crop_to_bbox(ndimage.binary_fill_holes(sil))
    return m


# ---------------------------------------------------------------- per run
class InputShape:
    """The drawing's sheet and view outlines, computed once per run. Thread-safe: `start_sheet()` right away,
    `start_views(views)` once the read returns, `wait()` before comparing."""

    def __init__(self, drawing, segmenter, out_dir=None):
        self.data, self.img = load_rgb(drawing)
        self.seg, self.out_dir = segmenter, out_dir
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.sheet_future = self.views_future = None
        self.rect, self.M, self.sheet, self.views, self.error = None, np.eye(3), None, {}, None
        self.files = {}
        self.lock = threading.Lock()

    # sheet --------------------------------------------------------
    def start_sheet(self):
        self.sheet_future = self.pool.submit(self._sheet)
        return self

    def _sheet(self):
        img = self.img
        if looks_like_scan(img):
            self.rect, self.sheet = img, {"method": "scan", "rectified": False}
            return
        quad, method = None, None
        try:
            res = self.seg(self.data, "sheet of paper")
            recs = [r for r in res["records"]]
            if recs:
                rec = max(recs, key=lambda r: (r["box"][2] - r["box"][0]) * (r["box"][3] - r["box"][1]))
                q = quad_from_mask(sam.full_mask(rec))
                if quad_ok(q, img.shape):
                    quad, method = q, "sam"
        except (sam.SamError, meta_api.ModelError) as e:
            self.sheet = {"sam_error": str(e)[:200]}
        if quad is None:
            q = quad_from_mask(paper_threshold(img))
            if quad_ok(q, img.shape):
                quad, method = q, "threshold"
        if quad is None:
            self.rect, self.sheet = img, {**(self.sheet or {}), "method": "none", "rectified": False}
            return
        self.rect, self.M = rectify(img, quad)
        self.sheet = {**(self.sheet or {}), "method": method, "rectified": True,
                      "quad": [[round(float(x), 1), round(float(y), 1)] for x, y in quad]}
        if self.out_dir:
            self._save_sheet(quad)

    def _save_sheet(self, quad):
        d = os.path.join(self.out_dir, "shape")
        os.makedirs(d, exist_ok=True)
        found = self.img.copy()
        cv2.polylines(found, [quad.astype(np.int32)], True, (212, 68, 28), max(3, self.img.shape[1] // 300))
        for name, im in (("sheet_found.png", found), ("sheet_rectified.png", self.rect)):
            small = Image.fromarray(im)
            small.thumbnail((640, 640))
            path = os.path.join(d, name)
            small.convert("RGB").save(path.replace(".png", ".jpg"), "JPEG", quality=82)
            self.files[name.replace(".png", "")] = path.replace(".png", ".jpg")

    # views --------------------------------------------------------
    def start_views(self, views):
        self.views_future = self.pool.submit(self._views, views)
        return self

    def _views(self, views):
        if self.sheet_future is not None:
            self.sheet_future.result()
        if self.rect is None:
            self.rect = self.img
        boxes = self.map_boxes(views)
        if not boxes:
            raise Unavailable("the read gave no view boxes")
        H, W = self.rect.shape[:2]
        data = jpeg_bytes(self.rect) if self.sheet.get("rectified") else self.data
        names = list(boxes)
        gray = cv2.cvtColor(self.rect, cv2.COLOR_RGB2GRAY)
        # never cut the part off (fatal); a loose box only lets SAM leak a little
        boxes = {v: [min(a, c) if i < 2 else max(a, c) for i, (a, c) in enumerate(zip(b, refine_box(gray, b)))]
                 for v, b in boxes.items()}
        objs = []
        for k, v in enumerate(names):
            pt = interior_point(gray, boxes[v])
            objs.append({"id": k, "box": boxes[v], "points": [pt] if pt else []})
        masks = {r["id"]: sam.full_mask(r) for r in self.seg(data, sam.object_prompt(objs, W, H))["records"]}
        out = {}
        for k, v in enumerate(names):
            box = boxes[v]
            m = self._largest(masks.get(k), box)
            bw, bh = box[2] - box[0] + 1, box[3] - box[1] + 1
            filled = None if m is None else trim(ink_bounded(ndimage.binary_fill_holes(
                cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)) > 0), gray, box))
            if filled is None or filled.sum() < MIN_FILL * bw * bh:
                out[v] = {"view": v, "box": box, "usable": False, "reason": "SAM found no part outline in the box"}
                continue
            outline, (x1, y1, x2, y2) = crop_to_bbox(filled)
            if (x2 - x1 + 1) < MIN_SPAN * bw or (y2 - y1 + 1) < MIN_SPAN * bh:
                # the box is drawn around the outline, so a mask spanning a fraction of it is a piece (a wall strip)
                out[v] = {"view": v, "box": box, "usable": False,
                          "reason": "SAM's outline covers only part of the view box"}
                continue
            scale = WORK / max(outline.shape)
            size = (max(1, int(round(outline.shape[1] * scale))), max(1, int(round(outline.shape[0] * scale))))
            small = cv2.resize(gray[y1:y2 + 1, x1:x2 + 1], size, interpolation=cv2.INTER_AREA)
            work = cv2.resize(outline.astype(np.uint8), size, interpolation=cv2.INTER_NEAREST) > 0
            holes = closed_regions(ink_mask(small, close=5), work, raw=ink_mask(small))
            out[v] = {"view": v, "box": box, "usable": True, "outline": outline, "outline_box": [x1, y1, x2, y2],
                      "work_size": size, "holes": holes, "point": (objs[k]["points"] or [None])[0]}
            if self.out_dir:
                self._save_input(v, out[v])
        self.views = out
        return out

    def map_boxes(self, views):
        """Read boxes (0-1000 of the original image) -> pixel boxes in the straightened sheet, padded by 3 %."""
        h0, w0 = self.img.shape[:2]
        H, W = self.rect.shape[:2]
        out = {}
        for item in views or []:
            v, b = item.get("view"), item.get("box")
            if v not in VIEW_NAMES or v in out or not b or len(b) != 4:
                continue
            x1, y1, x2, y2 = (float(t) for t in b)
            x1, x2 = sorted((x1, x2))
            y1, y2 = sorted((y1, y2))
            pts = np.float32([[x1, y1], [x2, y1], [x2, y2], [x1, y2]]) / 1000 * np.float32([w0, h0])
            q = cv2.perspectiveTransform(pts[None], self.M)[0]
            x1, y1 = q.min(axis=0)
            x2, y2 = q.max(axis=0)
            px, py = 0.03 * (x2 - x1) + 3, 0.03 * (y2 - y1) + 3
            box = [int(max(0, x1 - px)), int(max(0, y1 - py)), int(min(W - 1, x2 + px)), int(min(H - 1, y2 + py))]
            if box[2] - box[0] >= 12 and box[3] - box[1] >= 12:
                out[v] = box
        return out

    @staticmethod
    def _largest(mask, box):
        if mask is None:
            return None
        m = np.zeros_like(mask)
        x1, y1, x2, y2 = box
        m[y1:y2 + 1, x1:x2 + 1] = mask[y1:y2 + 1, x1:x2 + 1]
        lab, n = ndimage.label(m)
        if n == 0:
            return None
        sizes = ndimage.sum(m, lab, index=np.arange(1, n + 1))
        return lab == 1 + int(np.argmax(sizes))

    def _save_input(self, v, iv):
        """The SAM outline (blue) and detected holes (orange) over the drawing, cropped to the view."""
        d = os.path.join(self.out_dir, "shape")
        os.makedirs(d, exist_ok=True)
        x1, y1, x2, y2 = iv["box"]
        crop = (self.rect[y1:y2 + 1, x1:x2 + 1].astype(np.float32) * 0.6 + 255 * 0.4).astype(np.uint8)
        full = np.zeros(self.rect.shape[:2], bool)
        ox1, oy1, ox2, oy2 = iv["outline_box"]
        full[oy1:oy2 + 1, ox1:ox2 + 1] = iv["outline"]
        m = full[y1:y2 + 1, x1:x2 + 1]
        tint = crop.copy()
        tint[m] = (tint[m] * 0.6 + np.array([40, 110, 220]) * 0.4).astype(np.uint8)
        cnts, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(tint, cnts, -1, (30, 90, 200), 2)
        s = (ox2 - ox1 + 1) / iv["work_size"][0]
        for hole in iv["holes"]:
            c = (int(hole["px"][0] * s + ox1 - x1), int(hole["px"][1] * s + oy1 - y1))
            cv2.circle(tint, c, max(4, int(hole["r_px"] * s) + 3), (212, 68, 28), 2)
        path = os.path.join(d, f"input_{v}.png")
        im = Image.fromarray(tint)
        im.thumbnail((360, 360))
        im.save(path)
        self.files[f"input_{v}"] = path

    def wait(self, timeout=60):
        """The view outlines; raises Unavailable if they could not be computed."""
        if self.views_future is None:
            raise Unavailable("no view boxes from the read")
        try:
            return self.views_future.result(timeout=timeout)
        except Unavailable:
            raise
        except (sam.SamError, meta_api.ModelError) as e:
            raise Unavailable(f"SAM failed: {str(e)[:200]}") from None
        except Exception as e:  # noqa: BLE001 - any failure here only disables the check
            raise Unavailable(f"{type(e).__name__}: {str(e)[:200]}") from None


# ---------------------------------------------------------------- per iteration
def compare_view(iv, result, out_dir=None):
    """One view of the drawing against the part. Returns the per-view result with messages."""
    v = iv["view"]
    rec = {"view": v, "status": "ok", "issues": [], "advisories": [], "files": {}}
    part = part_outline(result, v)
    drawn = iv["outline"]
    a_in = drawn.shape[1] / drawn.shape[0]
    a_part = part.shape[1] / part.shape[0]
    rec["aspect"] = {"drawing": round(a_in, 3), "part": round(a_part, 3)}
    ax_r, ax_u = AXES[v]
    if max(a_in / a_part, a_part / a_in) > ASPECT_TOL:
        rec["issues"].append(f"{v} view: the drawing's outline is {_fmt_ratio(a_in)} (width along {ax_r} : height "
                             f"along {ax_u}), the part's is {_fmt_ratio(a_part)}. Check which sizes run along "
                             f"{ax_r} and {ax_u}.")
    gd, gp = grid(drawn), grid(part)
    scores = {k: round(iou(t(gd), gp), 3) for k, t in TRANSFORMS.items()}
    rec["orientation"] = scores
    best = max((k for k in scores if k != "as_is"), key=scores.get)
    rec["orientation_gap"] = round(scores[best] - scores["as_is"], 3)
    if not rec["issues"] and rec["orientation_gap"] > ORIENT_MARGIN:
        rec["issues"].append(orientation_message(v, best, gd, gp))
    # holes: the part's own line drawing through the same detector, at the drawing's working size
    ink, outline = part_lines(result, v, tuple(iv["work_size"]))
    part_holes = closed_regions(ink, outline)
    rec["holes"] = {"drawing": [{k: h[k] for k in ("u", "v", "area")} for h in iv["holes"]],
                    "part": [{k: h[k] for k in ("u", "v", "area")} for h in part_holes]}
    pairs, miss, extra = match_holes(iv["holes"], part_holes)
    if miss or extra:
        def plist(hs, idx):
            return ", ".join(where(v, hs[i]["u"], hs[i]["v"]) for i in idx)
        msg = (f"{v} view: the drawing shows {len(iv['holes'])} hole(s) or closed outline(s), the part "
               f"{len(part_holes)}.")
        if miss:
            msg += f" In the drawing but not on the part: {plist(iv['holes'], miss)}."
        if extra:
            msg += f" On the part but not in the drawing: {plist(part_holes, extra)}."
        (rec["issues"] if HOLES_BLOCKING else rec["advisories"]).append(msg)
    if rec["issues"]:
        rec["status"] = "issue"
    elif rec["advisories"]:
        rec["status"] = "advisory"
    if out_dir:
        rec["files"]["compare"] = save_compare(v, drawn, part, iv["holes"], part_holes, out_dir)
    return rec


def save_compare(v, drawn, part, holes_in, holes_part, out_dir):
    """The drawing's outline (grey fill, blue edge) under the part's silhouette edge (red), both at the drawing's
    outline size; holes as circles (blue: drawing, red: part)."""
    h, w = drawn.shape
    s = 300 / max(h, w)
    W, H = max(1, int(w * s)), max(1, int(h * s))
    d = cv2.resize(drawn.astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST)
    p = cv2.resize(part.astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST)
    pad = 10
    img = np.full((H + 2 * pad, W + 2 * pad, 3), 255, np.uint8)
    sub = img[pad:pad + H, pad:pad + W]
    sub[d > 0] = (214, 222, 235)
    for m, col, t in ((d, (40, 110, 220), 2), (p, (212, 68, 28), 2)):
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(sub, cnts, -1, col, t)
    for hs, col in ((holes_in, (40, 110, 220)), (holes_part, (212, 68, 28))):
        for hole in hs:
            c = (int(hole["u"] * W), int((1 - hole["v"]) * H))
            r = max(3, int(np.sqrt(hole["area"] * d.sum() / np.pi)))
            cv2.circle(sub, c, r, col, 1)
    path = os.path.join(out_dir, f"shape_{v}.png")
    os.makedirs(out_dir, exist_ok=True)
    Image.fromarray(img).save(path)
    return path


def compare(inp, result, out_dir=None, timeout=60):
    """Every usable view against the part. Returns the `shape_check` event fields:
    {available, note, views: [...], issues: [{source: "shape", message}], advisories: [...], sheet, files, sam}."""
    base = {"available": False, "views": [], "issues": [], "advisories": [], "sheet": None, "files": {}}
    try:
        views = inp.wait(timeout)
    except Unavailable as e:
        return {**base, "note": str(e), "sheet": inp.sheet, "files": dict(inp.files), "sam": _sam(inp)}
    if result is None or not getattr(result, "silhouettes", None):
        return {**base, "note": "the part did not compile", "sheet": inp.sheet, "files": dict(inp.files),
                "sam": _sam(inp)}
    rows, issues, adv = [], [], []
    for v in VIEW_NAMES:
        iv = views.get(v)
        if iv is None:
            continue
        if not iv["usable"]:
            rows.append({"view": v, "status": "unavailable", "note": iv["reason"]})
            continue
        try:
            r = compare_view(iv, result, out_dir)
        except Exception as e:  # noqa: BLE001 - one view failing must not fail the run
            rows.append({"view": v, "status": "unavailable", "note": f"{type(e).__name__}: {str(e)[:160]}"})
            continue
        r["files"]["input"] = inp.files.get(f"input_{v}")
        rows.append(r)
        issues += [{"source": "shape", "message": m} for m in r["issues"]]
        adv += r["advisories"]
    usable = [r for r in rows if r["status"] != "unavailable"]
    return {**base, "available": bool(usable), "note": None if usable else "no usable view outline",
            "views": rows, "issues": issues, "advisories": adv, "sheet": inp.sheet, "files": dict(inp.files),
            "sam": _sam(inp)}


def _sam(inp):
    s = inp.seg
    return {"calls": s.calls, "cost_usd": round(s.cost_usd, 6), "seconds": round(s.seconds, 2)}
