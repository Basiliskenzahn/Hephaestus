"""A replayable run of the hand-sketched block (written 40, read as 60), for the web gallery. No API calls.

The model outputs are the recorded development-model ones (mock_data/block_numbers.json: the read and the re-read,
Oct 28); the loop runs here on them, without the critic and the SAM shape check (neither was recorded for this
sketch), and the event times are shifted by the recorded call times (read 28.3 s, re-read 13.4 s) so the replay
paces like the real run.

  python experiments/numbers/record_block.py      # -> results/block_replay.json, then python -m server.build_gallery
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from hephaestus.talos import run  # noqa: E402
from server.mock import MockClient  # noqa: E402

IMAGE = "experiments/hand-sketches/sketches/03_block.png"
READ_S, REREAD_S = 28.3, 13.4


def main():
    with open(os.path.join(HERE, "mock_data", "block_numbers.json")) as f:
        rec = json.load(f)
    client = MockClient(rec, delay=0)
    events = list(run(os.path.join(ROOT, IMAGE), client=client, use_critic=False, use_shape=False))
    for e in events:
        if e["event"] == "spec" and e.get("call"):
            e["call"].update(seconds=READ_S, model="muse-spark-1.3-contributor")
        if e["event"] != "read_started":
            e["t"] = round(e["t"] + READ_S, 1)
        if e["event"] == "numbers":
            e["t"] = round(max(e["t"], REREAD_S + READ_S), 1)
            e["seconds"] = REREAD_S
    events.sort(key=lambda e: (e["event"] == "done", e["t"]))
    events[-1]["t"] = events[-1]["seconds"] = max(e["t"] for e in events)
    out = {"part": "03_block", "set": "hand", "image": IMAGE, "events": events}
    with open(os.path.join(HERE, "results", "block_replay.json"), "w") as f:
        json.dump(out, f, indent=1)
    print([(e["event"], e["t"]) for e in events])


if __name__ == "__main__":
    main()
