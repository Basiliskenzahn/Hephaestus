"""Production smoke test: one live run (image or text), timed from the client; logs its model calls to spend.jsonl.

  python smoke.py --image path/to/drawing.jpg
  python smoke.py --prompt "a 60x40 L-bracket ..."
"""
import argparse
import datetime
import json
import os
import time

import requests

API = "https://david-julian-wermuth--hephaestus-web.us-east.modal.run"
HERE = os.path.dirname(os.path.abspath(__file__))
SPEND = os.path.join(os.path.dirname(HERE), "spend.jsonl")


def log_calls(events, label):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with open(SPEND, "a") as f:
        for e in events:
            call = e.get("call") if e["event"] == "spec" else e.get("critic_call") if e["event"] == "checks" else None
            if call:
                name = "CriticOutput x2" if e["event"] == "checks" else ("ReadOutput" if e.get("changelog") is None else "CorrectionOutput")
                f.write(json.dumps({"ts": now, "script": f"experiments/smoke/smoke.py ({label}, production)", "call": name,
                                    "model": call.get("model"), "input_tokens": call.get("input_tokens"),
                                    "output_tokens": call.get("output_tokens"), "cost_usd": call.get("cost_usd")}) + "\n")
        sam = [e["sam"] for e in events if e["event"] == "shape_check" and e.get("sam")]
        if sam and sam[-1]["calls"]:     # cumulative per run: log the final count once
            f.write(json.dumps({"ts": now, "script": f"experiments/smoke/smoke.py ({label}, production)",
                                "call": f"SAM x{sam[-1]['calls']}", "model": "sam-3.1", "input_tokens": 0,
                                "output_tokens": 0, "cost_usd": sam[-1]["cost_usd"]}) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image")
    ap.add_argument("--prompt")
    args = ap.parse_args()
    t0 = time.time()
    if args.image:
        r = requests.post(f"{API}/runs", files={"image": (os.path.basename(args.image), open(args.image, "rb"))}, timeout=120)
        label = "image " + os.path.basename(args.image)
    else:
        r = requests.post(f"{API}/runs", data={"prompt": args.prompt}, timeout=120)
        label = "text"
    r.raise_for_status()
    run = r.json()
    t_post = time.time() - t0
    events, marks = [], {}
    with requests.get(f"{API}/runs/{run['run_id']}/events", stream=True, timeout=600) as s:
        for line in s.iter_lines(decode_unicode=True):
            if line and line.startswith("data: ") and line != "data: {}":
                e = json.loads(line[6:])
                events.append(e)
                key = e["event"] if e["event"] != "compiled" else f"compiled_{e['iteration']}"
                marks.setdefault(key, round(time.time() - t0, 1))
                print(f"[{time.time() - t0:6.1f}s] {e['event']} it{e.get('iteration')}", flush=True)
            if line and line.startswith("event: end"):
                break
    done = events[-1]
    out = {"label": label, "run_id": run["run_id"], "post_seconds": round(t_post, 1), "marks": marks,
           "status": done.get("status"), "corrections": done.get("iterations"), "cost_usd": done.get("cost_usd"),
           "server_seconds": done.get("seconds"),
           "files": next((e.get("files") for e in events if e["event"] == "compiled" and e.get("ok")), None)}
    name = "text" if not args.image else "image"
    with open(os.path.join(HERE, f"{name}.events.json"), "w") as f:
        json.dump(events, f)
    with open(os.path.join(HERE, f"{name}.summary.json"), "w") as f:
        json.dump(out, f, indent=1)
    log_calls(events, label)
    print(json.dumps(out, indent=1))
    for n, rel in (out["files"] or {}).items():
        if n in ("part.glb", "part.stl", "part.step", "sheet.png"):
            g = requests.get(f"{API}/runs/{run['run_id']}/files/{rel}", timeout=60)
            print("file", n, g.status_code, len(g.content))


if __name__ == "__main__":
    main()
