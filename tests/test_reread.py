"""Independent re-read of the written numbers (hephaestus/reread.py): no API calls."""
import io
import json
import os

from PIL import Image

from conftest import EXAMPLES, example
from hephaestus import reread
from hephaestus.talos import run
from test_talos import OK, ScriptedClient, callouts

DRAWING = os.path.join(os.path.dirname(EXAMPLES), "experiments", "hand-sketches", "sketches", "03_block.png")


def test_compare():
    c = {"value": 40, "label": "40"}
    assert reread.compare(c, "40") == ("confirmed", 40)
    assert reread.compare(c, "60") == ("uncertain", 60)
    assert reread.compare(c, "unclear") == ("uncertain", None)                       # the read was not sure either
    assert reread.compare({**c, "confidence": "high"}, "unclear") == ("unverified", None)
    assert reread.compare({"value": 5, "label": "R5 (4x)"}, "4x") == ("confirmed", 5)  # part of the same label
    assert reread.compare({"value": 6, "label": "4x Ø6 thru"}, "4x Ø8") == ("uncertain", 8)
    assert reread.compare({"value": 6, "label": "4x Ø6 thru"}, "4x Ø6") == ("confirmed", 6)
    assert reread.compare({"value": 1.5, "label": "1,5"}, "1,5") == ("confirmed", 1.5)


def test_crop_is_upscaled():
    img = Image.new("RGB", (1000, 800), "white")
    data = reread.crop(img, [100, 100, 130, 120])
    c = Image.open(io.BytesIO(data))
    assert c.height >= reread.MIN_HEIGHT and c.format == "JPEG"
    assert reread.crop(img, [500, 500, 500, 500]) is not None          # margin keeps a degenerate box readable


def test_loop_flags_a_misread_number_without_correcting_it():
    co = callouts("block")
    for c in co:                                      # every written number located, as the read now does
        c.update(text_box=[100, 100, 160, 140] if c["measure"] != "unchecked" else None, confidence="high",
                 parameter=None)
    first = next(k for k, c in enumerate(co) if c["label"] == "40")
    co[first]["parameter"] = "width"
    located = [k for k, c in enumerate(co) if c.get("text_box")]
    readings = [{"image": i, "text": "60" if k == first else co[k]["label"]} for i, k in enumerate(located, 1)]
    client = ScriptedClient(ReadOutput=[{"spec": example("block"), "callouts": co, "views": []}],
                            RereadOutput=[{"readings": readings}], CriticOutput=[OK] * 2)
    ev = list(run(DRAWING, client=client, use_shape=False))
    nums = next(e for e in ev if e["event"] == "numbers")
    assert nums["available"] and nums["uncertain"] == 1
    flagged = [n for n in nums["numbers"] if n["status"] == "uncertain"]
    assert flagged[0]["label"] == "40" and flagged[0]["second"] == 60 and flagged[0]["parameter"] == "width"
    assert ev[-1]["status"] == "pass" and "CorrectionOutput" not in client.calls      # the user decides


def test_reread_failure_never_fails_the_run():
    co = callouts("block")
    for c in co:
        c.update(text_box=[100, 100, 160, 140], confidence="high", parameter=None)
    client = ScriptedClient(ReadOutput=[{"spec": example("block"), "callouts": co, "views": []}], CriticOutput=[OK] * 2)
    ev = list(run(DRAWING, client=client, use_shape=False))          # no RereadOutput recorded: the call fails
    nums = next(e for e in ev if e["event"] == "numbers")
    assert not nums["available"] and ev[-1]["status"] == "pass"


def test_a_suspected_contradiction_builds_the_part_and_flags_the_numbers():
    """The recorded read that stopped on 'Ø40, 35 and 15 contradict each other' (hand shaft collar photo, Oct 28):
    no rejection. The read is asked once more; the best-guess part is built and the conflicting numbers become chips
    (no correction call for them)."""
    with open(os.path.join(os.path.dirname(__file__), "data", "shaft_collar_rejected_read.json")) as f:
        stopped = json.load(f)
    assert stopped["problem"] and not stopped["callouts"]
    co = callouts("shaft_collar")
    for c in co:
        c.update(text_box=[100, 100, 160, 140] if c["measure"] != "unchecked" else None, confidence="high",
                 parameter=None, conflict=None)
    k35 = next(k for k, c in enumerate(co) if c["label"] == "Ø40")         # the sketch also says Ø35 somewhere
    co[k35].update(label="Ø35", value=35, conflict="Ø35 contradicts the 40 mm width in the top view")
    located = [k for k, c in enumerate(co) if c.get("text_box")]
    readings = [{"image": i, "text": co[k]["label"]} for i, k in enumerate(located, 1)]
    client = ScriptedClient(ReadOutput=[stopped, {"input_kind": "drawing", "problem": None,
                                                  "spec": example("shaft_collar"), "callouts": co, "views": []}],
                            RereadOutput=[{"readings": readings}], CriticOutput=[OK] * 2)
    ev = list(run(DRAWING, client=client, use_shape=False))
    assert "rejected" not in [e["event"] for e in ev] and ev[-1]["status"] == "pass"
    assert client.calls.count("ReadOutput") == 2 and "CorrectionOutput" not in client.calls
    rows = next(e for e in ev if e["event"] == "checks")["callouts"]
    assert rows[k35]["status"] == "conflict" and rows[k35]["measured"] == 40       # not an issue
    nums = next(e for e in ev if e["event"] == "numbers")
    flagged = [n for n in nums["numbers"] if n["status"] == "uncertain"]
    assert len(flagged) == 1 and flagged[0]["conflict"] and flagged[0]["second"] is None   # chip: "conflict"


def test_a_conflict_the_reread_confirms_is_still_shown():
    co = [{"label": "35", "value": 35, "measure": "unchecked", "text_box": [100, 100, 160, 140], "confidence": "high",
           "conflict": "contradicts Ø40"}]
    client = ScriptedClient(RereadOutput=[{"readings": [{"image": 1, "text": "35"}]}])
    n = reread.reread(client, DRAWING, co)["numbers"][0]
    assert n["status"] == "uncertain" and n["second"] is None
