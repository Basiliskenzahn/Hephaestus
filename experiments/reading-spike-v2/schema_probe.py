"""Step 1: does the Meta API accept the spec v1 JSON Schema as structured output?

Sends one clean sketch with the exported schema and records the HTTP outcome, whether the output parses,
validates against PartSpec and compiles. Results go to results/schema_probe/raw[_strict].json.

  python schema_probe.py              # json_schema() exactly as exported, non-strict
  python schema_probe.py --strict     # same with strict: true
"""
import argparse
import base64
import json
import os
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "experiments", "reading-spike"))

from spike import API, cost, read_stream  # noqa: E402

from hephaestus.compile import SpecError, compile  # noqa: E402
from hephaestus.spec import PartSpec, json_schema  # noqa: E402
from prompt import SYSTEM_PROMPT  # noqa: E402

SKETCHES = os.path.join(ROOT, "experiments", "reading-spike", "sketches")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="send strict: true in text.format")
    ap.add_argument("--image", default="01_plate.png")
    ap.add_argument("--model", default="muse-spark-1.3-contributor")
    args = ap.parse_args()

    schema = json_schema()
    with open(os.path.join(SKETCHES, args.image), "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    fmt = {"type": "json_schema", "name": "PartSpec", "schema": schema}
    if args.strict:
        fmt["strict"] = True
    body = {"model": args.model, "reasoning": {"effort": "medium"}, "instructions": SYSTEM_PROMPT, "stream": True,
            "input": [{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "Convert this drawing into the part spec."},
                {"type": "input_image", "image_url": f"data:image/png;base64,{b64}"}]}],
            "text": {"format": fmt}}
    key = os.environ.get("MODEL_API_KEY")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    t0 = time.time()
    r = requests.post(API, headers=headers, json=body, timeout=(15, 120), stream=True)
    record = {"shape": "raw", "strict": args.strict, "image": args.image, "model": args.model,
              "schema_bytes": len(json.dumps(schema)), "http_status": r.status_code}
    if r.status_code != 200:
        record["error"] = r.text[:2000]
    else:
        data = read_stream(r.iter_lines())
        record["seconds"] = round(time.time() - t0, 1)
        record["usage"] = data.get("usage", {})
        record["cost_usd"] = cost(args.model, record["usage"])
        text = next((c["text"] for item in data.get("output", []) if item.get("type") == "message"
                     for c in item.get("content", []) if c.get("type") == "output_text"), None)
        record["output_text"] = text
        try:
            out = json.loads(text)
            record["parsed"] = True
        except (TypeError, json.JSONDecodeError) as e:
            record["parsed"], record["parse_error"] = False, str(e)
            out = None
        if out is not None:
            try:
                spec = PartSpec.model_validate(out)
                record["validates"] = True
                try:
                    res = compile(spec)
                    record["compiles"] = True
                    record["bbox"] = res.metrics["bbox"]["size"]
                except SpecError as e:
                    record["compiles"], record["compile_errors"] = False, e.errors
            except Exception as e:  # noqa: BLE001
                record["validates"], record["validation_error"] = False, str(e)[:2000]
    outdir = os.path.join(HERE, "results", "schema_probe")
    os.makedirs(outdir, exist_ok=True)
    name = f"raw{'_strict' if args.strict else ''}.json"
    with open(os.path.join(outdir, name), "w") as f:
        json.dump(record, f, indent=2)
    print(json.dumps({k: v for k, v in record.items() if k != "output_text"}, indent=2)[:3000])


if __name__ == "__main__":
    main()
