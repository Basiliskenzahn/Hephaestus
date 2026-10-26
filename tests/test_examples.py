"""The example parts compile to the analytically expected geometry and silhouettes."""
import math

import numpy as np
import pytest

from conftest import NAMES, example
from hephaestus.compile import compile
from hephaestus.render import count_holes

pi = math.pi


def stadium_area(length, width):
    return (length - width) * width + pi * (width / 2) ** 2


EXPECTED = {
    # name: (volume mm3, bbox size, relative tolerance)
    "plate": (80 * 50 * 6 - 4 * pi * 3 ** 2 * 6 - pi * 10 ** 2 * 6, (80, 50, 6), 1e-6),
    "l_bracket": (60 * 40 * 5 + 60 * 5 * 35 - 2 * pi * 2.5 ** 2 * 5 - stadium_area(20, 6) * 5, (60, 40, 40), 1e-6),
    # fillets: (4 - pi) r^2 per unit height; chamfer ring via Pappus (triangle centroid at r - c/3 on the arcs)
    "block": ((70 * 40 - (4 - pi) * 5 ** 2) * 20
              - 0.5 * 1 ** 2 * (2 * (70 + 40) - 8 * 5 + 2 * pi * (5 - 1 / 3))
              - pi * 6 ** 2 * 20 - 2 * pi * 3 ** 2 * 20, (70, 40, 20), 1e-5),
    "bushing": (pi * ((20 ** 2 - 8 ** 2) * 5 + (12 ** 2 - 8 ** 2) * 25) - 4 * pi * 2 ** 2 * 5, (40, 40, 30), 1e-6),
    "enclosure": ((100 * 60 - (4 - pi) * 6 ** 2) * 40 - (96 * 56 - (4 - pi) * 4 ** 2) * 38
                  - pi * 4 ** 2 * 2 - pi * 0.5 ** 2 * (4 + 0.5 / 3) - 4 * pi * 1.6 ** 2 * 2, (100, 60, 40), 1e-5),
}


@pytest.mark.parametrize("name", NAMES)
def test_volume_and_bbox(compiled, name):
    m = compiled(name).metrics
    volume, size, rel = EXPECTED[name]
    assert m["volume_mm3"] == pytest.approx(volume, rel=rel)
    assert m["bbox"]["size"] == pytest.approx(size, abs=1e-6)
    assert m["bbox"]["min"] == pytest.approx((0, 0, 0), abs=1e-6)
    assert all(f["status"] == "ok" for f in m["features"])
    assert compiled(name).warnings == []


@pytest.mark.parametrize("name", NAMES)
def test_compiles_fast(compiled, name):
    assert compiled(name).timings["total"] < 2.0


@pytest.mark.parametrize("name", ["plate", "enclosure"])
def test_deterministic(name):
    a, b = compile(example(name)), compile(example(name))
    assert a.step == b.step
    assert a.stl == b.stl
    assert a.metrics == b.metrics
    for view in a.silhouettes:
        assert a.silhouettes[view].png() == b.silhouettes[view].png()


def test_outputs_are_real_files(compiled):
    r = compiled("plate")
    assert r.step.startswith(b"ISO-10303-21;")
    n_tri = int.from_bytes(r.stl[80:84], "little")
    assert len(r.stl) == 84 + 50 * n_tri and n_tri == r.metrics["triangles"]


def test_plate_top_view_shows_five_holes(compiled):
    top = compiled("plate").silhouettes["top"]
    assert count_holes(top.mask) == 5
    for x, y in [(8, 8), (72, 8), (8, 42), (72, 42), (40, 25)]:
        assert not top.at(x, y), f"expected a hole at ({x}, {y})"
    assert top.at(20, 25) and top.at(2, 2)


def test_plate_silhouette_area_matches_geometry(compiled):
    top = compiled("plate").silhouettes["top"]
    area = top.mask.sum() * top.mm_per_px ** 2
    assert area == pytest.approx(80 * 50 - 4 * pi * 9 - pi * 100, rel=0.01)


def test_l_bracket_side_view_is_an_l(compiled):
    side = compiled("l_bracket").silhouettes["side"]
    assert side.axes == ("y", "z")
    h, w = side.mask.shape
    s = side.mm_per_px
    cols = side.origin_mm[0] + (np.arange(w) + 0.5) * s
    rows = side.origin_mm[1] + (h - np.arange(h) - 0.5) * s
    Y, Z = np.meshgrid(cols, rows)
    inside = (Y > 0) & (Y < 40) & (Z > 0) & (Z < 40)
    expected = inside & ((Z < 5) | (Y > 35))
    iou = (side.mask & expected).sum() / (side.mask | expected).sum()
    assert iou > 0.98
    assert count_holes(side.mask) == 0


def test_l_bracket_holes_and_slot_show_in_their_views(compiled):
    r = compiled("l_bracket")
    assert count_holes(r.silhouettes["front"].mask) == 2     # upright holes, seen along Y
    assert count_holes(r.silhouettes["top"].mask) == 1       # base slot
    assert not r.silhouettes["top"].at(30, 17.5) and not r.silhouettes["top"].at(38.5, 17.5)
    assert r.silhouettes["top"].at(41.5, 17.5)               # just past the slot end (30 + 10)


def test_bushing_and_enclosure_holes(compiled):
    assert count_holes(compiled("bushing").silhouettes["top"].mask) == 5       # bore + 4 bolt holes
    assert count_holes(compiled("enclosure").silhouettes["top"].mask) == 4     # floor screw holes
    # the cable hole is blind into the front wall, so the back wall hides it in the front view
    assert count_holes(compiled("enclosure").silhouettes["front"].mask) == 0
