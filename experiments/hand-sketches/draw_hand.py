"""Realistic hand sketches of the 8 benchmark parts: handwriting, not to scale, sloppy.

  python draw_hand.py            # writes sketches/<part>.png (a scan) and sketches/<part>__photo.jpg

The benchmark drawings (experiments/reading-spike/draw_sketches.py, experiments/benchmark/draw_parts.py) are drawn
again through a wrapper around the matplotlib axes that makes them look hand-made, while every written number
stays true (ground truth = the same specs):
- handwriting fonts (Caveat, Patrick Hand, Kalam from @fontsource, SIL OFL; see fonts/) for all text;
- NOT to scale: a smooth monotonic warp per sketch stretches regions by 0.8-1.25x and shifts views against each
  other by a few mm, so proportions and alignment are off by 10-30 %;
- wobbly lines with overshoot, imperfect circles, sloppy text placement;
- some dimension arrows missing or one-sided; on 3 sketches one number written wrong, crossed out and corrected;
- one sketch on squared paper (5 mm grid).
Deterministic (fixed seeds).
"""
import math
import os
import sys

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.patches import Circle  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "experiments", "reading-spike"))
sys.path.insert(0, os.path.join(ROOT, "experiments", "benchmark"))
import draw_sketches as ds  # noqa: E402
import draw_parts as dp  # noqa: E402
import make_photos as mp  # noqa: E402

OUT = os.path.join(HERE, "sketches")
FONTS = {n: font_manager.FontProperties(fname=os.path.join(HERE, "fonts", f"{n}.ttf"))
         for n in ("caveat", "patrick-hand", "kalam")}
#            part                  font            seed  crossed-out  grid paper
SKETCHES = [("01_plate",          "caveat",        11,  True,        False),
            ("02_l_bracket",      "patrick-hand",  12,  False,       False),
            ("03_block",          "kalam",         13,  False,       True),
            ("04_bushing",        "caveat",        14,  True,        False),
            ("05_enclosure",      "patrick-hand",  15,  False,       False),
            ("06_angled_bracket", "kalam",         16,  False,       False),
            ("07_pocket_plate",   "caveat",        17,  True,        False),
            ("08_shaft_collar",   "patrick-hand",  18,  False,       False)]
FUNCS = {"01_plate": ds.part1, "02_l_bracket": ds.part2, "03_block": ds.part3, **dp.PARTS}


class Warp:
    """x' = fx(x) + gx(y), y' = fy(y) + gy(x): piecewise-linear, monotonic stretches (0.8-1.25x every 25 mm) and
    smooth relative shifts (+-4 mm every 35 mm) that move views out of alignment."""

    def __init__(self, rng, lo=-150, hi=300):
        self.k = np.arange(lo, hi + 25, 25.0)
        self.fx = np.concatenate([[lo], lo + np.cumsum(np.diff(self.k) * rng.uniform(0.8, 1.25, len(self.k) - 1))])
        self.fy = np.concatenate([[lo], lo + np.cumsum(np.diff(self.k) * rng.uniform(0.8, 1.25, len(self.k) - 1))])
        self.s = np.arange(lo, hi + 35, 35.0)
        self.gx, self.gy = rng.uniform(-4, 4, len(self.s)), rng.uniform(-4, 4, len(self.s))

    def __call__(self, x, y):
        x, y = np.asarray(x, float), np.asarray(y, float)
        return (np.interp(x, self.k, self.fx) + np.interp(y, self.s, self.gx),
                np.interp(y, self.k, self.fy) + np.interp(x, self.s, self.gy))


class HandAxes:
    """Wraps an Axes: every line, patch, arrow and text goes through the warp and a hand-drawn style."""

    def __init__(self, ax, rng, font, crossed_out, grid):
        self.ax, self.rng, self.font = ax, rng, font
        self.warp = Warp(rng)
        self.crossed_out, self.grid = crossed_out, grid
        self.numbers_seen = 0

    def __getattr__(self, name):
        return getattr(self.ax, name)

    # ---- strokes
    def _stroke(self, xs, ys, color, lw, ls="-"):
        xs, ys = np.asarray(xs, float), np.asarray(ys, float)
        seg = np.hypot(np.diff(xs), np.diff(ys))
        n = np.maximum(2, (seg / 0.8).astype(int))
        px = np.concatenate([np.linspace(xs[i], xs[i + 1], n[i], endpoint=False) for i in range(len(seg))] + [xs[-1:]])
        py = np.concatenate([np.linspace(ys[i], ys[i + 1], n[i], endpoint=False) for i in range(len(seg))] + [ys[-1:]])
        if len(px) > 2 and ls == "-":            # overshoot at the ends, like a pen that does not stop exactly
            for end in (0, -1):
                j = 1 if end == 0 else -2
                d = np.array([px[end] - px[j], py[end] - py[j]])
                d /= max(np.linalg.norm(d), 1e-9)
                o = self.rng.uniform(0, 1.2)
                if end == 0:
                    px, py = np.r_[px[0] + d[0] * o, px], np.r_[py[0] + d[1] * o, py]
                else:
                    px, py = np.r_[px, px[-1] + d[0] * o], np.r_[py, py[-1] + d[1] * o]
        wx, wy = self.warp(px, py)
        t = np.linspace(0, 1, len(wx))
        wob = sum(self.rng.uniform(0.15, 0.4) * np.sin(2 * np.pi * self.rng.uniform(0.5, 3) * t + self.rng.uniform(0, 6))
                  for _ in range(2))
        nx, ny = np.gradient(wy), -np.gradient(wx)
        nn = np.maximum(np.hypot(nx, ny), 1e-9)
        self.ax.plot(wx + wob * nx / nn, wy + wob * ny / nn, color=color, lw=lw * self.rng.uniform(0.85, 1.15),
                     ls=ls, solid_capstyle="round")

    def plot(self, xs, ys, color=None, lw=1.0, ls="-", **kw):
        self._stroke(xs, ys, color or ds.INK, lw, ls)
        return []

    def add_patch(self, p):
        path = p.get_path().interpolated(80)
        v = p.get_patch_transform().transform(path.vertices)
        if isinstance(p, Circle):                  # imperfect: a lumpy radius, and the loop does not quite close
            c = np.array(p.center)
            r = np.linalg.norm(v - c, axis=1, keepdims=True)
            th = np.arctan2(v[:, 1] - c[1], v[:, 0] - c[0])
            lump = 1 + 0.04 * np.sin(2 * th + self.rng.uniform(0, 6)) + 0.02 * np.sin(3 * th + self.rng.uniform(0, 6))
            v = c + (v - c) / r * r * lump[:, None]
            v = v[: int(len(v) * self.rng.uniform(0.94, 1.03))]
        ls = p.get_linestyle() if isinstance(p.get_linestyle(), str) else "-"
        self._stroke(v[:, 0], v[:, 1], p.get_edgecolor(), p.get_linewidth(), "--" if ls in ("--", "dashed") else "-")
        return p

    # ---- arrows and text
    def annotate(self, text, xy, xytext=None, arrowprops=None, **kw):
        (x1, y1), (x2, y2) = self.warp(*xy), self.warp(*(xytext or xy))
        if arrowprops:
            style = arrowprops.get("arrowstyle", "->")
            r = self.rng.uniform()
            if style == "<->":
                style = "-" if r < 0.2 else "->" if r < 0.35 else style     # arrows missing or one-sided
            elif r < 0.2:
                style = "-"
            arrowprops = {**arrowprops, "arrowstyle": style, "lw": arrowprops.get("lw", 0.9) * 1.1}
        if text:
            props = self._props(kw)
            return self.ax.annotate(text, (float(x1), float(y1)), (float(x2), float(y2)), arrowprops=arrowprops,
                                    **props)
        return self.ax.annotate("", (float(x1), float(y1)), (float(x2), float(y2)), arrowprops=arrowprops)

    def _props(self, kw):
        props = {k: v for k, v in kw.items() if k not in ("fontweight", "fontsize", "fontfamily")}
        props["fontproperties"] = self.font
        props["fontsize"] = kw.get("fontsize", 12) * 1.45
        return props

    def text(self, x, y, s, **kw):
        wx, wy = self.warp(x, y)
        wx, wy = float(wx + self.rng.uniform(-1.5, 1.5)), float(wy + self.rng.uniform(-1.0, 1.0))
        props = self._props(kw)
        props["rotation"] = kw.get("rotation", 0) + self.rng.uniform(-4, 4)
        number = s.replace(".", "", 1).isdigit()
        if number:
            self.numbers_seen += 1
        if number and self.crossed_out and self.numbers_seen >= 2 and kw.get("rotation", 0) == 0:
            self.crossed_out = False
            # a wrong number, struck through, and the right one written next to it
            wrong = str(int(round(float(s) * self.rng.choice([0.8, 1.2]))))
            t = self.ax.text(wx - 2.5, wy, wrong, **{**props, "ha": "right"})
            # strike it through with dashes drawn as text, same anchor, alignment and font: they land on the digits
            # whatever the axis limits and the font metrics turn out to be
            for rot in (8, -6):
                self.ax.text(wx - 2.5, wy, "—" * len(wrong), **{**props, "ha": "right", "rotation": rot,
                                                                    "rotation_mode": "anchor"})
            self.ax.text(wx + 0.5, wy, s, **{**props, "ha": "left"})
            return t
        return self.ax.text(wx, wy, s, **props)

    # ---- frame
    def set_xlim(self, lo, hi):
        a, _ = self.warp(np.array([lo, hi]), np.array([0, 0]))
        self.ax.set_xlim(a[0] - 4, a[1] + 4)

    def set_ylim(self, lo, hi):
        _, b = self.warp(np.array([0, 0]), np.array([lo, hi]))
        self.ax.set_ylim(b[0] - 4, b[1] + 4)
        if self.grid:                              # squared paper, regular in the paper's own coordinates
            x0, x1 = self.ax.get_xlim()
            y0, y1 = self.ax.get_ylim()
            for x in np.arange(math.floor(x0 / 5) * 5, x1, 5):
                self.ax.axvline(x, color="#9cc3e6", lw=0.5, zorder=0)
            for y in np.arange(math.floor(y0 / 5) * 5, y1, 5):
                self.ax.axhline(y, color="#9cc3e6", lw=0.5, zorder=0)


def draw(name, font, seed, crossed, grid):
    rng = np.random.default_rng(seed)
    real = plt.subplots

    def subplots(*a, **kw):
        fig, ax = real(*a, **kw)
        return fig, HandAxes(ax, rng, FONTS[font], crossed, grid)

    plt.subplots = subplots
    ds.OUT = OUT
    try:
        FUNCS[name]()
    finally:
        plt.subplots = real


def main():
    os.makedirs(OUT, exist_ok=True)
    only = sys.argv[1:]
    for i, (name, font, seed, crossed, grid) in enumerate(SKETCHES):
        if only and name not in only:
            continue
        draw(name, font, seed, crossed, grid)
        clean = cv2.cvtColor(cv2.imread(os.path.join(OUT, f"{name}.png")), cv2.COLOR_BGR2RGB)
        rng = np.random.default_rng(3000 + i)
        img, q = mp.photograph(mp.paper(clean, rng), "photo", rng)
        cv2.imwrite(os.path.join(OUT, f"{name}__photo.jpg"), cv2.cvtColor(img, cv2.COLOR_RGB2BGR),
                    [cv2.IMWRITE_JPEG_QUALITY, q])
        print("drew", name, flush=True)


if __name__ == "__main__":
    main()
