import pytest

from hephaestus.expr import ExprError, evaluate, resolve_parameters


def test_arithmetic_and_precedence():
    p = {"w": 80.0, "t": 6.0}
    assert evaluate("w - 2 * 8", p) == 64
    assert evaluate("(w - 8) / 2", p) == 36
    assert evaluate("-t / 2", p) == -3
    assert evaluate(12, p) == 12.0


def test_parameters_reference_each_other():
    assert resolve_parameters({"w": 80, "inset": "w / 10", "pitch": "w - 2 * inset"}) == \
        {"w": 80.0, "inset": 8.0, "pitch": 64.0}


@pytest.mark.parametrize("text", ["__import__('os')", "w.real", "w ** 2", "w if w else 1", "[w]", "'a'", "w // 2"])
def test_rejects_anything_outside_the_grammar(text):
    with pytest.raises(ExprError, match="unsupported syntax|cannot parse"):
        evaluate(text, {"w": 1.0})


def test_unknown_name_lists_known_parameters():
    with pytest.raises(ExprError, match=r"unknown parameter 'widht' in 'widht - 8' \(known parameters: depth, width\)"):
        evaluate("widht - 8", {"width": 1.0, "depth": 2.0})


def test_cycle_names_the_chain():
    with pytest.raises(ExprError, match=r"parameter cycle: a -> b -> c -> a"):
        resolve_parameters({"a": "b + 1", "b": "c * 2", "c": "a"})


def test_division_by_zero():
    with pytest.raises(ExprError, match="division by zero"):
        evaluate("w / (w - w)", {"w": 3.0})
