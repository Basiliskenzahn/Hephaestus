"""Callouts are measured on the compiled part; mismatches become precise messages."""
import json
import os

import pytest

from conftest import EXAMPLES, example
from hephaestus.compile import compile
from hephaestus.measure import check, issues


def callouts(name):
    with open(os.path.join(EXAMPLES, "callouts", f"{name}.json")) as f:
        return json.load(f)


@pytest.mark.parametrize("name", ["plate", "l_bracket", "block", "bushing", "enclosure", "angled_bracket",
                                  "pocket_plate", "shaft_collar"])
def test_true_callouts_pass_on_true_specs(compiled, name):
    rows = check(callouts(name), compiled(name))
    assert issues(rows) == []
    assert all(r["status"] in ("ok", "unchecked") for r in rows)


def test_widened_plate_reports_overall_and_pitch():
    msgs = issues(check(callouts("plate"), compile(example("plate"), overrides={"width": 100})))
    assert "callout '80' (overall size along x) measures 100 on the part, expected 80" in msgs
    assert "callout '64' (distance corner_holes[0] → corner_holes[1] along x) measures 84 on the part, expected 64" in msgs
    assert "callout 'bore centred' (distance from bore to x_min) measures 50 on the part, expected 40" in msgs
    assert len(msgs) == 3


def test_moved_hole_and_wrong_diameter():
    spec = example("block")
    spec["features"][1]["center"] = ["length / 2", "width / 2 + 5"]
    spec["features"][1]["diameter"] = 10
    msgs = issues(check(callouts("block"), compile(spec)))
    assert "callout 'Ø12 thru' (diameter of bore) measures 10 on the part, expected 12" in msgs
    assert "callout '20' (distance from bore to y_min) measures 25 on the part, expected 20" in msgs


def test_dropped_feature_and_bad_reference():
    spec = example("plate")
    del spec["features"][3]                                  # no bore
    rows = check(callouts("plate") + [{"label": "x", "value": 1, "measure": "distance",
                                        "feature": "corner_holes[7]", "feature_b": "bore", "axis": "x"}], compile(spec))
    msgs = issues(rows)
    assert any(m.startswith("callout 'Ø20 thru' (diameter of bore): feature 'bore' does not exist") for m in msgs)
    assert any("pattern corner_holes has 4 copies, numbered 0 to 3" in m for m in msgs)


def test_chamfer_instead_of_fillet():
    spec = example("block")
    spec["features"][4] = {"id": "corners", "type": "chamfer", "size": "corner_r", "edges": "vertical_outer"}
    msgs = issues(check(callouts("block"), compile(spec)))
    assert any(m.startswith("callout 'R5 (4x)' (radius of corners)") for m in msgs)


def test_slot_and_thickness_measure_geometry():
    spec = example("l_bracket")
    spec["parameters"]["slot_len"]["value"] = 24
    spec["parameters"]["t"]["value"] = 6
    msgs = issues(check(callouts("l_bracket"), compile(spec)))
    assert "callout '20' (slot length of base_slot) measures 24 on the part, expected 20" in msgs
    assert "callout '5' (thickness of base) measures 6 on the part, expected 5" in msgs


def test_unchecked_and_incomplete_callouts():
    rows = check([{"label": "30°", "value": 30, "measure": "unchecked"},
                  {"label": "64", "value": 64, "measure": "distance", "feature": "corner_hole"}],
                 compile(example("plate")))
    assert rows[0]["status"] == "unchecked"
    assert rows[1]["message"] == "callout '64' (distance corner_hole → None along None): measure 'distance' needs axis"


def test_depth_of_pocket_and_blind_hole():
    rows = check([{"label": "4 deep", "value": 4, "measure": "depth", "feature": "pocket"}],
                 compile("examples/pocket_plate.json", overrides={"pocket_depth": 5}))
    assert rows[0]["message"] == "callout '4 deep' (depth of pocket) measures 5 on the part, expected 4"
    spec = example("plate")
    spec["features"][3].update(through=False, depth=4)
    rows = check([{"label": "Ø20 x 4 deep", "value": 4, "measure": "depth", "feature": "bore"},
                  {"label": "Ø6", "value": 6, "measure": "depth", "feature": "corner_holes[1]"}], compile(spec))
    assert rows[0]["status"] == "ok" and rows[0]["measured"] == 4
    assert "goes all the way through" in rows[1]["message"]
