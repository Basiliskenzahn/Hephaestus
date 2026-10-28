"""Run manager: each run executes the Talos loop in a background thread and collects its events in memory.

Run state is in memory (one container serves all runs; see modal_app.py). Files live in RUNS_DIR/<run_id>/ and are
deleted after RETENTION_HOURS.
"""
import datetime
import io
import json
import os
import secrets
import shutil
import threading
import time
import traceback

import trimesh

from hephaestus import meta_api, sam, talos

from . import config


def today_utc_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT00:00:00")


def spent_today():
    return meta_api.spent_since(config.SPEND_LOG, today_utc_iso())


class GuardedBudget(meta_api.Budget):
    """A run's own cap, and the global daily cap (read from the spend log every model call writes)."""

    def check(self):
        super().check()
        if spent_today() >= config.DAILY_CAP_USD:
            raise meta_api.BudgetExceeded(f"the daily spend cap of ${config.DAILY_CAP_USD:.2f} is reached")


def glb_from_stl(stl_path, glb_path):
    mesh = trimesh.load(stl_path, file_type="stl")
    with open(glb_path, "wb") as f:
        f.write(mesh.export(file_type="glb"))


def publish_files(event, run_dir):
    """compiled events: absolute paths -> names relative to the run folder, plus a GLB for the viewer."""
    files = event.get("files") or {}
    if not files:
        return event
    out = {}
    for name, path in files.items():
        out[name] = os.path.relpath(path, run_dir)
    stl = files.get("part.stl")
    if stl:
        glb = os.path.join(os.path.dirname(stl), "part.glb")
        glb_from_stl(stl, glb)
        out["part.glb"] = os.path.relpath(glb, run_dir)
    return {**event, "files": out}


def publish_shape(event, run_dir):
    """shape_check events: overlay paths -> names relative to the run folder."""
    def rel(files):
        return {k: os.path.relpath(v, run_dir) for k, v in (files or {}).items() if v}
    views = [{**v, "files": rel(v.get("files"))} for v in event.get("views") or []]
    return {**event, "files": rel(event.get("files")), "views": views}


def log_line(rec):
    """Append a timing line to RUNS_LOG (runs, transcriptions, edits)."""
    rec = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), **rec}
    print("timing", json.dumps(rec), flush=True)                     # Modal logs
    try:
        os.makedirs(os.path.dirname(config.RUNS_LOG), exist_ok=True)
        with open(config.RUNS_LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        traceback.print_exc()


class Run:
    def __init__(self, run_id, ip, kind, prompt=None):
        self.id, self.ip, self.kind, self.prompt = run_id, ip, kind, prompt
        self.created = time.time()
        self.dir = os.path.join(config.RUNS_DIR, run_id)
        self.events, self.done = [], False


class RunManager:
    def __init__(self, client_factory, on_finish=None):
        """client_factory(run, budget) -> object with meta_api.Client.respond; on_finish() persists (Modal volume)."""
        self.client_factory, self.on_finish = client_factory, on_finish
        self.runs, self.lock = {}, threading.Lock()

    def start(self, ip, image_bytes=None, prompt=None):
        run = Run(secrets.token_urlsafe(9), ip, "text" if prompt else "image", prompt)
        os.makedirs(run.dir, exist_ok=True)
        if image_bytes:
            with open(os.path.join(run.dir, "input.jpg"), "wb") as f:
                f.write(image_bytes)
        with self.lock:
            self.runs[run.id] = run
        threading.Thread(target=self._work, args=(run,), daemon=True).start()
        return run

    def get(self, run_id):
        return self.runs.get(run_id)

    def _emit(self, run, event):
        run.events.append(event)
        with open(os.path.join(run.dir, "events.jsonl"), "a") as f:
            f.write(json.dumps(event) + "\n")

    def _work(self, run):
        budget = GuardedBudget(config.RUN_CAP_USD)
        client = self.client_factory(run, budget)
        settings = talos.PRESETS[config.PRESET]
        # SAM 3.1 shares the run's budget and the daily cap; its cache lives (and is deleted) with the run.
        # Mock mode never calls SAM: the cache is empty, so the shape check reports itself unavailable.
        segmenter = sam.Segmenter(budget=budget, cache_dir=os.path.join(run.dir, "sam"), offline=config.MOCK_MODELS,
                                  tag=run.id)
        try:
            kwargs = {"prompt": run.prompt} if run.kind == "text" else {"drawing": os.path.join(run.dir, "input.jpg")}
            for event in talos.run(client=client, out_dir=run.dir, settings=settings, segmenter=segmenter, **kwargs):
                if event["event"] == "compiled":
                    event = publish_files(event, run.dir)
                elif event["event"] == "shape_check":
                    event = publish_shape(event, run.dir)
                self._emit(run, event)
        except Exception as e:  # noqa: BLE001 - never leave a run hanging without a done event
            traceback.print_exc()
            self._emit(run, {"event": "error", "message": f"internal error: {type(e).__name__}: {e}"})
            self._emit(run, {"event": "done", "status": "error", "note": str(e), "spec": None})
        finally:
            run.done = True
            self._log_timing(run)
            if self.on_finish:
                self.on_finish()

    def _log_timing(self, run):
        """One line per run in RUNS_LOG: read time and tokens, time to first part and to the end, cost."""
        try:
            read = next((e for e in run.events if e["event"] == "spec" and e.get("changelog") is None), None)
            first = next((e for e in run.events if e["event"] == "compiled" and e.get("ok")), None)
            done = run.events[-1] if run.events and run.events[-1]["event"] == "done" else {}
            call = (read or {}).get("call") or {}
            rec = {"run": run.id, "kind": run.kind, "preset": config.PRESET, "read_seconds": call.get("seconds"),
                   "read_effort": call.get("effort"), "read_output_tokens": call.get("output_tokens"),
                   "read_reasoning_tokens": call.get("reasoning_tokens"),
                   "first_part_t": (first or {}).get("t"), "done_t": done.get("t"), "status": done.get("status"),
                   "corrections": done.get("iterations"), "cost_usd": done.get("cost_usd")}
            log_line(rec)
        except Exception:  # noqa: BLE001 - logging must never break a run
            traceback.print_exc()

    def cleanup(self, max_age_hours=config.RETENTION_HOURS):
        """Delete runs (memory and files) older than max_age_hours, including folders left by earlier containers."""
        cutoff = time.time() - max_age_hours * 3600
        with self.lock:
            for run_id in [k for k, r in self.runs.items() if r.created < cutoff and r.done]:
                del self.runs[run_id]
        if os.path.isdir(config.RUNS_DIR):
            for name in os.listdir(config.RUNS_DIR):
                path = os.path.join(config.RUNS_DIR, name)
                if os.path.getmtime(path) < cutoff and name not in self.runs:
                    shutil.rmtree(path, ignore_errors=True)
