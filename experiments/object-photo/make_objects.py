"""Fake phone photos of flat (2.5D) parts lying on an A4 sheet, rendered with a pinhole camera.

  python make_objects.py            # writes photos/<name>.jpg and photos/<name>.json (ground truth)

Physically consistent, unlike the drawing "photos": a real camera (focal length, height, tilt) looks at the sheet,
so the top face appears enlarged by parallax exactly as in a phone photo. Scene: desk, A4 sheet (210 x 297 mm,
z = 0), the compiled part's mesh rotated and placed on it, Lambert shading, a soft shadow cast along the light
direction, uneven light, noise and JPEG. Ground truth: the spec, the placement and the camera.
Deterministic (fixed seeds).
"""
import io
import json
import os
import sys

import cv2
import numpy as np
import trimesh

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from hephaestus.compile import compile  # noqa: E402
from hephaestus.render import rasterize  # noqa: E402

A4 = (210.0, 297.0)
#        name                 part            rot   at (mm)      height tilt yaw   colour (RGB 0-1)     light
SHOTS = [("plate__a",         "plate",        12,  (-10, 20),    380,   6,  -4,  (0.62, 0.64, 0.67), (0.5, -0.4)),
         ("plate__b",         "plate",        -63, (15, -30),    330,   14, 9,   (0.18, 0.19, 0.22), (-0.6, -0.3)),
         ("pocket_plate__a",  "pocket_plate", 4,   (0, 10),      400,   8,  2,   (0.70, 0.71, 0.73), (0.4, 0.5)),
         ("pocket_plate__b",  "pocket_plate", 81,  (-5, -15),    350,   15, -12, (0.25, 0.27, 0.32), (0.7, -0.2)),
         ("shaft_collar__a",  "shaft_collar", 30,  (20, 25),     360,   5,  6,   (0.66, 0.66, 0.66), (-0.4, -0.5)),
         ("shaft_collar__b",  "shaft_collar", -100, (-25, -20),  320,   12, -6,  (0.20, 0.20, 0.20), (0.5, 0.4)),
         ("block__a",         "block",        -8,  (5, -5),      420,   7,  3,   (0.68, 0.69, 0.71), (0.5, -0.5)),
         ("block__b",         "block",        55,  (-15, 25),    340,   13, -15, (0.30, 0.22, 0.20), (-0.5, 0.3))]
W, H, F = 1600, 1200, 1300.0          # image px, focal length px (~ a phone's main camera, 63 deg horizontal)
SS = 2                                # supersampling


def mesh(part):
    r = compile(os.path.join(ROOT, "examples", f"{part}.json"))
    m = trimesh.load(io.BytesIO(r.stl), file_type="stl")
    lo, hi = m.bounds
    v = np.asarray(m.vertices, float) - [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]]   # centred, on z = 0
    return v, np.asarray(m.faces), r


def place(v, rot, at):
    a = np.radians(rot)
    Rz = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
    return v @ Rz.T + [at[0], at[1], 0]


def camera(height, tilt, yaw, at):
    """Camera above the sheet, looking at `at` from `height`, tilted by `tilt` deg and turned by `yaw` deg."""
    t, y = np.radians(tilt), np.radians(yaw)
    target = np.array([at[0] * 0.3, at[1] * 0.3, 0.0])
    back = np.array([np.sin(y), -np.cos(y), 0.0]) * height * np.tan(t)
    C = target - back + [0, 0, height]
    fwd = (target - C) / np.linalg.norm(target - C)
    up_hint = np.array([np.sin(y), np.cos(y), 0.0])
    right = np.cross(fwd, up_hint)
    right /= np.linalg.norm(right)
    down = np.cross(fwd, right)
    R = np.stack([right, down, fwd])          # world -> camera (x right, y down, z forward)
    return C, R


def project(P, C, R, f, cx, cy):
    pc = (P - C) @ R.T
    return np.c_[f * pc[:, 0] / pc[:, 2] + cx, f * pc[:, 1] / pc[:, 2] + cy], pc[:, 2]


def render(name, part, rot, at, height, tilt, yaw, colour, light, rng):
    v, faces, result = mesh(part)
    v = place(v, rot, at)
    C, R = camera(height, tilt, yaw, at)
    w, h, f = W * SS, H * SS, F * SS
    cx, cy = w / 2, h / 2
    L = np.array([light[0], light[1], -1.0])
    L /= np.linalg.norm(L)                     # direction the light travels
    # ---- background: ray from every pixel to the plane z = 0
    uu, vv = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)
    d = np.stack([(uu - cx) / f, (vv - cy) / f, np.ones_like(uu)], -1) @ R
    tt = -C[2] / d[..., 2]
    X, Y = C[0] + tt * d[..., 0], C[1] + tt * d[..., 1]
    on_sheet = (np.abs(X) <= A4[0] / 2) & (np.abs(Y) <= A4[1] / 2)
    tex = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 3 * SS)
    paper = 0.93 + 0.012 * tex
    desk = 0.38 + 0.05 * cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 8 * SS)
    base = np.where(on_sheet, paper, desk)
    img = np.stack([base * 1.0, base * 0.985, base * 0.95], -1) if True else None
    img[~on_sheet] *= [1.0, 0.86, 0.72]
    # ---- shadow: the mesh projected along the light onto z = 0, rasterised in sheet mm (4 px/mm), softened
    sp = v - np.outer(v[:, 2] / L[2], L)
    s_px, ox, oy = 4.0, -A4[0] / 2 - 60, -A4[1] / 2 - 60
    sw, sh = int((A4[0] + 120) * s_px), int((A4[1] + 120) * s_px)
    smask = rasterize(sp[faces][:, :, :2], (ox, oy), 1 / s_px, sw, sh).astype(np.float32)
    smask = cv2.GaussianBlur(smask, (0, 0), 2.5 * s_px)
    si = np.clip(((X - ox) * s_px).astype(int), 0, sw - 1)
    sj = np.clip(((Y - oy) * s_px).astype(int), 0, sh - 1)
    img *= (1 - 0.45 * smask[sj, si])[..., None]
    # ---- the part: z-buffered triangles, Lambert shading
    p2, z = project(v, C, R, f, cx, cy)
    tri2, triz = p2[faces], z[faces]
    n = np.cross(v[faces][:, 1] - v[faces][:, 0], v[faces][:, 2] - v[faces][:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    shade = 0.45 + 0.6 * np.clip(n @ -L, 0, 1)
    zbuf = np.full((h, w), np.inf)
    col = np.zeros((h, w))
    for k in range(len(faces)):
        a, b, c = tri2[k]
        x0, x1 = int(max(np.floor(min(a[0], b[0], c[0])), 0)), int(min(np.ceil(max(a[0], b[0], c[0])), w - 1))
        y0, y1 = int(max(np.floor(min(a[1], b[1], c[1])), 0)), int(min(np.ceil(max(a[1], b[1], c[1])), h - 1))
        if x1 < x0 or y1 < y0:
            continue
        xs, ys = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        den = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(den) < 1e-12:
            continue
        l1 = ((b[1] - c[1]) * (xs - c[0]) + (c[0] - b[0]) * (ys - c[1])) / den
        l2 = ((c[1] - a[1]) * (xs - c[0]) + (a[0] - c[0]) * (ys - c[1])) / den
        l3 = 1 - l1 - l2
        inside = (l1 >= 0) & (l2 >= 0) & (l3 >= 0)
        zz = l1 * triz[k, 0] + l2 * triz[k, 1] + l3 * triz[k, 2]
        sub = zbuf[y0:y1 + 1, x0:x1 + 1]
        upd = inside & (zz < sub)
        sub[upd] = zz[upd]
        col[y0:y1 + 1, x0:x1 + 1][upd] = shade[k]
    obj = np.isfinite(zbuf)
    img[obj] = np.outer(col[obj], colour) * 1.05
    # ---- phone: uneven light, vignette, blur, noise, JPEG
    gx, gy = np.meshgrid(np.linspace(-1, 1, w), np.linspace(-1, 1, h))
    img *= (1 - 0.12 * (gx * light[0] + gy * light[1]) - 0.18 * (gx ** 2 + gy ** 2) * 0.5)[..., None]
    img = cv2.resize(np.clip(img, 0, 1).astype(np.float32), (W, H), interpolation=cv2.INTER_AREA)
    img = cv2.GaussianBlur(img, (0, 0), 0.8)
    img = np.clip(img * 255 + rng.normal(0, 3.0, img.shape), 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 82])
    os.makedirs(os.path.join(HERE, "photos"), exist_ok=True)
    with open(os.path.join(HERE, "photos", f"{name}.jpg"), "wb") as fh:
        fh.write(buf.tobytes())
    truth = {"part": part, "rot_deg": rot, "at_mm": list(at), "camera_mm": [round(float(x), 2) for x in C],
             "camera_height_mm": height, "tilt_deg": tilt, "focal_px": F, "image": [W, H],
             "thickness_mm": round(float(result.metrics["bbox"]["size"][2]), 3),
             "bbox_mm": [round(float(x), 3) for x in result.metrics["bbox"]["size"]]}
    with open(os.path.join(HERE, "photos", f"{name}.json"), "w") as fh:
        json.dump(truth, fh, indent=1)
    return truth


def main():
    for k, (name, part, rot, at, height, tilt, yaw, colour, light) in enumerate(SHOTS):
        if len(sys.argv) > 1 and name not in sys.argv[1:]:
            continue
        t = render(name, part, rot, at, height, tilt, yaw, colour, light, np.random.default_rng(100 + k))
        print(name, t["camera_mm"], flush=True)


if __name__ == "__main__":
    main()
