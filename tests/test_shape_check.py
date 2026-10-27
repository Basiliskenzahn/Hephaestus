"""Shape check: hole detection, orientation feedback, and the loop with cached SAM responses (no API calls)."""
import json
import os

import cv2
import numpy as np

from conftest import EXAMPLES, example
from hephaestus import sam, shape_check
from hephaestus.talos import run
from test_talos import OK, ScriptedClient, callouts

ROOT = os.path.dirname(EXAMPLES)
L_DRAWING = os.path.join(ROOT, "experiments", "reading-spike", "sketches", "02_l_bracket.png")


def l_views():
    with open(os.path.join(ROOT, "experiments", "shape-check", "grounding", "02_l_bracket.json")) as f:
        return json.load(f)["views"]


def upright_at_front():
    spec = example("l_bracket")
    spec["features"][1]["profile"]["v"] = 0
    return spec


def test_closed_regions_ignore_text_dashes_and_centre_lines():
    img = np.full((300, 400), 255, np.uint8)
    cv2.rectangle(img, (10, 10), (390, 290), 0, 2)                      # the outline
    cv2.circle(img, (100, 150), 30, 0, 2)                               # a hole ...
    cv2.line(img, (60, 150), (140, 150), 0, 1)                          # ... with a centre-line cross
    cv2.line(img, (100, 110), (100, 190), 0, 1)
    cv2.putText(img, "08", (250, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.5, 0, 1)   # text loops
    for y in range(20, 280, 8):                                         # a strip between two hidden lines
        cv2.line(img, (300, y), (300, y + 4), 0, 2)
        cv2.line(img, (330, y), (330, y + 4), 0, 2)
    outline = np.zeros_like(img, bool)
    outline[10:291, 10:391] = True
    holes = shape_check.closed_regions(shape_check.ink_mask(img, close=5), outline, raw=shape_check.ink_mask(img))
    assert len(holes) == 1
    assert abs(holes[0]["u"] - 100 / 400) < 0.02 and abs(holes[0]["v"] - 0.5) < 0.02


def test_orientation_message_names_the_side():
    drawn = np.zeros((40, 40), bool)
    drawn[35:, :] = True              # base
    drawn[:, 35:] = True              # leg at the right = back in the side view
    part = drawn[:, ::-1]
    msg = shape_check.orientation_message("side", "mirror_lr", drawn, part)
    assert msg.startswith("side view: the drawing has its tallest section at the back (right), the part has it at "
                          "the front (left)")


def test_where_words():
    assert shape_check.where("top", 0.1, 0.9) == "back-left"
    assert shape_check.where("front", 0.5, 0.5) == "centre"
    assert shape_check.where("side", 0.9, 0.1) == "bottom-back"


def test_loop_blocks_on_a_mirrored_view_and_skips_the_critic(tmp_path):
    client = ScriptedClient(CriticOutput=[OK] * 2,
                            CorrectionOutput=[{"changelog": "upright to the back", "spec": example("l_bracket"),
                                               "disputed_callouts": []}])
    start = {"spec": upright_at_front(), "callouts": callouts("l_bracket"), "views": l_views()}
    ev = list(run(L_DRAWING, client=client, start=start, out_dir=str(tmp_path),
                  segmenter=sam.Segmenter(offline=True)))
    names = [e["event"] for e in ev]
    assert names == ["spec", "compiled", "shape_check", "checks", "correction_started", "spec", "compiled",
                     "shape_check", "checks", "done"]
    first = ev[2]
    assert first["available"] and [v["view"] for v in first["views"]] == ["top", "front", "side"]
    side = next(v for v in first["views"] if v["view"] == "side")
    assert side["status"] == "issue" and side["orientation_gap"] > shape_check.ORIENT_MARGIN
    assert os.path.exists(side["files"]["compare"]) and os.path.exists(side["files"]["input"])
    checks = ev[3]
    assert [i["source"] for i in checks["issues"]] == ["shape"]
    assert "tallest section at the back" in checks["issues"][0]["message"]
    assert checks["critic"] is None and "shape check" in checks["critic_skipped"]
    assert ev[7]["available"] and not ev[7]["issues"]
    assert ev[-1]["status"] == "pass" and client.calls == ["CorrectionOutput", "CriticOutput", "CriticOutput"]
    json.dumps(ev)


def test_unavailable_shape_check_never_fails_the_run(tmp_path):
    client = ScriptedClient(CriticOutput=[OK] * 2)
    start = {"spec": example("l_bracket"), "callouts": callouts("l_bracket"), "views": l_views()}
    empty = sam.Segmenter(cache_dir=str(tmp_path / "cache"), offline=True)       # every SAM lookup misses
    ev = list(run(L_DRAWING, client=client, start=start, segmenter=empty))
    sc = next(e for e in ev if e["event"] == "shape_check")
    assert not sc["available"] and "SAM failed" in sc["note"]
    assert ev[-1]["status"] == "pass"


def test_no_views_means_no_check():
    client = ScriptedClient(CriticOutput=[OK] * 2)
    start = {"spec": example("l_bracket"), "callouts": callouts("l_bracket")}
    ev = list(run(L_DRAWING, client=client, start=start, segmenter=sam.Segmenter(offline=True)))
    sc = next(e for e in ev if e["event"] == "shape_check")
    assert not sc["available"] and ev[-1]["status"] == "pass"
