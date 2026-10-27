"""SAM 3.1 on the Meta Model API: prompts, a cached call, output parsing and masks.

Docs: dev.meta.ai/docs/sam/ (segmenting, reading-segmentation, client-libraries).
- One user message: an input_text prompt (a short noun phrase, OR box/point tokens) and one input_image.
- Output: one line per frame, "<0f>" then records "id<|box;x1=..;y1=..;x2=..;y2=..;w=W;h=H[;c=..]|>"
  "<|mask;x=0;y=0[;c=..];data=H,W,<enc>payload|>", comma-separated. The mask raster (H rows x W columns) covers the
  record's box; its payload is base85 ("~" lossless, "!" one_bit) over an arithmetic-coded raster. We parse the line
  grammar ourselves and decode payloads with Meta's reference parser (pip meta-sam-parser), because the payload
  codec is not documented beyond that implementation.
- Billing: $2.50 per 1,000 images (dev.meta.ai/docs/pricing-rate-limits), i.e. $0.0025 per image request.

Every response is cached on disk, keyed by image hash + prompt + options, and never requested twice.
"""
import hashlib
import io
import json
import os
import re
import time

import numpy as np
import requests

from . import meta_api

MODEL = "sam-3.1"
PRICE_PER_IMAGE = 2.50 / 1000
MAX_OBJECTS = 16
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CACHE = os.path.join(_REPO, "experiments", "sam_cache")


class SamError(RuntimeError):
    pass


# ---------------------------------------------------------------- prompts
def box_token(x1, y1, x2, y2, w, h):
    return f"<|box;x1={int(round(x1))};y1={int(round(y1))};x2={int(round(x2))};y2={int(round(y2))};w={w};h={h}|>"


def point_token(x, y, w, h, negative=False):
    return ("-" if negative else "") + f"<|point;x={int(round(x))};y={int(round(y))};w={w};h={h}|>"


def object_prompt(objects, w, h):
    """objects: [{"id": int, "box": (x1, y1, x2, y2) inclusive, optional; "points": [(x, y), ...];
    "negative": [(x, y), ...]}] in image pixels -> the box/point prompt text for one image (frame 0)."""
    if not 0 < len(objects) <= MAX_OBJECTS:
        raise ValueError(f"SAM takes 1 to {MAX_OBJECTS} objects per request, got {len(objects)}")
    parts = []
    for ob in objects:
        tokens = []
        if ob.get("box") is not None:
            x1, y1, x2, y2 = ob["box"]
            x1, y1 = max(0, min(x1, w - 1)), max(0, min(y1, h - 1))
            x2, y2 = max(x1, min(x2, w - 1)), max(y1, min(y2, h - 1))
            tokens.append(box_token(x1, y1, x2, y2, w, h))
        tokens += [point_token(x, y, w, h) for x, y in ob.get("points", [])]
        tokens += [point_token(x, y, w, h, negative=True) for x, y in ob.get("negative", [])]
        if not tokens:
            raise ValueError(f"object {ob['id']} has no box or point")
        parts.append(f"{int(ob['id'])}" + "".join(tokens))
    return "<0f>" + ",".join(parts)


# ---------------------------------------------------------------- output
_RECORD = re.compile(r"(\d+)<\|box;([^|]*)\|><\|mask;([^|]*?)data=(\d+),(\d+),(.)(.*?)\|>", re.S)


def _fields(text):
    out = {}
    for part in text.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def parse(text):
    """Records of the first frame: [{"id", "box": (x1, y1, x2, y2), "size": (w, h), "conf", "mask_hw", "encoding",
    "payload"}]. The payload keeps its leading encoding marker, as the reference decoder expects."""
    records = []
    for m in _RECORD.finditer(text or ""):
        box = _fields(m.group(2))
        mask = _fields(m.group(3))
        enc = {"~": "lossless", "!": "one_bit"}.get(m.group(6))
        if enc is None:
            raise SamError(f"unknown mask encoding marker {m.group(6)!r}")
        conf = box.get("c") or mask.get("c")
        records.append({"id": int(m.group(1)),
                        "box": tuple(int(box[k]) for k in ("x1", "y1", "x2", "y2")),
                        "size": (int(box["w"]), int(box["h"])),
                        "conf": float(conf) if conf is not None else None,
                        "mask_hw": (int(m.group(4)), int(m.group(5))),
                        "encoding": enc, "payload": m.group(6) + m.group(7)})
    return records


def decode(record):
    """The record's mask raster as a bool array of shape mask_hw (rows, columns)."""
    from meta_sam_parser import SegmentationMask, decode_mask_to_raster
    h, w = record["mask_hw"]
    raster = decode_mask_to_raster(SegmentationMask(encoding=record["encoding"], payload=record["payload"],
                                                    width=w, height=h))
    return np.frombuffer(raster, dtype=np.uint8).reshape(h, w).astype(bool)


def full_mask(record):
    """The mask placed into a bool array of the full image size, covering the record's (inclusive) box."""
    w, h = record["size"]
    x1, y1, x2, y2 = record["box"]
    raster = decode(record)
    bw, bh = x2 - x1 + 1, y2 - y1 + 1
    if raster.shape != (bh, bw):                          # not at 1:1: scale the raster onto the box
        from PIL import Image
        raster = np.asarray(Image.fromarray(raster.astype(np.uint8) * 255).resize((bw, bh), Image.NEAREST)) > 127
    out = np.zeros((h, w), bool)
    out[y1:y1 + bh, x1:x1 + bw] = raster[: h - y1, : w - x1]
    return out


# ---------------------------------------------------------------- the call
def _key(image_bytes, prompt, metadata):
    hsh = hashlib.sha256(image_bytes).hexdigest()[:24]
    tail = hashlib.sha256((prompt + json.dumps(metadata or {}, sort_keys=True)).encode()).hexdigest()[:16]
    return f"{hsh}_{tail}"


def image_size(image_bytes):
    from PIL import Image
    with Image.open(io.BytesIO(image_bytes)) as im:
        return im.size


def segment(image_bytes, prompt, budget=None, cache_dir=None, metadata=None, mime=None, offline=False):
    """Run SAM 3.1 on one image (cached). Returns {"records", "text", "usage", "cost_usd", "seconds", "cached"}.
    `offline=True` only reads the cache (raises SamError on a miss) - for tests and replays."""
    cache_dir = cache_dir or os.environ.get("SAM_CACHE") or DEFAULT_CACHE
    key = _key(image_bytes, prompt, metadata)
    path = os.path.join(cache_dir, key + ".json")
    if os.path.exists(path):
        with open(path) as f:
            rec = json.load(f)
        return {**rec, "records": parse(rec["text"]), "cached": True, "cost_usd": 0.0}
    if offline:
        raise SamError("no cached SAM response for this image and prompt")
    if budget is not None:
        budget.check()
    mime = mime or ("image/png" if image_bytes[:4] == b"\x89PNG" else "image/jpeg")
    body = {"model": MODEL, "stream": True,
            "input": [{"type": "message", "role": "user", "content": [
                meta_api.text(prompt), meta_api.image(image_bytes, mime)]}]}
    if metadata:
        body["metadata"] = metadata
    key_env = os.environ.get("MODEL_API_KEY")
    headers = {"Authorization": f"Bearer {key_env}"} if key_env else {}
    t0 = time.time()
    last = None
    for attempt in range(3):
        try:
            r = requests.post(meta_api.API, headers=headers, json=body, timeout=(15, 300), stream=True)
            if r.status_code == 200:
                data = meta_api.read_stream(r.iter_lines())
                break
            last = f"HTTP {r.status_code}: {r.text[:400]}"
            if r.status_code < 500 and r.status_code != 429:
                raise SamError(last)
        except requests.RequestException as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(3 * (attempt + 1))
    else:
        raise SamError(last)
    text = "".join(c.get("text", "") for item in data.get("output", []) if item.get("type") == "message"
                   for c in item.get("content", []) if c.get("type") == "output_text")
    usage = data.get("usage", {})
    cost = PRICE_PER_IMAGE
    if budget is not None:
        budget.charge(cost)
    meta_api.log_spend(MODEL, usage, cost, "sam:" + prompt[:40])
    rec = {"prompt": prompt, "metadata": metadata, "text": text, "usage": usage, "seconds": round(time.time() - t0, 2),
           "image_size": list(image_size(image_bytes))}
    os.makedirs(cache_dir, exist_ok=True)
    with open(path, "w") as f:
        json.dump(rec, f)
    return {**rec, "records": parse(text), "cached": False, "cost_usd": cost}
