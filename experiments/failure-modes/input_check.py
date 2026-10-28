"""Does the read's input check (input_kind, problem) recognise inputs that are not parts? One dev read each.

  python input_check.py --cat /path/to/cat.jpg --max-usd 0.03
Inputs (made here, except the cat photo, which is not committed for licence reasons): a screenshot of the app, an
empty sheet photographed, a blurry and dark photo of the L-bracket, the L-bracket hand sketch without any text,
a photo of a real object on A4 (out of scope), and a cat.
"""
import argparse
import json
import os
import shutil
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "experiments", "hand-sketches"))

from hephaestus import meta_api  # noqa: E402
from hephaestus.prompts import READ_PROMPT  # noqa: E402
from hephaestus.spec import ReadOutput, json_schema  # noqa: E402
from hephaestus.talos import PRESETS  # noqa: E402

IN = os.path.join(HERE, "inputs")


def make_inputs():
    import draw_hand as dh
    import make_photos as mp
    os.makedirs(IN, exist_ok=True)
    shutil.copy(os.path.join(ROOT, "experiments", "smoke", "voice_edit.png"), os.path.join(IN, "screenshot.png"))
    rng = np.random.default_rng(7)
    blank = np.full((900, 1200, 3), 251, np.uint8)
    img, q = mp.photograph(mp.paper(blank, rng), "photo", rng)
    cv2.imwrite(os.path.join(IN, "empty_page.jpg"), cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, q])
    ph = cv2.imread(os.path.join(ROOT, "experiments", "reading-spike", "sketches", "02_l_bracket__photo.jpg"))
    dark = (cv2.GaussianBlur(ph, (0, 0), 7) * 0.22).astype(np.uint8)
    cv2.imwrite(os.path.join(IN, "blurry_dark.jpg"), dark, [cv2.IMWRITE_JPEG_QUALITY, 70])
    # the hand sketch with every word and number left out
    real_text, real_annot = dh.HandAxes.text, dh.HandAxes.annotate
    dh.HandAxes.text = lambda self, *a, **k: None
    dh.HandAxes.annotate = lambda self, text, xy, xytext=None, arrowprops=None, **k: real_annot(
        self, "", xy, xytext, arrowprops)
    out = dh.OUT
    dh.OUT = IN
    try:
        dh.draw("02_l_bracket", "patrick-hand", 12, False, False)
        os.replace(os.path.join(IN, "02_l_bracket.png"), os.path.join(IN, "no_dimensions.png"))
    finally:
        dh.HandAxes.text, dh.HandAxes.annotate, dh.OUT = real_text, real_annot, out
    shutil.copy(os.path.join(ROOT, "experiments", "object-photo", "photos", "plate__a.jpg"),
                os.path.join(IN, "object_photo.jpg"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cat")
    ap.add_argument("--max-usd", type=float, default=0.03)
    args = ap.parse_args()
    os.environ.setdefault("SPEND_SCRIPT", "experiments/failure-modes/input_check.py")
    make_inputs()
    s = PRESETS["dev"]
    client = meta_api.Client(s.read_model, budget=meta_api.Budget(args.max_usd))
    files = sorted(os.path.join(IN, f) for f in os.listdir(IN)) + ([args.cat] if args.cat else [])
    rows = []
    for path in files:
        out, info = client.respond(READ_PROMPT, [meta_api.text("Convert this drawing into the part spec and list its "
                                                               "dimensions."), meta_api.image(path)],
                                   json_schema(ReadOutput), "ReadOutput", model=s.read_model, effort=s.read_effort)
        row = {"input": "cat (Wikimedia Cat03.jpg, not committed)" if path == args.cat else os.path.basename(path),
               "input_kind": out.get("input_kind"), "problem": out.get("problem"), "seconds": info["seconds"],
               "cost_usd": round(info["cost_usd"], 6)}
        rows.append(row)
        print(json.dumps(row), flush=True)
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    with open(os.path.join(HERE, "results", "input_check.json"), "w") as f:
        json.dump(rows, f, indent=1)
    print(f"spent ${client.budget.spent:.4f}")


if __name__ == "__main__":
    main()
