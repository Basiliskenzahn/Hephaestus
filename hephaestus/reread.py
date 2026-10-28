"""Independent re-read of every written number: catch misread handwriting the rest of the loop cannot see.

The read is self-consistent: if it reads a handwritten 40 as 60, the spec, the callouts and the checks all agree on
60. So each callout's number is read a second time, from a crop of where the read says it is written (callout
`text_box`), with no drawing, no part and no other numbers in view: one call, all crops as separate images,
"transcribe the number in each image". Same number -> confirmed. A different number, or "unclear" -> uncertain,
with both readings: the user picks (UI chips); nothing is corrected automatically.
"""
import io
import re

from PIL import Image

from . import meta_api
from .spec import _Model

from pydantic import Field

MAX_CROPS = 50              # images per request
MIN_HEIGHT = 96             # crops are upscaled to at least this height (px)
REREAD_PROMPT = ("Transcribe the number nearest the centre of each image exactly as written: digits, decimal point, "
                 "and any Ø, R, x or ° that belongs to it. If that number is crossed out, give the number written next to "
                 "it as its replacement. If you cannot read it, write 'unclear'. Return one reading per image, in order.")


class Reading(_Model):
    image: int = Field(description="the image number, starting at 1")
    text: str = Field(description="the number as written, or 'unclear'")


class RereadOutput(_Model):
    readings: list[Reading]


NUM = re.compile(r"\d+(?:[.,]\d+)?")


def numbers(text):
    return [float(n.replace(",", ".")) for n in NUM.findall(text or "")]


def crop(img, box, margin=0.6, context=0.025):
    """The text box (0-1000 normalised) cut from the full-resolution image, upscaled if small. The margin is generous
    (60 % of the box plus 2.5 % of the image on each side): text boxes from grounding are often a few % off, and a
    crop that misses the digits reads 'unclear'. The re-read takes the number nearest the centre."""
    W, H = img.size
    x1, y1, x2, y2 = (float(v) for v in box)
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    x1, x2, y1, y2 = x1 / 1000 * W, x2 / 1000 * W, y1 / 1000 * H, y2 / 1000 * H
    mx, my = margin * (x2 - x1) + context * W, margin * (y2 - y1) + context * H
    b = (int(max(0, x1 - mx)), int(max(0, y1 - my)), int(min(W, x2 + mx)), int(min(H, y2 + my)))
    if b[2] - b[0] < 4 or b[3] - b[1] < 4:
        return None
    c = img.crop(b)
    if c.height < MIN_HEIGHT:
        f = min(4.0, MIN_HEIGHT / c.height)
        c = c.resize((int(c.width * f), int(c.height * f)), Image.LANCZOS)
    buf = io.BytesIO()
    c.convert("RGB").save(buf, "JPEG", quality=92)
    return buf.getvalue()


def compare(callout, text):
    """(status, second reading) for one callout.
    confirmed: the re-read contains the callout's number, or only numbers of the same label (a partial reading of
    "R5 (4x)" as "4x"). uncertain: the re-read has a number the label does not, or says unclear while the read itself
    was not sure. unverified: unclear, but the read was confident (usually the crop missed the digits: shown, not
    highlighted)."""
    if text is None or "unclear" in text.lower() or not numbers(text):
        return ("unverified" if callout.get("confidence") == "high" else "uncertain"), None
    value, got = float(callout["value"]), numbers(text)
    written = numbers(callout.get("label"))
    if any(abs(g - value) < 1e-6 for g in got) or all(any(abs(g - w) < 1e-6 for w in written) for g in got):
        return "confirmed", value
    if value in written and len(written) == len(got):   # align "4x Ø6" with "4x Ø8": the number at the same place
        return "uncertain", got[written.index(value)]
    return "uncertain", got[-1] if len(got) == 1 or not written else got[min(len(got) - 1, len(written) - 1)]


def reread(client, image, callouts, model=None, effort="low"):
    """Re-read every callout that has a text box. Returns {numbers: [...], calls: [...], cost_usd, seconds}.
    numbers: one entry per callout: {index, label, value, parameter, text_box, confidence, status
    (confirmed | uncertain | unverified | not_located), second, reread}."""
    img = Image.open(image if not isinstance(image, (bytes, bytearray)) else io.BytesIO(image))
    img.load()
    out = []
    jobs = []
    for k, c in enumerate(callouts or []):
        entry = {"index": k, "label": c.get("label"), "value": c.get("value"), "parameter": c.get("parameter"),
                 "text_box": c.get("text_box"), "confidence": c.get("confidence"), "status": "not_located",
                 "second": None, "reread": None}
        out.append(entry)
        # only numbers actually written: implied positions ("bore centred") have none to re-read
        if c.get("text_box") and isinstance(c.get("value"), (int, float)) and numbers(c.get("label")):
            data = crop(img, c["text_box"])
            if data:
                jobs.append((entry, data))
    calls = []
    for start in range(0, len(jobs), MAX_CROPS):
        batch = jobs[start:start + MAX_CROPS]
        content = [meta_api.text(REREAD_PROMPT)]
        for i, (_, data) in enumerate(batch, 1):
            content += [meta_api.text(f"Image {i}:"), meta_api.image(data, "image/jpeg")]
        res, info = client.respond("You transcribe handwritten and printed numbers.", content,
                                   _schema(), "RereadOutput", model=model, effort=effort, max_output_tokens=4000)
        calls.append({"seconds": info["seconds"], "cost_usd": round(info["cost_usd"], 6), "crops": len(batch),
                      "model": info.get("model")})
        texts = {r.get("image"): r.get("text") for r in res.get("readings") or []}
        for i, (entry, _) in enumerate(batch, 1):
            text = texts.get(i)
            entry["reread"] = text
            entry["status"], entry["second"] = compare(entry, text)
    return {"numbers": out, "calls": calls, "cost_usd": round(sum(c["cost_usd"] for c in calls), 6),
            "seconds": round(max([c["seconds"] for c in calls] or [0]), 1)}


def _schema():
    from .spec import json_schema
    return json_schema(RereadOutput)
