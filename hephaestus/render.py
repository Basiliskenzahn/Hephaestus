"""Orthographic silhouettes and STL bytes from a triangle mesh.

Silhouettes are binary masks: True where the part covers the view. A pixel is set when its centre lies
inside a projected triangle, so through-holes stay open and the mask has no anti-aliasing to drift.

Views (image right, image up):
  top   -> (x, y)    looking down -Z
  front -> (x, z)    looking along +Y
  side  -> (y, z)    looking along -X
`origin_mm` is the (right, up) position in mm of the image's lower-left corner; pixel (row r, col c) has its
centre at origin + ((c + 0.5) * s, (height - r - 0.5) * s).
"""
import io
import struct
from dataclasses import dataclass

import numpy as np
from PIL import Image

VIEWS = {"top": (0, 1), "front": (0, 2), "side": (1, 2)}
AXIS_NAMES = "xyz"


@dataclass
class Silhouette:
    view: str
    mask: np.ndarray          # bool, shape (height, width), row 0 at the top of the image
    mm_per_px: float
    origin_mm: tuple          # (right, up) of the image's lower-left corner
    axes: tuple               # world axes shown as (right, up), e.g. ("x", "y")

    def png(self):
        buf = io.BytesIO()
        Image.fromarray(np.where(self.mask, 255, 0).astype(np.uint8), "L").save(buf, "PNG")
        return buf.getvalue()

    def to_px(self, right_mm, up_mm):
        """(row, col) of the pixel containing a point given in mm."""
        col = int(np.floor((right_mm - self.origin_mm[0]) / self.mm_per_px))
        row = self.mask.shape[0] - 1 - int(np.floor((up_mm - self.origin_mm[1]) / self.mm_per_px))
        return row, col

    def at(self, right_mm, up_mm):
        row, col = self.to_px(right_mm, up_mm)
        return bool(self.mask[row, col])

    def meta(self):
        return {"mm_per_px": self.mm_per_px, "origin_mm": [round(o, 6) for o in self.origin_mm],
                "size_px": [int(self.mask.shape[1]), int(self.mask.shape[0])], "axes": list(self.axes)}


def rasterize(tri2d, origin, s, width, height):
    """Union of 2D triangles (M, 3, 2) in mm onto a (height, width) grid. Returns mask with row 0 at the bottom."""
    mask = np.zeros((height, width), bool)
    p = (tri2d - np.asarray(origin)) / s - 0.5          # pixel-centre coordinates are integers
    a, b, c = p[:, 0], p[:, 1], p[:, 2]
    area = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    lo = np.ceil(p.min(axis=1) - 1e-9).astype(int)
    hi = np.floor(p.max(axis=1) + 1e-9).astype(int)
    eps = 1e-9
    for i in np.nonzero(np.abs(area) > 1e-12)[0]:
        x0, y0 = max(lo[i, 0], 0), max(lo[i, 1], 0)
        x1, y1 = min(hi[i, 0], width - 1), min(hi[i, 1], height - 1)
        if x1 < x0 or y1 < y0:
            continue
        xs, ys = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
        sgn = 1.0 if area[i] > 0 else -1.0
        inside = np.ones(xs.shape, bool)
        for (pa, pb) in ((a[i], b[i]), (b[i], c[i]), (c[i], a[i])):
            e = (pb[0] - pa[0]) * (ys - pa[1]) - (pb[1] - pa[1]) * (xs - pa[0])
            inside &= sgn * e >= -eps
        mask[y0:y1 + 1, x0:x1 + 1] |= inside
    return mask


def silhouettes(vertices, triangles, bbox_min, bbox_max, mm_per_px=0.2, margin_mm=2.0):
    """Top, front and side masks of a mesh, framed on the part's bounding box plus a margin."""
    tris = vertices[triangles]                           # (M, 3, 3)
    out = {}
    for view, (i, j) in VIEWS.items():
        s = mm_per_px
        origin = (np.floor((bbox_min[i] - margin_mm) / s) * s, np.floor((bbox_min[j] - margin_mm) / s) * s)
        width = int(np.ceil((bbox_max[i] + margin_mm - origin[0]) / s))
        height = int(np.ceil((bbox_max[j] + margin_mm - origin[1]) / s))
        m = rasterize(tris[:, :, [i, j]], origin, s, width, height)
        out[view] = Silhouette(view, np.flipud(m).copy(), s, (float(origin[0]), float(origin[1])),
                               (AXIS_NAMES[i], AXIS_NAMES[j]))
    return out


def count_holes(mask):
    """Number of enclosed background regions (holes) in a mask, ignoring background touching the border."""
    from scipy import ndimage
    labels, n = ndimage.label(~mask)
    border = set(np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])))
    return sum(1 for k in range(1, n + 1) if k not in border)


def stl_bytes(vertices, triangles, name="hephaestus"):
    """Binary STL with facet normals from the triangle winding."""
    tris = vertices[triangles].astype(np.float32)
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    length = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.divide(n, length, out=np.zeros_like(n), where=length > 0)
    rec = np.zeros(len(tris), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
    rec["n"], rec["v"] = n, tris
    header = name.encode()[:80].ljust(80, b" ")
    return header + struct.pack("<I", len(tris)) + rec.tobytes()
