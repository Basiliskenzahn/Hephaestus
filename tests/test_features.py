"""One small part per feature variant, checked against analytic volumes."""
import math

import pytest

from hephaestus.compile import compile
from hephaestus.render import count_holes

pi = math.pi
SPACER = (10 - 3 * pi) / (12 - 3 * pi)      # centroid offset of a fillet's cross-section, as a fraction of r


def part(*features, **params):
    return {"name": "t", "parameters": {k: {"value": v} for k, v in params.items()}, "features": list(features)}


def box(x, y, z, fid="box"):
    return {"id": fid, "type": "extrude", "plane": "XY",
            "profile": {"shape": "rect", "width": x, "height": y}, "distance": z}


def volume(spec):
    return compile(spec).metrics["volume_mm3"]


def test_rounded_rect():
    spec = part({"id": "p", "type": "extrude", "plane": "XY", "distance": 10,
                 "profile": {"shape": "rounded_rect", "width": 40, "height": 20, "radius": 5}})
    assert volume(spec) == pytest.approx((800 - (4 - pi) * 25) * 10, rel=1e-6)


@pytest.mark.parametrize("seg, area", [
    ({"kind": "arc_center", "center": [30, 10], "to": [30, 20]}, 600 + 50 * pi),
    ({"kind": "arc_center", "center": [30, 10], "to": [30, 20], "clockwise": True}, 600 - 50 * pi),
    ({"kind": "arc3", "via": [40, 10], "to": [30, 20]}, 600 + 50 * pi),
])
def test_path_with_arcs_on_xz(seg, area):
    spec = part({"id": "p", "type": "extrude", "plane": "XZ", "distance": 5,
                 "profile": {"shape": "path", "start": [0, 0],
                             "segments": [{"kind": "line", "to": [30, 0]}, seg, {"kind": "line", "to": [0, 20]}]}})
    r = compile(spec)
    assert r.metrics["volume_mm3"] == pytest.approx(area * 5, rel=1e-6)
    assert r.metrics["bbox"]["size"][1] == pytest.approx(5)          # XZ sketch extrudes along +Y


def test_arc_center_radius_mismatch_is_reported():
    spec = part({"id": "p", "type": "extrude", "plane": "XY", "distance": 5,
                 "profile": {"shape": "path", "start": [0, 0], "segments": [
                     {"kind": "line", "to": [30, 0]}, {"kind": "arc_center", "center": [30, 10], "to": [30, 25]}]}})
    with pytest.raises(Exception, match=r"feature p: path segment 2 \(arc_center\) start \(30, 0\) and end \(30, 25\) "
                                        r"are not the same distance from centre \(30, 10\) \(10 vs 15\)"):
        compile(spec)


def test_rotated_slot():
    spec = part(box(40, 40, 5), {"id": "s", "type": "slot", "face": "top", "center": [20, 20],
                                 "length": 20, "width": 6, "angle": 90, "through": True})
    r = compile(spec)
    assert r.metrics["volume_mm3"] == pytest.approx(40 * 40 * 5 - ((20 - 6) * 6 + 9 * pi) * 5, rel=1e-6)
    top = r.silhouettes["top"]
    assert not top.at(20, 28.5) and top.at(28.5, 20)                  # runs along Y, not X


def test_counterbore_and_countersink():
    cb = part(box(30, 30, 6), {"id": "h", "type": "hole", "face": "top", "center": [15, 15], "diameter": 6,
                               "through": True, "counterbore": {"diameter": 11, "depth": 3}})
    assert volume(cb) == pytest.approx(30 * 30 * 6 - pi * 9 * 6 - pi * (5.5 ** 2 - 9) * 3, rel=1e-6)
    cs = part(box(30, 30, 6), {"id": "h", "type": "hole", "face": "top", "center": [15, 15], "diameter": 6,
                               "through": True, "countersink": {"diameter": 12, "angle": 90}})
    # cone frustum R6 -> r3 over 3 mm adds 63 pi - 27 pi on top of the plain hole
    assert volume(cs) == pytest.approx(30 * 30 * 6 - pi * 9 * 6 - 36 * pi, rel=1e-6)


def test_blind_hole_from_the_bottom_via_plane():
    spec = part(box(30, 30, 10), {"id": "h", "type": "hole", "plane": "XY", "offset": 0, "direction": "positive",
                                  "center": [15, 15], "diameter": 8, "depth": 4})
    r = compile(spec)
    assert r.metrics["volume_mm3"] == pytest.approx(9000 - pi * 16 * 4, rel=1e-6)
    assert count_holes(r.silhouettes["top"].mask) == 0                # blind: not visible from the top


def test_symmetric_cut_on_yz():
    spec = part(box(20, 20, 20), {"id": "c", "type": "extrude", "plane": "YZ", "offset": 10, "direction": "symmetric",
                                  "distance": 4, "operation": "cut",
                                  "profile": {"shape": "rect", "u": 5, "v": 5, "width": 10, "height": 10}})
    assert volume(spec) == pytest.approx(8000 - 400, rel=1e-9)


def test_negative_extrude_warns_about_origin():
    spec = part({"id": "p", "type": "extrude", "plane": "XY", "direction": "negative", "distance": 5,
                 "profile": {"shape": "rect", "width": 10, "height": 10}})
    r = compile(spec)
    assert r.metrics["bbox"]["min"][2] == pytest.approx(-5)
    assert "not at the origin" in r.warnings[0]


def test_top_of_feature_fillets_only_the_boss_rim():
    boss = {"id": "boss", "type": "extrude", "plane": "XY", "offset": 5, "distance": 10,
            "profile": {"shape": "circle", "u": 20, "v": 20, "diameter": 10}}
    spec = part(box(40, 40, 5), boss, {"id": "rim", "type": "fillet", "radius": 1, "edges": "top_of_feature:boss"})
    removed = (1 - pi / 4) * 2 * pi * (5 - SPACER)
    assert volume(spec) == pytest.approx(40 * 40 * 5 + pi * 25 * 10 - removed, rel=1e-6)


def test_vertical_outer_skips_hole_and_concave_edges():
    # L-bracket: the inner corner along X is concave and must not be chamfered by all_outer
    spec = part(box(60, 40, 5, "base"),
                {"id": "up", "type": "extrude", "plane": "XY", "distance": 40,
                 "profile": {"shape": "rect", "v": 35, "width": 60, "height": 5}},
                {"id": "c", "type": "chamfer", "size": 1, "edges": "all_outer"})
    r = compile(spec)
    side = r.silhouettes["side"]
    assert side.at(35.2, 5.2)                                         # inner corner stays sharp (filled)
    assert not side.at(0.2, 4.8)                                      # outer front-top corner is chamfered
