"""Build the example gallery (web/gallery/) and the mock recordings (server/mock_data/) from recorded runs.

No API calls: every spec version in a recorded event stream is recompiled here (deterministic) to produce the GLB for
the viewer, the drawing sheet, and STL/STEP downloads.

  python -m server.build_gallery
"""
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import trimesh  # noqa: E402

from hephaestus.compile import SpecError, compile  # noqa: E402
from hephaestus.drawing import render_sheet  # noqa: E402

RESULTS = os.path.join(ROOT, "experiments", "benchmark", "results")
SKETCHES = {"0" + str(i): os.path.join(ROOT, "experiments", "reading-spike", "sketches") for i in (1, 2, 3)}
SKETCHES.update({"0" + str(i): os.path.join(ROOT, "experiments", "benchmark", "sketches") for i in range(4, 9)})
GALLERY = os.path.join(ROOT, "web", "gallery")
MOCK = os.path.join(ROOT, "server", "mock_data")

# id, recorded run, title, note shown with the replay
PICKS = [
    # the development-model enclosure catch-and-fix is superseded by the live one in web/replays (final benchmark)
    # the number check: the read took a handwritten 40 for 60, the independent re-read says 40; one tap fixes it
    # (development-model outputs, replayed through the loop by experiments/numbers/record_block.py)
    ("block", "experiments/numbers/results/block_replay.json", "Block, hand sketch",
     "Caught: the read took the handwritten 40 for a 60; the second, independent reading says 40. Tap the 60 "
     "on the drawing to fix it (no model call). Recorded with the cheaper development model."),
    ("shaft_collar", "muse-spark-1.3/rerecord/08_shaft_collar__photo.run1.json", "Shaft collar, phone photo",
     "A split ring with a cross hole: right on the first read, outlines checked by SAM 3.1."),
    ("l_bracket", "muse-spark-1.3/live/02_l_bracket__clean.run1.json", "L-bracket, drawing",
     "Three views, a slot and two holes: right on the first read."),
    ("bushing", "muse-spark-1.3/live/04_bushing__photo.run1.json", "Flanged bushing, phone photo",
     "A revolved part with a bolt circle."),
    ("pocket_plate", "muse-spark-1.3/live/07_pocket_plate__photo.run1.json", "Pocket plate, phone photo",
     "Pocket, counterbored holes; spacing derived from the insets."),
    ("plate", "muse-spark-1.3/live/01_plate__photo.run1.json", "Mounting plate, phone photo",
     "The simplest part, in 27 seconds."),
]


def model_outputs(events):
    """The model outputs of a recorded run, in order, as the mock client serves them."""
    out = {"ReadOutput": [], "CriticOutput": [], "CorrectionOutput": [], "RereadOutput": []}
    for e in events:
        if e["event"] == "spec" and e.get("changelog") is None:
            out["ReadOutput"].append({"spec": e["spec"], "callouts": e["callouts"]})
        elif e["event"] == "spec":
            disputes = [{"index": d["index"], "reason": d.get("reason") or "", "replacement": d["replacement"]}
                        for d in e.get("disputes") or [] if d.get("replacement")]
            out["CorrectionOutput"].append({"changelog": e["changelog"] or "", "spec": e["spec"],
                                            "disputed_callouts": disputes})
        elif e["event"] == "numbers" and e.get("available"):
            texts = [n["reread"] for n in e["numbers"] if n.get("reread") is not None]
            out["RereadOutput"].append({"readings": [{"image": i, "text": t} for i, t in enumerate(texts, 1)]})
        elif e["event"] == "checks" and e.get("critic"):
            crit = e["critic"]
            out["CriticOutput"] += crit["samples"] if "samples" in crit else [crit]
    return out


def model_of(events):
    for e in events:
        if (e.get("call") or {}).get("model"):
            return e["call"]["model"]
    return "muse-spark-1.3-contributor"


def build_item(pid, rel, title, note, out_root=GALLERY, write_mock=True):
    rec = json.load(open(os.path.join(ROOT, rel) if rel.startswith("experiments/") else os.path.join(RESULTS, rel)))
    part = rec.get("part") or rec.get("key")
    prompt = rec.get("prompt") if not rec.get("image") and rec.get("prompt") else None
    image = None if prompt else os.path.join(ROOT, rec["image"]) if rec.get("image") else os.path.join(
        SKETCHES[part[:2]], {"photo": f"{part}__photo.jpg", "rough": f"{part}__rough.jpg", "clean": f"{part}.png"}[rec["set"]])
    out = os.path.join(out_root, pid)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    ext = os.path.splitext(image)[1] if image else ""
    if image:
        shutil.copy(image, os.path.join(out, "input" + ext))
    events, iteration_files = [], {}
    for e in rec["events"]:
        e = dict(e)
        if e["event"] == "spec":
            k = e["iteration"]
            idir = os.path.join(out, f"iter_{k}")
            try:
                result = compile(e["spec"])
            except SpecError:
                iteration_files[k] = None
            else:
                os.makedirs(idir, exist_ok=True)
                with open(os.path.join(idir, "part.glb"), "wb") as f:
                    f.write(trimesh.load(__import__("io").BytesIO(result.stl), file_type="stl").export(file_type="glb"))
                with open(os.path.join(idir, "sheet.png"), "wb") as f:
                    f.write(render_sheet(result))
                with open(os.path.join(idir, "part.stl"), "wb") as f:
                    f.write(result.stl)
                with open(os.path.join(idir, "part.step"), "wb") as f:
                    f.write(result.step)
                iteration_files[k] = {n: f"iter_{k}/{n}" for n in ("part.glb", "sheet.png", "part.stl", "part.step")}
        if e["event"] == "compiled":
            e.pop("files", None)
            if e.get("ok") and iteration_files.get(e["iteration"]):
                e["files"] = iteration_files[e["iteration"]]
        events.append(e)
    with open(os.path.join(out, "events.json"), "w") as f:
        json.dump(events, f, separators=(",", ":"))
    done = events[-1]
    entry = {"id": pid, "title": title, "note": note, "input": f"input{ext}" if image else None, "model": model_of(rec["events"]),
             "seconds": done.get("seconds"), "corrections": done.get("iterations", 0), "status": done.get("status")}
    if prompt:
        entry["prompt"] = prompt
    if write_mock:
        with open(os.path.join(MOCK, f"{pid}.json"), "w") as f:
            json.dump({"kind": "image", "source": rel, "outputs": model_outputs(rec["events"])}, f)
    return entry


TEXT_PROMPT = "a 60x40 L-bracket, 5 mm thick, 40 mm tall, two M5 holes in the upright 30 mm apart"


def text_recording():
    """A hand-written model output for the text path in mock mode (no recorded text run exists yet)."""
    with open(os.path.join(ROOT, "examples", "l_bracket.json")) as f:
        spec = json.load(f)
    spec["features"] = [ft for ft in spec["features"] if ft["id"] != "base_slot"]
    spec["parameters"].pop("slot_y")
    spec["parameters"].pop("slot_len")
    spec["parameters"].pop("slot_w")
    spec["parameters"]["hole_d"] = {"value": 5.5, "description": "M5 clearance hole"}
    spec["description"] = "From the text. Assumed: holes centred side to side at mid-height of the upright."
    callouts = [{"label": "60", "value": 60, "measure": "overall", "axis": "x", "feature": None, "feature_b": None, "edge": None},
                {"label": "40", "value": 40, "measure": "overall", "axis": "y", "feature": None, "feature_b": None, "edge": None},
                {"label": "5 mm thick", "value": 5, "measure": "thickness", "axis": None, "feature": "base", "feature_b": None, "edge": None},
                {"label": "40 mm tall", "value": 40, "measure": "overall", "axis": "z", "feature": None, "feature_b": None, "edge": None},
                {"label": "30 mm apart", "value": 30, "measure": "distance", "axis": "x", "feature": "upright_holes[0]",
                 "feature_b": "upright_holes[1]", "edge": None}]
    with open(os.path.join(MOCK, "text_l_bracket.json"), "w") as f:
        json.dump({"kind": "text", "prompt": TEXT_PROMPT, "outputs": {"ReadOutput": [{"spec": spec, "callouts": callouts}]}},
                  f, indent=1)


# the demo runs of the final benchmark (live preset, Nov 2026), full event streams, playable like the gallery:
# web/replays/<id>/ (events.json, input, compiled iterations) + web/replays/index.json. Filled in after the run.
REPLAYS_DIR = os.path.join(ROOT, "web", "replays")
F = "experiments/final/results/"
REPLAYS = [
    ("enclosure_hand_catch", F + "hand/05_enclosure__scan.json", "Enclosure, hand sketch",
     "Caught: the cable hole went through the back wall too; the second look (Muse Spark, fresh context) saw it, "
     "one correction fixed it. Live model, final benchmark."),
    ("bushing_hand_confirm", F + "hand/04_bushing__photo.json", "Bushing, hand sketch photo",
     "The read took the flange's handwritten 5 for a 3; the second reading could not read it, so the number asks to "
     "be confirmed. Tap it and type 5: the part rebuilds, no model call. Live model, final benchmark."),
    ("nema17_text", F + "text/nema17.json", "Text: NEMA 17 motor plate",
     "No drawing: the named standard gives the sizes (31 mm hole pattern, 22 mm pilot), every choice marked assumed. "
     "Live model, final benchmark."),
    ("l_bracket_hand", F + "hand/02_l_bracket__photo.json", "L-bracket, hand sketch photo",
     "A sloppy, not-to-scale hand sketch photographed on a desk: right on the first read. Live model, final benchmark."),
]


def build_replays():
    os.makedirs(REPLAYS_DIR, exist_ok=True)
    entries = [build_item(*r, out_root=REPLAYS_DIR, write_mock=False) for r in REPLAYS]
    with open(os.path.join(REPLAYS_DIR, "index.json"), "w") as f:
        json.dump(entries, f, indent=1)
    for e in entries:
        print("replay", e["id"], e["status"], e["corrections"], e["model"])


def main():
    if "--replays" in sys.argv:
        return build_replays()
    os.makedirs(GALLERY, exist_ok=True)
    os.makedirs(MOCK, exist_ok=True)
    entries = [build_item(*p) for p in PICKS]
    with open(os.path.join(GALLERY, "index.json"), "w") as f:
        json.dump(entries, f, indent=1)
    text_recording()
    for e in entries:
        print(e["id"], e["status"], e["corrections"], e["model"])


if __name__ == "__main__":
    main()
