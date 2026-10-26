"""Draws three dimensioned test sketches in a hand-drawn style.

Ground truth for each part lives in ground_truth.json (same format the model must return).
Coordinates: mm, origin at the lower-left-front corner of the part's bounding box,
X to the right, Y to the back, Z up.
"""
import math
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Arc, Circle, FancyBboxPatch

OUT = os.path.join(os.path.dirname(__file__), "sketches")
os.makedirs(OUT, exist_ok=True)
INK = "#1d2a4a"
LW = 1.6


def line(ax, x1, y1, x2, y2, lw=LW, ls="-"):
    ax.plot([x1, x2], [y1, y2], color=INK, lw=lw, ls=ls, solid_capstyle="round")


def rect(ax, x, y, w, h):
    line(ax, x, y, x + w, y)
    line(ax, x + w, y, x + w, y + h)
    line(ax, x + w, y + h, x, y + h)
    line(ax, x, y + h, x, y)


def circle(ax, cx, cy, d, centre=True):
    ax.add_patch(Circle((cx, cy), d / 2, fill=False, ec=INK, lw=LW))
    if centre:
        e = d / 2 + 2
        line(ax, cx - e, cy, cx + e, cy, lw=0.7, ls="-.")
        line(ax, cx, cy - e, cx, cy + e, lw=0.7, ls="-.")


def dim(ax, p1, p2, offset, text, horizontal=True, fs=12):
    """Linear dimension between two points with extension lines."""
    (x1, y1), (x2, y2) = p1, p2
    if horizontal:
        y = offset
        for x, y0 in ((x1, y1), (x2, y2)):
            line(ax, x, y0, x, y + (1.5 if y > y0 else -1.5), lw=0.7)
        ax.annotate("", (x1, y), (x2, y), arrowprops=dict(arrowstyle="<->", color=INK, lw=0.9))
        ax.text((x1 + x2) / 2, y + (1.2 if y > y1 else -1.2), text, ha="center",
                va="bottom" if y > y1 else "top", fontsize=fs, color=INK)
    else:
        x = offset
        for x0, y in ((x1, y1), (x2, y2)):
            line(ax, x0, y, x + (1.5 if x > x0 else -1.5), y, lw=0.7)
        ax.annotate("", (x, y1), (x, y2), arrowprops=dict(arrowstyle="<->", color=INK, lw=0.9))
        ax.text(x + (1.2 if x > x1 else -1.2), (y1 + y2) / 2, text, ha="left" if x > x1 else "right",
                va="center", fontsize=fs, color=INK, rotation=90)


def leader(ax, target, at, text, fs=12):
    ax.annotate(text, target, at, fontsize=fs, color=INK,
                arrowprops=dict(arrowstyle="->", color=INK, lw=0.9))


def label(ax, x, y, text, fs=13):
    ax.text(x, y, text, fontsize=fs, color=INK, ha="center", fontweight="bold")


def finish(fig, ax, name, xlim, ylim):
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.patch.set_facecolor("#fbfaf6")
    fig.savefig(os.path.join(OUT, name), dpi=110, facecolor=fig.get_facecolor())
    plt.close(fig)


def slot(ax, cx, cy, length, width):
    r = width / 2
    a = length / 2 - r
    line(ax, cx - a, cy - r, cx + a, cy - r)
    line(ax, cx - a, cy + r, cx + a, cy + r)
    ax.add_patch(Arc((cx + a, cy), width, width, theta1=-90, theta2=90, ec=INK, lw=LW))
    ax.add_patch(Arc((cx - a, cy), width, width, theta1=90, theta2=270, ec=INK, lw=LW))
    line(ax, cx - a - r - 2, cy, cx + a + r + 2, cy, lw=0.7, ls="-.")


def part1():
    """Mounting plate 80 x 50 x 6, 4x Ø6 corner holes, central Ø20."""
    fig, ax = plt.subplots(figsize=(9, 6.5))
    rect(ax, 0, 0, 80, 50)
    for x, y in ((8, 8), (72, 8), (8, 42), (72, 42)):
        circle(ax, x, y, 6)
    circle(ax, 40, 25, 20)
    dim(ax, (0, 50), (80, 50), 58, "80")
    dim(ax, (80, 0), (80, 50), 90, "50", horizontal=False)
    dim(ax, (0, 0), (8, 8), -7, "8")
    dim(ax, (0, 0), (8, 8), -8, "8", horizontal=False)
    dim(ax, (8, 8), (72, 8), -15, "64")
    dim(ax, (8, 8), (8, 42), -16, "34", horizontal=False)
    leader(ax, (74, 44), (84, 64), "4x Ø6 thru")
    leader(ax, (47, 32), (56, 66), "Ø20 thru")
    label(ax, 40, -27, "TOP VIEW      plate, t = 6")
    finish(fig, ax, "01_plate.png", (-28, 105), (-32, 72))


def part2():
    """L-bracket: base 60x40x5, upright 60x5x40 at the back, slot 20x6, 2x Ø5 in upright."""
    fig, ax = plt.subplots(figsize=(11, 9))
    # TOP view (X right, Y up) at origin
    rect(ax, 0, 0, 60, 40)
    line(ax, 0, 35, 60, 35)
    slot(ax, 30, 17.5, 20, 6)
    dim(ax, (0, 0), (60, 0), -8, "60")
    dim(ax, (0, 0), (0, 40), -8, "40", horizontal=False)
    dim(ax, (20, 17.5), (40, 17.5), -16, "20")
    dim(ax, (60, 0), (60, 17.5), 67, "17.5", horizontal=False)
    leader(ax, (36, 20.5), (44, 28), "slot w = 6")
    label(ax, 30, -26, "TOP")
    # FRONT view (X right, Z up), placed above the top view
    oy = 62
    rect(ax, 0, oy, 60, 40)
    line(ax, 0, oy + 5, 60, oy + 5)
    circle(ax, 15, oy + 28, 5)
    circle(ax, 45, oy + 28, 5)
    dim(ax, (0, oy + 40), (60, oy + 40), oy + 47, "60")
    dim(ax, (0, oy), (0, oy + 40), -8, "40", horizontal=False)
    dim(ax, (0, oy + 28), (15, oy + 28), oy + 36, "15", fs=11)
    dim(ax, (15, oy + 28), (45, oy + 28), oy + 36, "30", fs=11)
    dim(ax, (60, oy), (60, oy + 28), 67, "28", horizontal=False)
    leader(ax, (46.8, oy + 29.8), (70, oy + 50), "2x Ø5 thru")
    label(ax, 30, oy - 9, "FRONT")
    # SIDE view (Y right, Z up), right of the front view
    ox = 95
    pts = [(0, 0), (40, 0), (40, 40), (35, 40), (35, 5), (0, 5), (0, 0)]
    for (a, b), (c, d) in zip(pts, pts[1:]):
        line(ax, ox + a, oy + b, ox + c, oy + d)
    dim(ax, (ox, oy), (ox + 40, oy), oy - 8, "40")
    dim(ax, (ox + 35, oy + 40), (ox + 40, oy + 40), oy + 47, "5", fs=11)
    dim(ax, (ox, oy), (ox, oy + 5), ox - 6, "5", horizontal=False, fs=11)
    label(ax, ox + 20, oy - 20, "SIDE")
    ax.text(-15, 125, "L-bracket, all dims mm", fontsize=13, color=INK)
    finish(fig, ax, "02_l_bracket.png", (-22, 150), (-32, 130))


def part3():
    """Block 70x40x20, R5 vertical edges, Ø12 centre + 2x Ø6 thru, 1x45° chamfer on top edges."""
    fig, ax = plt.subplots(figsize=(9, 9))
    ax.add_patch(FancyBboxPatch((5, 5), 60, 30, boxstyle="round,pad=5", fill=False, ec=INK, lw=LW))
    circle(ax, 35, 20, 12)
    circle(ax, 12, 20, 6)
    circle(ax, 58, 20, 6)
    dim(ax, (0, 0), (70, 0), -8, "70")
    dim(ax, (0, 0), (0, 40), -8, "40", horizontal=False)
    dim(ax, (12, 20), (58, 20), -16, "46")
    dim(ax, (70, 0), (70, 20), 77, "20", horizontal=False)
    leader(ax, (66.5, 36.5), (72, 50), "R5 (4x)")
    leader(ax, (38, 25), (40, 50), "Ø12 thru")
    leader(ax, (14, 23), (2, 50), "2x Ø6 thru")
    label(ax, 35, -26, "TOP")
    oy = 70
    pts = [(0, 0), (70, 0), (70, 19), (69, 20), (1, 20), (0, 19), (0, 0)]
    for (a, b), (c, d) in zip(pts, pts[1:]):
        line(ax, a, oy + b, c, oy + d)
    line(ax, 1, oy + 19, 69, oy + 19, lw=0.8)
    dim(ax, (70, oy), (70, oy + 20), 77, "20", horizontal=False)
    leader(ax, (69.5, oy + 19.5), (74, oy + 32), "1 x 45° all top edges")
    label(ax, 35, oy - 9, "FRONT")
    finish(fig, ax, "03_block.png", (-24, 110), (-32, 108))


if __name__ == "__main__":
    with plt.xkcd(scale=0.6, length=120, randomness=2):
        plt.rcParams["font.family"] = "DejaVu Sans"
        part1()
        part2()
        part3()
    print("wrote", sorted(os.listdir(OUT)))
