"""Invalid specs fail with messages that say which feature is wrong and what to change."""
import pytest

from conftest import example
from hephaestus.compile import SpecError, compile


def errors_of(spec, **kw):
    with pytest.raises(SpecError) as info:
        compile(spec, **kw)
    return "\n".join(info.value.errors), info.value


def test_unknown_parameter():
    spec = example("plate")
    spec["features"][1]["diameter"] = "hole_dia"
    msg, _ = errors_of(spec)
    assert "feature corner_hole.diameter: unknown parameter 'hole_dia'" in msg
    assert "known parameters: bore_d, depth, hole_d" in msg


def test_all_expression_errors_are_reported_together():
    spec = example("plate")
    spec["features"][1]["diameter"] = "hole_dia"
    spec["features"][3]["center"] = ["widht / 2", "depth / 2"]
    msg, _ = errors_of(spec)
    assert "hole_dia" in msg and "widht" in msg


def test_hole_outside_the_part():
    spec = example("plate")
    spec["features"][3]["center"] = [85, 10]
    msg, err = errors_of(spec)
    assert msg == ("feature bore: centre (85, 10) on the top face is outside the part bbox in XY "
                   "(80 x 50: x 0..80, y 0..50)")
    assert [s["status"] for s in err.feature_status] == ["ok", "ok", "ok", "error"]


def test_pattern_copy_outside_the_part():
    spec = example("plate")
    spec["features"][2]["spacing_u"] = 80
    msg, _ = errors_of(spec)
    assert "feature corner_holes (copy 2): centre (88, 8)" in msg and "outside the part bbox" in msg


def test_fillet_larger_than_the_wall():
    spec = example("block")
    spec["features"][4] = {"id": "top_round", "type": "fillet", "radius": 25, "edges": "top_outer"}
    msg, _ = errors_of(spec)
    assert msg.startswith("feature top_round: fillet radius 25 is larger than the wall it sits on")
    assert "is only 20 mm across); use less than 20" in msg


def test_fillet_the_kernel_rejects():
    spec = example("block")
    spec["parameters"]["corner_r"]["value"] = 22        # two R22 corners cannot fit on the 40 mm side
    msg, _ = errors_of(spec)
    assert "feature corners: fillet radius 22" in msg and "Try a smaller radius" in msg


def test_cyclic_parameters():
    spec = example("plate")
    spec["parameters"]["width"]["value"] = "depth + hole_inset"
    spec["parameters"]["depth"]["value"] = "width - 30"
    msg, _ = errors_of(spec)
    assert "parameter cycle: depth -> width -> depth" in msg


def test_cut_that_misses():
    spec = example("bushing")
    spec["features"][1]["center"] = ["c", "c"]         # inside the bore: nothing to cut
    spec["features"][1]["diameter"] = 10
    msg, _ = errors_of(spec)
    assert "feature bolt_hole: hole at (20, 20) removes no material; it misses the part" in msg


def test_disconnected_add():
    spec = example("plate")
    spec["features"].insert(1, {"id": "floating", "type": "extrude", "plane": "XY", "offset": 20,
                                "profile": {"shape": "rect", "width": 10, "height": 10}, "distance": 5})
    msg, _ = errors_of(spec)
    assert "feature floating: extrude (add) leaves 2 separate bodies" in msg


def test_revolve_profile_crossing_the_axis():
    spec = example("bushing")
    spec["features"][0]["axis_origin"] = ["c + 10", 0]
    msg, _ = errors_of(spec)
    assert "feature body: the profile crosses the revolve axis" in msg


def test_pattern_of_unknown_or_wrong_feature():
    spec = example("plate")
    spec["features"][2]["feature"] = "corner_hoel"
    msg, _ = errors_of(spec)
    assert "feature corner_holes: pattern source 'corner_hoel' is not an earlier feature (earlier: base, corner_hole)" in msg
    spec["features"][2]["feature"] = "base"
    msg, _ = errors_of(spec)
    assert "can only pattern a hole or slot, 'base' is a extrude" in msg


def test_schema_level_errors_are_readable():
    spec = example("plate")
    spec["features"][1]["plane"] = "XY"                 # face and plane both given
    del spec["features"][3]["diameter"]
    msg, _ = errors_of(spec)
    assert "give exactly one of 'face' or 'plane'" in msg
    assert "spec features.3.hole.diameter: Field required" in msg


def test_selector_that_matches_nothing():
    spec = {"name": "disc", "features": [
        {"id": "disc", "type": "extrude", "plane": "XY",
         "profile": {"shape": "circle", "u": 25, "v": 25, "diameter": 50}, "distance": 5},
        {"id": "edge", "type": "chamfer", "size": 0.5, "edges": "vertical_outer"}]}
    msg, _ = errors_of(spec)
    assert msg == "feature edge: edge selector 'vertical_outer' matched no edges on the part"


def test_unknown_override():
    msg, _ = errors_of(example("plate"), overrides={"widht": 90})
    assert "override: unknown parameter 'widht'" in msg
