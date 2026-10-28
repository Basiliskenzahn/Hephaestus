"""Edits (typed or voice) with recorded transcriptions and edit responses: no API calls."""
import io
import json
import os
import wave

import numpy as np
import pytest

from conftest import EXAMPLES, example
from hephaestus import edit, voice
from test_talos import ScriptedClient, callouts

with open(os.path.join(os.path.dirname(__file__), "data", "edits.json")) as f:
    RECORDED = {r["name"]: r for r in json.load(f)}


def replay(name):
    rec = RECORDED[name]
    client = ScriptedClient(EditOutput=rec["outputs"])
    res = edit.edit_part(example(rec["part"]), rec["asr"]["transcript"], client, callouts=callouts(rec["part"]))
    return rec, res, client


def param(res, name):
    return res["spec"]["parameters"][name]["value"]


def test_recorded_edits_apply_and_rebuild():
    _, res, _ = replay("holes_m6")
    assert res["status"] == "applied" and param(res, "hole_d") == 6.6 and "M6" in res["explanation"]
    _, res, _ = replay("thicker")
    assert param(res, "t") == 15 and res["result"].metrics["bbox"]["size"][2] == 40
    _, res, _ = replay("width_typed")
    assert param(res, "width") == 100 and res["result"].metrics["bbox"]["size"][0] == 100
    _, res, _ = replay("remove_slot")
    assert "base_slot" not in [f["id"] for f in res["spec"]["features"]] and res["changes"] == ["removed base_slot"]
    _, res, _ = replay("fillet")
    added = [f for f in res["spec"]["features"] if f["type"] == "fillet"]
    assert len(added) == 1 and res["spec"]["parameters"][added[0]["radius"]]["value"] == 2
    _, res, _ = replay("four_holes")
    pattern = next(f for f in res["spec"]["features"] if f["id"] == "upright_holes")
    assert pattern["count_u"] == 4 and res["result"].metrics["features"]


def test_edited_dimensions_leave_the_callout_check():
    _, res, _ = replay("thicker")
    status = {r["label"]: r["status"] for r in res["callouts"]}
    assert status["5"] == "edited" and status["60"] == "ok" and status["2x Ø5 thru"] == "ok"
    assert res["callout_issues"] == []          # unchanged dimensions still match the drawing


def test_misheard_and_ambiguous_ask_instead_of_guessing():
    rec, res, _ = replay("width")
    assert rec["asr"]["transcript"] == "with one hundred"           # what the ASR heard for "width 100"
    assert res["status"] == "clarification" and res["spec"] == example("l_bracket")
    _, res, _ = replay("ambiguous")
    assert res["status"] == "clarification" and res["clarification"].endswith("?")


def test_impossible_edit_is_refused():
    _, res, client = replay("impossible")
    assert res["status"] == "clarification" and "cannot be built" in res["clarification"]
    assert client.calls == ["EditOutput"]


def test_build_error_goes_back_once_then_is_shown():
    huge = {"clarification": None, "explanation": "bore 20 -> 200", "set_parameters": [{"name": "bore_d", "value": 200}],
            "add_features": [], "remove_features": []}
    client = ScriptedClient(EditOutput=[huge, huge])
    spec = example("plate")
    assert "bore_d" in spec["parameters"]
    res = edit.edit_part(spec, "make the hole bigger than the plate", client)
    assert client.calls == ["EditOutput", "EditOutput"]
    assert res["status"] == "error" and "removes the whole part" in res["errors"][0] and res["spec"] == spec
    # the retry sees the build error
    fixed = {**huge, "set_parameters": [{"name": "bore_d", "value": 30}], "explanation": "bore 20 -> 30"}
    client = ScriptedClient(EditOutput=[huge, fixed])
    res = edit.edit_part(spec, "make the hole bigger", client)
    assert res["status"] == "applied" and param(res, "bore_d") == 30


def test_apply_edit_rules():
    spec = example("l_bracket")
    with pytest.raises(edit.EditError, match="over itself"):
        edit.apply_edit(spec, {"set_parameters": [{"name": "t", "value": "t + 10"}]})
    with pytest.raises(edit.EditError, match="no feature 'boss'"):
        edit.apply_edit(spec, {"remove_features": ["boss"]})
    with pytest.raises(edit.EditError, match="not shell"):
        edit.apply_edit(spec, {"add_features": [{"id": "s", "type": "shell", "thickness": 1}]})
    with pytest.raises(edit.EditError, match="already exists"):
        edit.apply_edit(spec, {"add_features": [{**spec["features"][4]}]})
    new, changes = edit.apply_edit(spec, {"remove_features": ["upright_hole"]})
    assert changes == ["removed upright_hole", "removed upright_holes"]       # its pattern goes with it


def wav_bytes(seconds=1.0, rate=16000, channels=1):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels), w.setsampwidth(2), w.setframerate(rate)
        w.writeframes(np.zeros(int(seconds * rate) * channels, np.int16).tobytes())
    return buf.getvalue()


def test_wav_checks():
    assert voice.wav_info(wav_bytes())[1] == 16000
    for bad, msg in ((wav_bytes(rate=44100), "16 or 24 kHz"), (wav_bytes(channels=2), "mono"),
                     (wav_bytes(seconds=40), "under 30 s"), (b"RIFFnope", "not a WAV")):
        with pytest.raises(voice.VoiceError, match=msg):
            voice.wav_info(bad)


def test_transcribe_request_format(monkeypatch, tmp_path):
    monkeypatch.setenv("SPEND_LOG", str(tmp_path / "spend.jsonl"))
    sent = {}

    class Resp:
        status_code = 200

        def json(self):
            return {"sessionId": "s", "transcript": " make the holes M6 ", "audioDurationMs": 2300, "turns": []}

    def post(url, headers=None, files=None, timeout=None):
        sent.update(url=url, files=files)
        return Resp()

    monkeypatch.setattr(voice.requests, "post", post)
    out = voice.transcribe(wav_bytes(2.3))
    req = json.loads(sent["files"]["request"][1])
    assert sent["url"].endswith("/v1/asr/transcribe") and sent["files"]["audio"][2] == "audio/wav"
    assert req["model"] == "muse-voice-transcribe-1.0" and req["audioEncoding"] == "WAV"
    assert out["transcript"] == "make the holes M6" and out["cost_usd"] == pytest.approx(2 * 0.18 / 3600)
