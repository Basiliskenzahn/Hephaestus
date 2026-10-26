import copy
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hephaestus.compile import compile  # noqa: E402

EXAMPLES = os.path.join(ROOT, "examples")
NAMES = ["plate", "l_bracket", "block", "bushing", "enclosure"]


def example(name):
    """A fresh, mutable copy of an example spec dict."""
    with open(os.path.join(EXAMPLES, f"{name}.json")) as f:
        return copy.deepcopy(json.load(f))


@pytest.fixture(scope="session")
def compiled():
    cache = {}

    def get(name):
        if name not in cache:
            cache[name] = compile(example(name))
        return cache[name]
    return get
