"""Turns the clean test sketches into fake phone photos.

Two variants per part, both scored against the same ground truth:
  <part>__photo.jpg  the clean sketch, printed and photographed: perspective, paper, uneven light, shadow, blur, noise
  <part>__rough.jpg  redrawn shakier in grey pencil, then photographed at a steeper angle with a harder shadow

Deterministic (fixed seeds), so the benchmark stays reproducible.
"""
import os
import tempfile

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import draw_sketches as ds

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "sketches")
PARTS = {"01_plate": ds.part1, "02_l_bracket": ds.part2, "03_block": ds.part3}

SETTINGS = {
    #          tilt  rot  shadow blur noise jpeg
    "photo": (0.06, 4.0, 0.30, 1.2, 6, 72),
    "rough": (0.10, -7.0, 0.45, 1.4, 8, 62),
}


def render_rough(fn, path):
    """Redraw a part shakier, thinner and in grey pencil."""
    ds.INK, ds.LW, ds.OUT = "#3a3d44", 1.4, os.path.dirname(path)
    with plt.xkcd(scale=1.1, length=140, randomness=4):
        plt.rcParams["font.family"] = "DejaVu Sans"
        fn()
    ds.INK, ds.LW, ds.OUT = "#1d2a4a", 1.6, OUT


def paper(img, rng):
    """Replace the flat background with slightly textured off-white paper."""
    g = img.astype(np.float32)
    tex = cv2.GaussianBlur(rng.normal(0, 1, g.shape[:2]).astype(np.float32), (0, 0), 3) * 4
    ink = np.clip((1 - g.mean(axis=2) / 255) * 1.3, 0, 1)  # 0 = paper, 1 = ink
    base = np.array([238, 236, 228], np.float32) + tex[..., None]
    out = base * (1 - ink[..., None]) + g * ink[..., None]
    return np.clip(out, 0, 255)


def photograph(img, variant, rng):
    tilt, rot, shadow, blur, noise, quality = SETTINGS[variant]
    h, w = img.shape[:2]
    pad = int(0.18 * max(h, w))
    H, W = h + 2 * pad, w + 2 * pad
    # desk background: warm grey-brown gradient with grain
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    desk = np.dstack([105 + 30 * xx / W, 92 + 25 * xx / W, 80 + 15 * yy / H])
    desk += cv2.GaussianBlur(rng.normal(0, 1, (H, W)).astype(np.float32), (0, 0), 1.5)[..., None] * 6

    # paper corners, then perspective-warp them like a phone held at an angle
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    jitter = lambda: rng.uniform(-1, 1) * tilt * min(w, h)
    dst = np.float32([[pad + jitter(), pad + jitter() + tilt * h],
                      [pad + w + jitter(), pad + jitter()],
                      [pad + w + jitter() - tilt * w * 0.6, pad + h + jitter()],
                      [pad + jitter() + tilt * w * 0.6, pad + h + jitter() - tilt * h * 0.3]])
    M = cv2.getPerspectiveTransform(src, dst)
    R = cv2.getRotationMatrix2D((W / 2, H / 2), rot, 1.0)
    M = np.vstack([R, [0, 0, 1]]) @ M
    warped = cv2.warpPerspective(img, M, (W, H), flags=cv2.INTER_CUBIC)
    mask = cv2.warpPerspective(np.ones((h, w), np.float32), M, (W, H))
    # soft drop shadow of the paper on the desk
    drop = cv2.GaussianBlur(cv2.warpAffine(mask, np.float32([[1, 0, 10], [0, 1, 14]]), (W, H)), (0, 0), 12)
    desk *= (1 - 0.35 * drop)[..., None]
    out = desk * (1 - mask[..., None]) + warped * mask[..., None]

    # lighting: directional gradient, vignette, and a soft blob shadow (phone/hand)
    light = 1.0 - 0.18 * (xx / W) - 0.10 * (yy / H)
    vign = 1 - 0.25 * (((xx - W / 2) / W) ** 2 + ((yy - H / 2) / H) ** 2) * 2
    cx, cy = rng.uniform(0.15, 0.85) * W, rng.uniform(0.55, 0.95) * H
    blob = np.exp(-(((xx - cx) / (0.35 * W)) ** 2 + ((yy - cy) / (0.22 * H)) ** 2))
    out *= (light * vign * (1 - shadow * blob))[..., None]
    out[..., 2] *= 0.97  # slightly warm white balance (BGR order after conversion below)

    out = cv2.GaussianBlur(out, (0, 0), blur)
    out += rng.normal(0, noise, out.shape)
    out = np.clip(out, 0, 255).astype(np.uint8)
    # phone-ish size
    scale = 1600 / max(out.shape[:2])
    return cv2.resize(out, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA), quality


def main():
    tmp = tempfile.mkdtemp()
    for i, (name, fn) in enumerate(PARTS.items()):
        clean = cv2.cvtColor(cv2.imread(os.path.join(OUT, f"{name}.png")), cv2.COLOR_BGR2RGB)
        render_rough(fn, os.path.join(tmp, "x"))
        rough = cv2.cvtColor(cv2.imread(os.path.join(tmp, f"{name}.png")), cv2.COLOR_BGR2RGB)
        for variant, src in (("photo", clean), ("rough", rough)):
            rng = np.random.default_rng(1000 + 10 * i + (variant == "rough"))
            img, q = photograph(paper(src, rng), variant, rng)
            path = os.path.join(OUT, f"{name}__{variant}.jpg")
            cv2.imwrite(path, cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, q])
            print("wrote", os.path.relpath(path, HERE))


if __name__ == "__main__":
    main()
