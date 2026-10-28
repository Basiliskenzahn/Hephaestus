"""Does the `views` field (view boxes for the shape check) make the read slower? Same read with and without it.

  python views_ab.py --max-usd 0.03                  # dev preset (contributor model), 3 images x 2 variants
Writes results/<preset>.json with seconds, output and reasoning tokens per call.
"""
import argparse
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from hephaestus import meta_api  # noqa: E402
from hephaestus.prompts import READ_PROMPT, VIEWS_GUIDE  # noqa: E402
from hephaestus.spec import ReadOutput, json_schema  # noqa: E402
from hephaestus.talos import PRESETS  # noqa: E402

IMAGES = ["reading-spike/sketches/02_l_bracket__photo.jpg", "benchmark/sketches/06_angled_bracket__photo.jpg",
          "reading-spike/sketches/01_plate__rough.jpg"]


def variants():
    with_views = json_schema(ReadOutput)
    without = copy.deepcopy(with_views)
    without["properties"].pop("views")
    without["required"] = [k for k in without["required"] if k != "views"]
    prompt_without = READ_PROMPT.replace("\n\n" + VIEWS_GUIDE, "")
    assert prompt_without != READ_PROMPT
    return {"with_views": (READ_PROMPT, with_views), "without_views": (prompt_without, without)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="dev")
    ap.add_argument("--max-usd", type=float, default=0.03)
    args = ap.parse_args()
    os.environ.setdefault("SPEND_SCRIPT", "experiments/read-views/views_ab.py")
    s = PRESETS[args.preset]
    client = meta_api.Client(s.read_model, budget=meta_api.Budget(args.max_usd))
    rows = []
    for img in IMAGES:
        for name, (prompt, schema) in variants().items():
            out, info = client.respond(prompt, [meta_api.text("Convert this drawing into the part spec and list its "
                                                              "dimensions."),
                                                meta_api.image(os.path.join(ROOT, "experiments", img))],
                                       schema, "ReadOutput", model=s.read_model, effort=s.read_effort)
            u = info["usage"]
            row = {"image": os.path.basename(img), "variant": name, "seconds": info["seconds"],
                   "output_tokens": u.get("output_tokens"),
                   "reasoning_tokens": (u.get("output_tokens_details") or {}).get("reasoning_tokens"),
                   "cost_usd": round(info["cost_usd"], 6), "views": out.get("views"),
                   "answer_chars": len(json.dumps(out))}
            rows.append(row)
            print(json.dumps({k: v for k, v in row.items() if k != "views"}), flush=True)
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    with open(os.path.join(HERE, "results", f"{args.preset}.json"), "w") as f:
        json.dump(rows, f, indent=1)
    print(f"spent ${client.budget.spent:.4f}")


if __name__ == "__main__":
    main()
