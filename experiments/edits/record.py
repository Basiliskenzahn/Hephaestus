"""Record voice edits for the offline tests: synthetic speech -> Muse Voice Transcribe -> edit call (dev preset).

  python record.py --max-usd 0.05        # needs espeak-ng; writes audio/*.wav and ../../tests/data/edits.json
Every API response is kept, so tests/test_edit.py replays them without calls.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import wave

import numpy as np
from scipy.signal import resample_poly

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from hephaestus import edit, meta_api, voice  # noqa: E402
from hephaestus.talos import PRESETS  # noqa: E402

CASES = [  # name, part, spoken instruction, what a correct edit does
    ("holes_m6", "l_bracket", "make the holes M6", "hole_d 5 -> 6.6"),
    ("thicker", "l_bracket", "make it 10 millimetres thicker", "t 5 -> 15"),
    ("width", "l_bracket", "width 100", "width 60 -> 100"),
    ("fillet", "l_bracket", "add a 2 millimetre fillet on the outer edges", "new fillet r2"),
    ("remove_slot", "l_bracket", "remove the slot", "base_slot removed"),
    ("four_holes", "l_bracket", "four holes instead of two", "pattern count 2 -> 4"),
    ("width_typed", "l_bracket", "TYPED:width 100", "the user corrects the misheard 'with one hundred'"),
    ("ambiguous", "l_bracket", "make it bigger", "clarification"),
    ("impossible", "plate", "make the hole bigger than the plate", "clarification or a build error"),
]
OUT = os.path.join(ROOT, "tests", "data", "edits.json")


def speak(text, path):
    """espeak-ng (22.05 kHz) -> mono 16-bit 16 kHz WAV, the format the browser uploads."""
    with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
        subprocess.run(["espeak-ng", "-v", "en-us", "-s", "150", "-w", tmp.name, text], check=True)
        with wave.open(tmp.name) as w:
            rate, x = w.getframerate(), np.frombuffer(w.readframes(w.getnframes()), np.int16)
    y = resample_poly(x.astype(np.float32), 16000, rate)
    pad = np.zeros(4000, np.float32)                       # 0.25 s of silence on both ends, like a real clip
    y = np.clip(np.concatenate([pad, y, pad]), -32768, 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
        w.writeframes(y.tobytes())


class Recorder:
    """Wraps a client and keeps every raw edit output."""

    def __init__(self, client):
        self.client, self.outputs = client, []
        self.budget = client.budget

    def respond(self, *a, **kw):
        out, info = self.client.respond(*a, **kw)
        self.outputs.append(out)
        return out, info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-usd", type=float, default=0.05)
    ap.add_argument("--only")
    args = ap.parse_args()
    os.environ.setdefault("SPEND_SCRIPT", "experiments/edits/record.py")
    budget = meta_api.Budget(args.max_usd)
    s = PRESETS["dev"]
    recs = {r["name"]: r for r in json.load(open(OUT))} if os.path.exists(OUT) else {}
    os.makedirs(os.path.join(HERE, "audio"), exist_ok=True)
    for name, part, text, expect in CASES:
        if args.only and name not in args.only.split(","):
            continue
        typed = text.startswith("TYPED:")
        rec = recs.get(name) or {"name": name, "part": part, "expect": expect,
                                 **({"typed": text[6:]} if typed else {"spoken": text})}
        if typed:
            rec["asr"] = {"transcript": text[6:], "audio_ms": 0, "seconds": 0, "cost_usd": 0}
        elif "asr" not in rec:
            wav = os.path.join(HERE, "audio", f"{name}.wav")
            if not os.path.exists(wav):
                speak(text, wav)
            rec["asr"] = voice.transcribe(open(wav, "rb").read(), budget=budget)
        spec = json.load(open(os.path.join(ROOT, "examples", f"{part}.json")))
        callouts = json.load(open(os.path.join(ROOT, "examples", "callouts", f"{part}.json")))
        client = Recorder(meta_api.Client(s.read_model, budget=budget))
        res = edit.edit_part(spec, rec["asr"]["transcript"], client, model=s.read_model, callouts=callouts)
        rec.update(outputs=client.outputs, status=res["status"], explanation=res["explanation"],
                   clarification=res["clarification"], changes=res["changes"], errors=res["errors"],
                   calls=res["calls"], seconds=res["seconds"])
        recs[name] = rec
        print(f"{name:12} heard={rec['asr']['transcript']!r} ({rec['asr']['seconds']} s) -> {res['status']}: "
              f"{res['explanation'] or res['clarification'] or res['errors']} | {res['changes']} "
              f"[{res['seconds']} s, ${res['cost_usd']:.4f}]", flush=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump([recs[c[0]] for c in CASES if c[0] in recs], f, indent=1)
    print(f"spent ${budget.spent:.4f}")


if __name__ == "__main__":
    main()
