"""The Talos loop with a scripted client: no API calls."""
import copy
import json
import os

import pytest

from conftest import EXAMPLES, example
from hephaestus import meta_api
from hephaestus.talos import run

DRAWING = os.path.join(os.path.dirname(EXAMPLES), "experiments", "reading-spike", "sketches", "03_block.png")


def callouts(name):
    with open(os.path.join(EXAMPLES, "callouts", f"{name}.json")) as f:
        return json.load(f)


class ScriptedClient:
    """Returns queued outputs per output schema name; records calls."""

    def __init__(self, **queues):
        self.queues = {k: list(v) for k, v in queues.items()}
        self.calls = []
        self.budget = meta_api.Budget(1.0)

    def respond(self, instructions, content, schema, name="Output", retries=1):
        self.budget.check()
        self.calls.append(name)
        self.budget.charge(0.001)
        info = {"usage": {"input_tokens": 1000, "output_tokens": 100}, "cost_usd": 0.001, "seconds": 0.1}
        return copy.deepcopy(self.queues[name].pop(0)), info


OK = {"matches": True, "issues": []}


def wrong_block():
    spec = example("block")
    spec["parameters"]["bore_d"]["value"] = 10
    return spec


def events_of(client, **kw):
    return list(run(DRAWING, client=client, **kw))


def test_read_check_correct_pass(tmp_path):
    client = ScriptedClient(ReadOutput=[{"spec": wrong_block(), "callouts": callouts("block")}],
                            CriticOutput=[OK, OK],
                            CorrectionOutput=[{"changelog": "bore 12", "spec": example("block"),
                                               "callouts": callouts("block")}])
    ev = events_of(client, out_dir=str(tmp_path))
    names = [e["event"] for e in ev]
    assert names == ["read_started", "spec", "compiled", "checks", "correction_started", "spec", "compiled",
                     "checks", "done"]
    first_issues = ev[3]["issues"]
    assert first_issues == [{"source": "callout",
                             "message": "callout 'Ø12 thru' (diameter of bore) measures 10 on the part, expected 12"}]
    done = ev[-1]
    assert done["status"] == "pass" and done["iterations"] == 1 and done["final_iteration"] == 1
    assert done["cost_usd"] == pytest.approx(0.004)
    json.dumps(ev)                                                     # every event is JSON-serialisable
    for f in ("spec.json", "part.stl", "part.step", "top.png", "sheet.png", "metrics.json"):
        assert (tmp_path / "iter_1" / f).exists()


def test_unchanged_correction_is_disputed():
    critic_says = {"matches": False, "issues": [{"feature_id": "bore", "issue": "bore missing", "severity": "high"}]}
    client = ScriptedClient(CriticOutput=[critic_says],
                            CorrectionOutput=[{"changelog": "kept, the drawing shows the bore", "spec": example("block"),
                                               "callouts": callouts("block")}])
    ev = events_of(client, start={"spec": example("block"), "callouts": callouts("block")})
    assert ev[-1]["status"] == "disputed" and ev[-1]["note"] == "kept, the drawing shows the bore"
    assert client.calls == ["CriticOutput", "CorrectionOutput"]


def test_medium_critic_issues_are_advisories():
    critic_says = {"matches": False, "issues": [{"feature_id": "bore", "issue": "bore looks off centre", "severity": "medium"}]}
    client = ScriptedClient(CriticOutput=[critic_says])
    ev = events_of(client, start={"spec": example("block"), "callouts": callouts("block")})
    checks = next(e for e in ev if e["event"] == "checks")
    assert checks["clean"] and checks["advisories"][0]["issue"] == "bore looks off centre"
    assert ev[-1]["status"] == "pass"


def test_same_issues_twice_stops_and_keeps_best():
    def bad(d):
        spec = example("block")
        spec["features"][1]["center"] = [85, 10]
        spec["parameters"]["bore_d"]["value"] = d
        return spec
    client = ScriptedClient(CorrectionOutput=[{"changelog": "smaller bore", "spec": bad(11), "callouts": callouts("block")}])
    ev = events_of(client, start={"spec": bad(12), "callouts": callouts("block")})
    assert ev[-1]["status"] == "stuck" and ev[-1]["final_iteration"] == 0
    assert client.calls == ["CorrectionOutput"]


def test_compile_error_goes_to_the_correction():
    bad = example("block")
    bad["features"][1]["center"] = [85, 10]
    client = ScriptedClient(CriticOutput=[OK],
                            CorrectionOutput=[{"changelog": "moved bore", "spec": example("block"),
                                               "callouts": callouts("block")}])
    ev = events_of(client, start={"spec": bad, "callouts": callouts("block")})
    assert ev[1]["ok"] is False and ev[2]["issues"][0]["source"] == "compile"
    assert "outside the part bbox" in ev[2]["issues"][0]["message"]
    assert ev[-1]["status"] == "pass" and client.calls == ["CorrectionOutput", "CriticOutput"]


def test_budget_stops_the_loop():
    client = ScriptedClient(CriticOutput=[OK])
    client.budget = meta_api.Budget(0.0)
    ev = events_of(client, start={"spec": wrong_block(), "callouts": callouts("block")})
    assert ev[-1]["status"] == "budget" and ev[-2]["event"] == "error"


def test_max_iters():
    def wrong(d):
        spec = example("block")
        spec["parameters"]["bore_d"]["value"] = d
        return spec
    client = ScriptedClient(CriticOutput=[OK] * 3,
                            CorrectionOutput=[{"changelog": f"bore {d}", "spec": wrong(d), "callouts": callouts("block")}
                                              for d in (9, 8)])
    ev = events_of(client, start={"spec": wrong(10), "callouts": callouts("block")}, max_iters=2)
    assert ev[-1]["status"] == "max_iters" and ev[-1]["iterations"] == 2
    assert ev[-1]["final_iteration"] == 0           # all versions have one issue: the first is kept
