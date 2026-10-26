"""The scorer accepts equivalent specs and catches the errors we care about.  Run: python -m pytest test_score.py"""
import copy
import json
import os

import pytest

from score import ROOT, score


def truth(name):
    with open(os.path.join(ROOT, "examples", f"{name}.json")) as f:
        return copy.deepcopy(json.load(f))


def test_equivalent_spec_without_parameters_is_perfect():
    # all literals, holes listed one by one instead of a pattern: same geometry, so still perfect
    spec = {"name": "plate", "features": [
        {"id": "b", "type": "extrude", "plane": "XY", "profile": {"shape": "rect", "width": 80, "height": 50}, "distance": 6}]
        + [{"id": f"h{i}", "type": "hole", "face": "top", "center": c, "diameter": 6, "through": True}
           for i, c in enumerate([[8, 8], [72, 8], [8, 42], [72, 42]])]
        + [{"id": "bore", "type": "hole", "face": "top", "center": [40, 25], "diameter": 20, "through": True}]}
    s = score("01_plate", spec)
    assert s["perfect"]
    assert s["x_param"]["param"] is None and s["params"]["expr_fields"] == 0


def test_rotated_slot_is_caught():
    spec = truth("l_bracket")
    spec["features"][4]["angle"] = 90
    s = score("02_l_bracket", spec)
    assert not s["perfect"] and s["iou"] < 0.98
    assert {d["label"] for d in s["failed_dims"]} >= {"slot 20"}


def test_hole_off_by_one_mm_is_caught():
    spec = truth("plate")
    spec["features"][3]["center"] = ["width / 2 + 1", "depth / 2"]
    s = score("01_plate", spec)
    assert not s["perfect"]
    assert [d["label"] for d in s["failed_dims"]] == ["Ø20"]


def test_missing_chamfer_and_wrong_fillet():
    spec = truth("block")
    del spec["features"][5]
    spec["parameters"]["corner_r"]["value"] = 4
    s = score("03_block", spec)
    failed = {d["label"] for d in s["failed_dims"]}
    assert "1 x 45°" in failed and "R5 (5, 5)" in failed


def test_upright_too_thick_and_compile_error():
    spec = truth("l_bracket")
    spec["parameters"]["t"]["value"] = 6
    assert not score("02_l_bracket", spec)["perfect"]
    spec["features"][2]["center"] = ["hole_x", 50]
    s = score("02_l_bracket", spec)
    assert s["compiles"] is False and "outside the part bbox" in s["errors"][0]


def test_hardcoded_positions_fail_the_x_param_check():
    spec = truth("plate")
    spec["features"][2]["spacing_u"] = 64
    s = score("01_plate", spec)
    assert s["perfect"] and s["x_param"]["param"] == "width" and not s["x_param"]["ok"]
