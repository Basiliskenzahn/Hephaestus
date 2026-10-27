"""MOCK_MODELS=1: a stand-in for meta_api.Client that replays recorded model outputs.

Recordings (server/mock_data/*.json, written by build_gallery.py from real runs in experiments/*/results) hold the
model outputs of one run in order: ReadOutput, CriticOutput samples, CorrectionOutput. Compile, callout checks and
the loop logic run for real, so a mocked run exercises everything except the network call.
"""
import copy
import glob
import itertools
import json
import os
import time

from hephaestus import meta_api

from .config import MOCK_DELAY

HERE = os.path.dirname(os.path.abspath(__file__))
OK = {"matches": True, "issues": []}


def recordings():
    out = {}
    for path in sorted(glob.glob(os.path.join(HERE, "mock_data", "*.json"))):
        with open(path) as f:
            out[os.path.basename(path)[:-5]] = json.load(f)
    return out


_cycle = None


def next_image_recording():
    """Uploaded images get the recorded runs in turn (the drawing itself is ignored in mock mode)."""
    global _cycle
    recs = {k: v for k, v in recordings().items() if v.get("kind") == "image"}
    if _cycle is None:
        _cycle = itertools.cycle(sorted(recs))
    return recs[next(_cycle)]


def text_recording():
    return recordings()["text_l_bracket"]


class MockClient:
    """Same interface as meta_api.Client.respond; serves queued outputs per schema name."""

    def __init__(self, recording, budget=None, delay=MOCK_DELAY):
        self.queues = {k: list(v) for k, v in recording["outputs"].items()}
        self.budget = budget or meta_api.Budget(float("inf"))
        self.delay = delay
        self.calls = []

    def respond(self, instructions, content, schema, name="Output", retries=2, **kw):
        self.budget.check()
        time.sleep(self.delay)
        self.calls.append(name)
        queue = self.queues.get(name) or []
        if queue:
            out = queue.pop(0)
        elif name == "CriticOutput":
            out = OK
        else:
            raise meta_api.ModelError(f"mock: no recorded {name} left")
        info = {"usage": {"input_tokens": 0, "output_tokens": 0}, "cost_usd": 0.0, "seconds": self.delay,
                "model": "mock", "effort": kw.get("effort")}
        return copy.deepcopy(out), info
