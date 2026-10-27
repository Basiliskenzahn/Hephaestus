"""Five harder benchmark drawings in the reading-spike sketch style, plus photo/rough variants.

Ground truth: examples/{bushing,enclosure,angled_bracket,pocket_plate,shaft_collar}.json and examples/callouts/.
  python draw_parts.py          # writes sketches/04_*.png .. 08_*.png and their __photo / __rough .jpg variants
"""
import math
import os
import sys
import tempfile

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import Circle, FancyBboxPatch  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "reading-spike"))
import draw_sketches as ds  # noqa: E402
import make_photos as mp  # noqa: E402

OUT = os.path.join(HERE, "sketches")
line, rect, circle, dim, leader, label, slot = ds.line, ds.rect, ds.circle, ds.dim, ds.leader, ds.label, ds.slot


def hidden(ax, x1, y1, x2, y2):
    ds.line(ax, x1, y1, x2, y2, lw=1.0, ls="--")


def rounded(ax, x, y, w, h, r):
    ax.add_patch(FancyBboxPatch((x + r, y + r), w - 2 * r, h - 2 * r, boxstyle=f"round,pad={r}", fill=False,
                                ec=ds.INK, lw=ds.LW))


def bare_circle(ax, cx, cy, d, ls="-"):
    ax.add_patch(Circle((cx, cy), d / 2, fill=False, ec=ds.INK, lw=ds.LW if ls == "-" else 0.8, ls=ls))


def part4():
    """Flanged bushing: flange Ø40 x 5, body Ø24, length 30, bore Ø16, 4x Ø4 on PCD 32."""
    fig, ax = plt.subplots(figsize=(9, 10))
    c = 20
    bare_circle(ax, c, c, 40)
    bare_circle(ax, c, c, 24)
    circle(ax, c, c, 16)
    bare_circle(ax, c, c, 32, ls="-.")
    for a in range(4):
        circle(ax, c + 16 * math.cos(a * math.pi / 2), c + 16 * math.sin(a * math.pi / 2), 4, centre=False)
    leader(ax, (c + 14.1, c + 14.1), (c + 26, c + 32), "Ø40")
    leader(ax, (c - 8.5, c + 8.5), (c - 30, c + 30), "Ø24")
    leader(ax, (c + 5.7, c - 5.7), (c + 26, c - 26), "Ø16 thru")
    leader(ax, (c + 17.4, c + 1.4), (c + 26, c + 10), "4x Ø4 thru")
    leader(ax, (c - 11.3, c - 11.3), (c - 34, c - 26), "PCD Ø32")
    label(ax, c, -34, "TOP")
    oy = 72
    rect(ax, 0, oy, 40, 5)
    rect(ax, 8, oy + 5, 24, 25)
    for x in (12, 28):
        hidden(ax, x, oy, x, oy + 30)
    for x in (2, 6, 34, 38):
        hidden(ax, x, oy, x, oy + 5)
    dim(ax, (0, oy), (0, oy + 30), -8, "30", horizontal=False)
    dim(ax, (40, oy), (40, oy + 5), 47, "5", horizontal=False, fs=11)
    label(ax, c, oy - 10, "FRONT")
    ax.text(-30, 112, "Flanged bushing, all dims mm", fontsize=13, color=ds.INK)
    ds.finish(fig, ax, "04_bushing.png", (-40, 75), (-40, 118))


def part5():
    """Enclosure 100 x 60 x 40, R6 corners, 2 mm walls, open top, Ø8 cable hole front, 4x Ø3.2 floor holes."""
    fig, ax = plt.subplots(figsize=(11, 10))
    rounded(ax, 0, 0, 100, 60, 6)
    rounded(ax, 2, 2, 96, 56, 4)
    for x, y in ((10, 10), (90, 10), (10, 50), (90, 50)):
        circle(ax, x, y, 3.2)
    dim(ax, (0, 0), (100, 0), -9, "100")
    dim(ax, (0, 0), (0, 60), -9, "60", horizontal=False)
    dim(ax, (0, 10), (10, 10), -18, "10", fs=11)
    dim(ax, (100, 0), (100, 10), 108, "10", horizontal=False, fs=11)
    leader(ax, (98.2, 58.2), (104, 70), "R6 (4x)")
    leader(ax, (99, 30), (108, 40), "wall 2")
    leader(ax, (88.8, 51.2), (40, 70), "4x Ø3.2 thru floor")
    label(ax, 50, -30, "TOP (open)")
    oy = 85
    rect(ax, 0, oy, 100, 40)
    hidden(ax, 2, oy + 2, 98, oy + 2)
    hidden(ax, 2, oy + 2, 2, oy + 40)
    hidden(ax, 98, oy + 2, 98, oy + 40)
    circle(ax, 50, oy + 20, 8)
    dim(ax, (0, oy), (0, oy + 40), -9, "40", horizontal=False)
    dim(ax, (100, oy), (100, oy + 20), 108, "20", horizontal=False, fs=11)
    leader(ax, (52.8, oy + 22.8), (62, oy + 50), "Ø8 thru front wall, 0.5 x 45°")
    label(ax, 50, oy - 10, "FRONT")
    ax.text(-15, 140, "Enclosure, open top, all dims mm", fontsize=13, color=ds.INK)
    ds.finish(fig, ax, "05_enclosure.png", (-30, 130), (-38, 146))


def part6():
    """Angled bracket: 80 long, 30 deep, flat top 40 high for 30, slope to 15 at the right; 2x Ø6, Ø8 cross hole."""
    fig, ax = plt.subplots(figsize=(10, 10))
    pts = [(0, 0), (80, 0), (80, 15), (30, 40), (0, 40), (0, 0)]
    oy = 55
    for (a, b), (c, d) in zip(pts, pts[1:]):
        line(ax, a, oy + b, c, oy + d)
    for x in (12, 18):
        hidden(ax, x, oy, x, oy + 40)
    circle(ax, 60, oy + 10, 8)
    dim(ax, (0, oy), (80, oy), oy - 8, "80")
    dim(ax, (0, oy), (0, oy + 40), -8, "40", horizontal=False)
    dim(ax, (80, oy), (80, oy + 15), 88, "15", horizontal=False, fs=11)
    dim(ax, (0, oy + 40), (30, oy + 40), oy + 47, "30", fs=11)
    dim(ax, (60, oy + 10), (80, oy + 10), oy + 26, "20", fs=11)
    dim(ax, (80, oy), (80, oy + 10), 96, "10", horizontal=False, fs=11)
    leader(ax, (57.2, oy + 12.8), (38, oy + 22), "Ø8 thru")
    label(ax, 40, oy - 19, "FRONT")
    rect(ax, 0, 0, 80, 30)
    line(ax, 30, 0, 30, 30)
    circle(ax, 15, 8, 6)
    circle(ax, 15, 22, 6)
    dim(ax, (0, 0), (0, 30), -8, "30", horizontal=False)
    dim(ax, (0, 30), (15, 30), 38, "15", fs=11)
    dim(ax, (80, 0), (80, 8), 88, "8", horizontal=False, fs=11)
    leader(ax, (17.1, 24.1), (36, 14), "2x Ø6 thru")
    label(ax, 40, -16, "TOP")
    ax.text(-15, 112, "Angled bracket, all dims mm", fontsize=13, color=ds.INK)
    ds.finish(fig, ax, "06_angled_bracket.png", (-25, 110), (-24, 118))


def part7():
    """Pocket plate 100 x 60 x 10: centred 50 x 30 pocket R3 x 4 deep, 4x Ø6.6 thru with Ø11 x 6.4 counterbore."""
    fig, ax = plt.subplots(figsize=(11, 9.5))
    rect(ax, 0, 0, 100, 60)
    rounded(ax, 25, 15, 50, 30, 3)
    for x, y in ((10, 10), (90, 10), (10, 50), (90, 50)):
        circle(ax, x, y, 6.6)
        bare_circle(ax, x, y, 11)
    dim(ax, (0, 0), (100, 0), -18, "100")
    dim(ax, (0, 0), (0, 60), -18, "60", horizontal=False)
    dim(ax, (0, 10), (10, 10), -8, "10", fs=11)
    dim(ax, (10, 10), (90, 10), -8.1, "80", fs=11)
    dim(ax, (100, 0), (100, 10), 108, "10", horizontal=False, fs=11)
    dim(ax, (100, 10), (100, 50), 108.1, "40", horizontal=False, fs=11)
    dim(ax, (25, 45), (75, 45), 52, "50", fs=11)
    dim(ax, (75, 15), (75, 45), 82, "30", horizontal=False, fs=11)
    leader(ax, (26, 44), (16, 30), "R3")
    leader(ax, (60, 30), (60, 70), "pocket 4 deep")
    leader(ax, (93.9, 53.9), (64, 78), "4x Ø6.6 thru, counterbore Ø11 x 6.4 deep")
    label(ax, 50, -34, "TOP")
    oy = 95
    rect(ax, 0, oy, 100, 10)
    hidden(ax, 25, oy + 6, 75, oy + 6)
    for x in (4.5, 15.5, 84.5, 95.5):
        hidden(ax, x, oy + 3.6, x, oy + 10)
    dim(ax, (0, oy), (0, oy + 10), -8, "10", horizontal=False, fs=11)
    label(ax, 50, oy - 10, "FRONT")
    ax.text(-15, 118, "Pocket plate, all dims mm", fontsize=13, color=ds.INK)
    ds.finish(fig, ax, "07_pocket_plate.png", (-30, 125), (-40, 124))


def part8():
    """Shaft collar: Ø40 x 15 ring, Ø20 bore, 2 mm slit on the right, Ø4 clamp hole across it at x = 35."""
    fig, ax = plt.subplots(figsize=(9, 10))
    c = 20
    bare_circle(ax, c, c, 40)
    circle(ax, c, c, 20)
    for y in (19, 21):
        line(ax, c + math.sqrt(100 - 1), y, c + math.sqrt(400 - 1), y)
    for x in (33, 37):
        h = math.sqrt(400 - (x - c) ** 2)
        hidden(ax, x, c - h, x, c + h)
    dim(ax, (0, c), (35, c), -8, "35", fs=11)
    leader(ax, (39.5, 20.5), (48, 32), "slit 2")
    leader(ax, (c - 14.1, c + 14.1), (c - 30, c + 30), "Ø40")
    leader(ax, (c - 7.1, c - 7.1), (c - 44, c - 12), "Ø20 thru")
    label(ax, c, -34, "TOP")
    oy = 65
    rect(ax, 0, oy, 40, 15)
    for x in (10, 30):
        hidden(ax, x, oy, x, oy + 15)
    circle(ax, 35, oy + 7.5, 4)
    dim(ax, (0, oy), (0, oy + 15), -8, "15", horizontal=False)
    leader(ax, (36.4, oy + 8.9), (46, oy + 24), "Ø4 thru")
    label(ax, c, oy - 10, "FRONT")
    ax.text(-30, 96, "Shaft collar, all dims mm", fontsize=13, color=ds.INK)
    ds.finish(fig, ax, "08_shaft_collar.png", (-40, 75), (-40, 102))


PARTS = {"04_bushing": part4, "05_enclosure": part5, "06_angled_bracket": part6, "07_pocket_plate": part7,
         "08_shaft_collar": part8}


def draw(style, out_dir):
    ink, lw = ("#3a3d44", 1.4) if style == "rough" else ("#1d2a4a", 1.6)
    params = dict(scale=1.1, length=140, randomness=4) if style == "rough" else dict(scale=0.6, length=120, randomness=2)
    ds.INK, ds.LW, ds.OUT = ink, lw, out_dir
    with plt.xkcd(**params):
        plt.rcParams["font.family"] = "DejaVu Sans"
        for fn in PARTS.values():
            fn()
    ds.INK, ds.LW = "#1d2a4a", 1.6


def main():
    os.makedirs(OUT, exist_ok=True)
    draw("clean", OUT)
    tmp = tempfile.mkdtemp()
    draw("rough", tmp)
    for i, name in enumerate(PARTS):
        clean = cv2.cvtColor(cv2.imread(os.path.join(OUT, f"{name}.png")), cv2.COLOR_BGR2RGB)
        rough = cv2.cvtColor(cv2.imread(os.path.join(tmp, f"{name}.png")), cv2.COLOR_BGR2RGB)
        for variant, src in (("photo", clean), ("rough", rough)):
            rng = np.random.default_rng(2000 + 10 * i + (variant == "rough"))
            img, q = mp.photograph(mp.paper(src, rng), variant, rng)
            cv2.imwrite(os.path.join(OUT, f"{name}__{variant}.jpg"), cv2.cvtColor(img, cv2.COLOR_RGB2BGR),
                        [cv2.IMWRITE_JPEG_QUALITY, q])
    print("wrote", sorted(os.listdir(OUT)))


if __name__ == "__main__":
    main()
