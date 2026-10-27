"""Pixel boxes of each view in the spike drawings, from the drawing scripts (spike only; the product gets view boxes
from Muse Spark grounding). Re-runs a draw function, intercepts its savefig and maps data coordinates to pixels."""
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
EXP = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(EXP, "reading-spike"))
sys.path.insert(0, os.path.join(EXP, "benchmark"))
import draw_sketches as ds  # noqa: E402
import draw_parts as dp  # noqa: E402

# view rectangles in drawing (data) coordinates, and a point inside the part's material for each view
VIEWS = {
    "01_plate": (ds.part1, {"top": ((0, 0, 80, 50), (20, 20))}),
    "02_l_bracket": (ds.part2, {"top": ((0, 0, 60, 40), (10, 25)), "front": ((0, 62, 60, 102), (5, 82)),
                                "side": ((95, 62, 135, 102), (132.5, 80))}),
    "06_angled_bracket": (dp.part6, {"front": ((0, 55, 80, 95), (8, 75)), "top": ((0, 0, 80, 30), (70, 22))}),
}


def pixel_views(part, pad=1.5):
    """{view: {"box": (x1, y1, x2, y2), "point": (x, y)}} in pixels of the clean PNG (origin top-left)."""
    fn, views = VIEWS[part]
    captured = {}

    def finish(fig, ax, name, xlim, ylim):
        ax.set_xlim(*xlim)
        ax.set_ylim(*ylim)
        ax.set_aspect("equal")
        ax.axis("off")
        fig.canvas.draw()
        scale = 110 / fig.dpi
        H = round(fig.get_size_inches()[1] * 110)

        def px(x, y):
            dx, dy = ax.transData.transform((x, y))
            return dx * scale, H - dy * scale
        for v, ((x0, y0, x1, y1), (qx, qy)) in views.items():
            ax_, ay_ = px(x0 - pad, y1 + pad)
            bx_, by_ = px(x1 + pad, y0 - pad)
            captured[v] = {"box": (round(ax_), round(ay_), round(bx_), round(by_)), "point": tuple(round(c) for c in px(qx, qy))}
        plt.close(fig)

    orig = ds.finish
    ds.finish = finish
    try:
        with plt.xkcd(scale=0.6, length=120, randomness=2):
            fn()
    finally:
        ds.finish = orig
    return captured


if __name__ == "__main__":
    from PIL import Image, ImageDraw
    for part in VIEWS:
        d = os.path.join(EXP, "reading-spike" if part[:2] in ("01", "02") else "benchmark", "sketches")
        im = Image.open(os.path.join(d, part + ".png")).convert("RGB")
        dr = ImageDraw.Draw(im)
        for v, b in pixel_views(part).items():
            dr.rectangle(b["box"], outline=(220, 40, 20), width=2)
            x, y = b["point"]
            dr.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(20, 160, 40))
            print(part, v, b, im.size)
        im.save(os.path.join(HERE, "out", f"{part}_views.png"))
