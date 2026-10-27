"""The web API in MOCK_MODELS mode: no Meta calls, recorded model outputs, real compile/check/loop."""
import io
import json
import os
import time

import pytest
from PIL import Image

from conftest import ROOT

pytest.importorskip("fastapi")


@pytest.fixture()
def api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from server import config
    monkeypatch.setattr(config, "MOCK_MODELS", True)
    monkeypatch.setattr(config, "MOCK_DELAY", 0.0)
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(config, "SPEND_LOG", str(tmp_path / "spend.jsonl"))
    from server import mock
    monkeypatch.setattr(mock, "MOCK_DELAY", 0.0)
    from server.api import create_app
    with TestClient(create_app()) as client:
        yield client, config


def sketch_bytes(name="02_l_bracket.png"):
    with open(os.path.join(ROOT, "experiments", "reading-spike", "sketches", name), "rb") as f:
        return f.read()


def read_events(client, run_id, headers=None):
    events = []
    with client.stream("GET", f"/runs/{run_id}/events", headers=headers or {}) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        ev_id = None
        for line in r.iter_lines():
            if line.startswith("id: "):
                ev_id = int(line[4:])
            if line.startswith("event: end"):
                break
            if line.startswith("data: ") and line != "data: {}":
                events.append((ev_id, json.loads(line[6:])))
    return events


def test_image_run_streams_events_and_files(api):
    client, _ = api
    r = client.post("/runs", files={"image": ("bracket.png", sketch_bytes(), "image/png")})
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    events = [e for _, e in read_events(client, run_id)]
    names = [e["event"] for e in events]
    assert names[0] == "read_started" and names[-1] == "done"
    compiled = next(e for e in events if e["event"] == "compiled")
    assert compiled["ok"] and compiled["files"]["part.glb"] == "iter_0/part.glb"
    for name in ("part.glb", "part.stl", "part.step", "sheet.png"):
        f = client.get(f"/runs/{run_id}/files/{compiled['files'][name]}")
        assert f.status_code == 200 and len(f.content) > 1000
    assert client.get(f"/runs/{run_id}/files/input.jpg").content[:2] == b"\xff\xd8"
    assert client.get(f"/runs/{run_id}/files/../../etc/passwd").status_code == 404


def test_reconnect_resumes_after_last_event_id(api):
    client, _ = api
    run_id = client.post("/runs", data={"prompt": "a 60x40 L-bracket, 5 mm thick"}).json()["run_id"]
    full = read_events(client, run_id)
    resumed = read_events(client, run_id, headers={"Last-Event-ID": str(full[2][0])})
    assert [i for i, _ in resumed] == [i for i, _ in full[3:]]


def test_text_run(api):
    client, _ = api
    r = client.post("/runs", data={"prompt": "a 60x40 L-bracket, 5 mm thick, two M5 holes in the upright"})
    assert r.json()["kind"] == "text"
    done = [e for _, e in read_events(client, r.json()["run_id"])][-1]
    assert done["status"] == "pass" and done["spec"]["parameters"]["hole_d"]["value"] == 5.5


def test_upload_validation_and_normalisation(api):
    client, _ = api
    assert client.post("/runs", files={"image": ("x.txt", b"hello", "text/plain")}).status_code == 400
    assert client.post("/runs").status_code == 400
    from server.images import normalise
    img = Image.new("RGB", (3000, 1500), "white")
    exif = Image.Exif()
    exif[0x0110] = "SecretPhone"            # camera model
    exif[0x0112] = 6                        # rotated 90 degrees
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    out = Image.open(io.BytesIO(normalise(buf.getvalue())))
    assert max(out.size) == 2000 and out.size == (1000, 2000)       # orientation applied, then downscaled
    assert not out.getexif() and b"SecretPhone" not in normalise(buf.getvalue())


def test_per_ip_limit_and_daily_cap(api):
    client, config = api
    for _ in range(config.RUNS_PER_IP_PER_HOUR):
        assert client.post("/runs", data={"prompt": "a plate"}).status_code == 200
    r = client.post("/runs", data={"prompt": "a plate"})
    assert r.status_code == 429 and r.json()["error"] == "busy" and "examples" in r.json()["message"]
    # daily cap: only checked for real model calls
    config.MOCK_MODELS = False
    with open(config.SPEND_LOG, "w") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()), "cost_usd": 5.01}) + "\n")
    r = client.post("/runs", data={"prompt": "a plate"}, headers={"x-forwarded-for": "10.0.0.9"})
    assert r.status_code == 429 and "budget" in r.json()["message"]
    assert client.get("/status").json()["busy"] is True


def test_compile_with_overrides(api):
    client, _ = api
    with open(os.path.join(ROOT, "examples", "plate.json")) as f:
        spec = json.load(f)
    client.post("/compile", json={"spec": spec})                      # warm up
    t0 = time.perf_counter()
    r = client.post("/compile", json={"spec": spec, "overrides": {"width": 100}}).json()
    elapsed = time.perf_counter() - t0
    assert r["ok"] and r["metrics"]["bbox"]["size"][0] == 100 and len(r["glb_b64"]) > 1000
    assert elapsed < 1.0
    step = client.get(r["files"]["part.step"])
    assert step.status_code == 200 and step.content.startswith(b"ISO-10303-21")
    bad = client.post("/compile", json={"spec": spec, "overrides": {"width": 10}}).json()
    assert not bad["ok"] and "below its minimum" in bad["errors"][0]
    worse = client.post("/compile", json={"spec": {"name": "x"}}).json()
    assert not worse["ok"] and worse["errors"]
