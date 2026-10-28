"""/compile latency while runs decode SAM masks: idle, then during 3 concurrent mock runs of a phone photo.

  python compile_under_load.py --api http://localhost:8000 --label before
Start the server in mock mode with SAM replay first (no API calls):
  MOCK_MODELS=1 MOCK_DATA_DIR=<dir with a recording whose read has views> MOCK_SAM_REPLAY_DIR=<sheet.json+views.json>
  RUNS_PER_IP_PER_HOUR=100 uvicorn server.api:create_app --factory --port 8000
SAM_DECODE_WORKERS=0 decodes in the server process (the "before" of results/compile_latency.json).
"""
import argparse
import json
import os
import statistics as st
import threading
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PHOTO = os.path.join(ROOT, "experiments", "benchmark", "sketches", "06_angled_bracket__photo.jpg")


def compile_once(api, spec, k):
    t0 = time.perf_counter()
    r = requests.post(f"{api}/compile", json={"spec": spec, "overrides": {"t": 5 + (k % 5)}}, timeout=60)
    assert r.json().get("ok"), r.text[:200]
    return time.perf_counter() - t0


def run_once(api, out):
    t0 = time.perf_counter()
    with open(PHOTO, "rb") as f:
        rid = requests.post(f"{api}/runs", files={"image": ("photo.jpg", f, "image/jpeg")}, timeout=60).json()["run_id"]
    shape = None
    with requests.get(f"{api}/runs/{rid}/events", stream=True, timeout=300) as s:
        for line in s.iter_lines(decode_unicode=True):
            if line.startswith("data: ") and line != "data: {}":
                e = json.loads(line[6:])
                if e["event"] == "shape_check" and shape is None:
                    shape = e.get("available")
            if line.startswith("event: end"):
                break
    out.append({"seconds": time.perf_counter() - t0, "shape_available": shape})


def stats(xs):
    xs = sorted(xs)
    return {"n": len(xs), "p50_ms": round(1000 * st.median(xs)), "p95_ms": round(1000 * xs[int(0.95 * (len(xs) - 1))]),
            "max_ms": round(1000 * xs[-1])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--label", default="")
    args = ap.parse_args()
    with open(os.path.join(ROOT, "examples", "l_bracket.json")) as f:
        spec = json.load(f)
    compile_once(args.api, spec, 0)                                  # warm up
    idle = []
    for k in range(12):                       # same pace as under load (the per-IP limit is 8 per second)
        idle.append(compile_once(args.api, spec, k))
        time.sleep(0.25)
    runs = []
    threads = [threading.Thread(target=run_once, args=(args.api, runs)) for _ in range(3)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    loaded, k = [], 0
    while any(t.is_alive() for t in threads):
        loaded.append(compile_once(args.api, spec, k))
        k += 1
        time.sleep(0.25)
    for t in threads:
        t.join()
    res = {"label": args.label, "idle": stats(idle), "during_3_runs": stats(loaded),
           "runs_seconds": [round(r["seconds"], 1) for r in runs], "shape_available": [r["shape_available"] for r in runs],
           "wall_seconds": round(time.perf_counter() - t0, 1)}
    print(json.dumps(res))


if __name__ == "__main__":
    main()
