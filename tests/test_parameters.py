"""Changing parameter values recompiles without touching the features."""
import math

import pytest

from conftest import example
from hephaestus.compile import compile


def test_widening_the_plate_moves_the_corner_holes():
    wide = compile(example("plate"), overrides={"width": 100})
    top = wide.silhouettes["top"]
    assert wide.metrics["bbox"]["size"] == pytest.approx((100, 50, 6))
    for x, y in [(8, 8), (92, 8), (8, 42), (92, 42), (50, 25)]:
        assert not top.at(x, y), f"expected a hole at ({x}, {y})"
    assert top.at(72, 8), "the old corner hole position should now be solid"
    assert wide.metrics["volume_mm3"] == pytest.approx(100 * 50 * 6 - 4 * math.pi * 9 * 6 - math.pi * 100 * 6)


def test_editing_the_parameter_in_the_spec_matches_the_override():
    spec = example("plate")
    spec["parameters"]["width"]["value"] = 100
    assert compile(spec).stl == compile(example("plate"), overrides={"width": 100}).stl


def test_derived_parameters_follow():
    r = compile(example("bushing"), overrides={"flange_d": 50, "bolt_circle_d": 40})
    assert r.metrics["parameters"]["c"] == 25
    assert r.metrics["bbox"]["size"] == pytest.approx((50, 50, 30))
    assert not r.silhouettes["top"].at(25 + 20, 25)        # bolt hole moved out with the circle


def test_bracket_hole_pitch():
    r = compile(example("l_bracket"), overrides={"hole_pitch": 20})
    front = r.silhouettes["front"]
    assert not front.at(35, 28) and front.at(45, 28)


def test_parameter_bounds_are_enforced():
    from hephaestus.compile import SpecError
    with pytest.raises(SpecError, match=r"parameter width = 20 is below its minimum 30"):
        compile(example("plate"), overrides={"width": 20})
