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


MARGIN, CONTEXT = 0.25, 0.012


def region(box, margin=MARGIN, context=CONTEXT):
    """The crop around a text box, both 0-1000 normalised: 25 % of the box plus 1.2 % of the image on each side.
    Text boxes from grounding are often a few % off, so some margin is needed; a wider one (60 % + 2.5 %) took in a
    neighbouring number in half of the false flags (Oct 28 eval, re-checked offline on the saved boxes)."""
    x1, y1, x2, y2 = (float(v) for v in box)
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    mx, my = margin * (x2 - x1) + context * 1000, margin * (y2 - y1) + context * 1000
    return max(0, x1 - mx), max(0, y1 - my), min(1000, x2 + mx), min(1000, y2 + my)


def crowded(box, others):
    """True if more than half of another number's text box falls inside this crop: the re-read could read that one."""
    r = region(box)

    def norm(b):
        x1, y1, x2, y2 = (float(v) for v in b)
        return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)

    for o in others:
        o = norm(o)
        area = (o[2] - o[0]) * (o[3] - o[1])
        inter = max(0, min(r[2], o[2]) - max(r[0], o[0])) * max(0, min(r[3], o[3]) - max(r[1], o[1]))
        if area > 0 and inter > 0.5 * area:
            return True
    return False


def crop(img, box, margin=MARGIN, context=CONTEXT):
    """The text box (0-1000 normalised) cut from the full-resolution image (see region), upscaled if small. The
    re-read takes the number nearest the centre."""
    W, H = img.size
    x1, y1, x2, y2 = region(box, margin, context)
    b = (int(x1 / 1000 * W), int(y1 / 1000 * H), int(x2 / 1000 * W), int(y2 / 1000 * H))
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
    (confirmed | uncertain | unverified | not_located), second, reread, conflict}. A callout the read marked
    as conflicting is always uncertain."""
    img = Image.open(image if not isinstance(image, (bytes, bytearray)) else io.BytesIO(image))
    img.load()
    out = []
    jobs = []
    for k, c in enumerate(callouts or []):
        entry = {"index": k, "label": c.get("label"), "value": c.get("value"), "parameter": c.get("parameter"),
                 "text_box": c.get("text_box"), "confidence": c.get("confidence"), "status": "not_located",
                 "second": None, "reread": None, "conflict": c.get("conflict")}
        out.append(entry)
        # only numbers actually written: implied positions ("bore centred") have none to re-read
        if c.get("text_box") and isinstance(c.get("value"), (int, float)) and numbers(c.get("label")):
            others = [o["text_box"] for j, o in enumerate(callouts) if j != k and o.get("text_box")
                      and numbers(o.get("label")) and o["text_box"] != c["text_box"]]
            if crowded(c["text_box"], others):
                entry["status"] = "unverified"      # another number is too close to crop this one alone
                entry["note"] = "too close to another number to re-read on its own"
                continue
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
            if entry["conflict"]:
                # the read says it contradicts another number: the user picks (both readings, or the conflict)
                entry["status"] = "uncertain"
                if entry["second"] is not None and abs(entry["second"] - float(entry["value"])) < 1e-6:
                    entry["second"] = None
    for entry in out:
        if entry["conflict"] and entry["reread"] is None and entry["status"] in ("not_located", "unverified"):
            entry["status"] = "uncertain"
    return {"numbers": out, "calls": calls, "cost_usd": round(sum(c["cost_usd"] for c in calls), 6),
            "seconds": round(max([c["seconds"] for c in calls] or [0]), 1)}


def _schema():
    from .spec import json_schema
    return json_schema(RereadOutput)


VIEW_AXES = {"top": ("x", "y"), "front": ("x", "z"), "side": ("y", "z")}


def strength(number, callouts, check_rows=None, shape=None):
    """How the UI shows an uncertain number: 'soft' (a 'confirm?' chip: the two readings differ, nothing else says the
    first is wrong) or 'red' (another signal backs the other value: the read flagged a conflict with a specific
    alternative, the callout check measured a mismatch, or the shape check found the proportions of a view along
    this number's axis off). None for numbers that are not uncertain. Mirrors strengthOf in web/app.js."""
    if number.get("status") != "uncertain":
        return None
    if number.get("conflict") and number.get("second") is not None:
        return "red"
    k = number.get("index")
    row = (check_rows or [])[k] if check_rows and k is not None and k < len(check_rows) else None
    if row and row.get("status") == "mismatch":
        return "red"
    c = (callouts or [])[k] if callouts and k is not None and k < len(callouts) else None
    if c and c.get("measure") == "overall" and c.get("axis") and shape and shape.get("available"):
        for v in shape.get("views") or []:
            flagged = any("outline is" in m for m in (v.get("advisories") or []) + (v.get("issues") or []))
            if flagged and c["axis"] in VIEW_AXES.get(v.get("view"), ()):
                return "red"
    return "soft"
