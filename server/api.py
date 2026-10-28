"""Hephaestus HTTP API (FastAPI). Deployed on Modal (modal_app.py); runs locally with
    MOCK_MODELS=1 uvicorn server.api:create_app --factory --port 8000

POST /runs                     image upload (multipart field "image") or text ("prompt") -> {run_id}
GET  /runs/{id}/events         Server-Sent Events: the Talos events, replayed from the start (or Last-Event-ID)
GET  /runs/{id}/files/{path}   files of a run: input.jpg, iter_<k>/part.glb|stl|step, sheet.png, ...
POST /compile                  {spec, overrides} -> recompiled part (GLB inline, STL/STEP links), no model call
POST /transcribe               WAV clip (multipart "audio", mono 16-bit 16/24 kHz) -> {transcript}: Muse Voice Transcribe
POST /edit                     {spec, instruction, callouts?, base_spec?} -> edited part, or a clarification question
GET  /compiles/{id}/{name}     part.stl / part.step / spec.json of a /compile result
GET  /status                   mock mode, today's spend vs the daily cap, timing of the last 10 runs
"""
import asyncio
import base64
import collections
import json
import os
import secrets
import threading
import time

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from hephaestus import edit as edits
from hephaestus import meta_api, sam, voice
from hephaestus.talos import PRESETS
from hephaestus.compile import SpecError, compile as compile_spec  # imports CadQuery at startup

from . import config, mock
from .images import UploadError, normalise
from .runs import GuardedBudget, RunManager, log_line, spent_today

BUSY = ("Hephaestus is busy right now ({why}). Try one of the examples: they replay recorded runs instantly.")


class RateLimiter:
    """Sliding-window limit per key."""

    def __init__(self, limit, window_s):
        self.limit, self.window = limit, window_s
        self.hits, self.lock = collections.defaultdict(collections.deque), threading.Lock()

    def allow(self, key):
        now = time.time()
        with self.lock:
            q = self.hits[key]
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


def client_ip(request):
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


def busy(why, status=429):
    return JSONResponse({"error": "busy", "message": BUSY.format(why=why)}, status_code=status)


def make_client(run, budget):
    if config.MOCK_MODELS:
        rec = mock.text_recording() if run.kind == "text" else mock.next_image_recording()
        return mock.MockClient(rec, budget)
    os.environ.setdefault("SPEND_LOG", config.SPEND_LOG)
    os.environ["SPEND_SCRIPT"] = "server"
    client = meta_api.Client(budget=budget)
    client.tag = run.id
    return client


def create_app(on_finish=None):
    os.makedirs(config.RUNS_DIR, exist_ok=True)
    os.environ["SPEND_LOG"] = config.SPEND_LOG
    app = FastAPI(title="Hephaestus", docs_url=None, redoc_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
                       expose_headers=["*"])
    runs = RunManager(make_client, on_finish)
    threading.Thread(target=sam.warm, daemon=True).start()       # SAM mask decoding runs in worker processes
    run_limit = RateLimiter(config.RUNS_PER_IP_PER_HOUR, 3600)
    compile_limit = RateLimiter(max(1, int(config.COMPILES_PER_IP_PER_SECOND)), 1.0)
    edit_limit = RateLimiter(config.EDITS_PER_IP_PER_HOUR, 3600)        # shared by /transcribe and /edit
    compiles = collections.OrderedDict()          # compile_id -> {name: bytes}, small LRU
    app.state.runs = runs

    def janitor():
        while True:
            runs.cleanup()
            time.sleep(600)

    threading.Thread(target=janitor, daemon=True).start()

    @app.get("/status")
    def status():
        spent = 0.0 if config.MOCK_MODELS else spent_today()
        recent = []
        if os.path.exists(config.RUNS_LOG):
            with open(config.RUNS_LOG) as f:
                recent = [json.loads(line) for line in f.readlines()[-10:]]
        return {"mock": config.MOCK_MODELS, "preset": config.PRESET, "spent_today_usd": round(spent, 4),
                "daily_cap_usd": config.DAILY_CAP_USD, "busy": spent >= config.DAILY_CAP_USD,
                "active_runs": sum(not r.done for r in runs.runs.values()),
                "recent_runs": recent}          # timing only: no IPs, no content

    @app.post("/runs")
    async def create_run(request: Request, image: UploadFile | None = File(None), prompt: str | None = Form(None)):
        ip = client_ip(request)
        prompt = (prompt or "").strip() or None
        if image is None and prompt is None:
            raise HTTPException(400, "send an image or a text prompt")
        if prompt and len(prompt) > config.MAX_PROMPT_CHARS:
            raise HTTPException(400, f"the description is too long (max {config.MAX_PROMPT_CHARS} characters)")
        if not config.MOCK_MODELS and spent_today() >= config.DAILY_CAP_USD:
            return busy("today's budget for live runs is used up")
        data = None
        if image is not None and prompt is None:
            raw = await image.read(config.MAX_UPLOAD_BYTES + 1)
            try:
                data = normalise(raw)
            except UploadError as e:
                raise HTTPException(400, str(e)) from None
        if not run_limit.allow(ip):
            return busy(f"you have started {config.RUNS_PER_IP_PER_HOUR} runs in the last hour")
        run = runs.start(ip, image_bytes=data, prompt=prompt if data is None else None)
        return {"run_id": run.id, "kind": run.kind, "mock": config.MOCK_MODELS,
                "events": f"/runs/{run.id}/events", "files": f"/runs/{run.id}/files/"}

    @app.get("/runs/{run_id}/events")
    async def events(run_id: str, request: Request):
        run = runs.get(run_id)
        if run is None:
            raise HTTPException(404, "unknown or expired run")
        last = request.headers.get("last-event-id") or request.query_params.get("from")
        start = int(last) + 1 if last not in (None, "") and str(last).lstrip("-").isdigit() else 0

        async def stream():
            i, idle = start, 0.0
            yield "retry: 2000\n\n"
            while True:
                while i < len(run.events):
                    yield f"id: {i}\nevent: talos\ndata: {json.dumps(run.events[i])}\n\n"
                    i += 1
                    idle = 0.0
                if run.done and i >= len(run.events):
                    yield "event: end\ndata: {}\n\n"
                    return
                if await request.is_disconnected():
                    return
                await asyncio.sleep(0.2)
                idle += 0.2
                if idle >= 15:
                    yield ": keep-alive\n\n"
                    idle = 0.0

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/runs/{run_id}/files/{path:path}")
    def run_file(run_id: str, path: str):
        run = runs.get(run_id)
        if run is None:
            raise HTTPException(404, "unknown or expired run")
        full = os.path.realpath(os.path.join(run.dir, path))
        if not full.startswith(os.path.realpath(run.dir) + os.sep) or not os.path.isfile(full):
            raise HTTPException(404, "no such file")
        return FileResponse(full, filename=os.path.basename(full) if full.endswith((".stl", ".step")) else None)

    @app.post("/compile")
    async def recompile(request: Request):
        if not compile_limit.allow(client_ip(request)):
            return JSONResponse({"error": "slow down", "message": "too many recompiles per second"}, status_code=429)
        body = await request.json()
        spec, overrides = body.get("spec"), body.get("overrides") or {}
        t0 = time.perf_counter()
        try:
            result = await asyncio.to_thread(compile_spec, spec, overrides, 0.2, False)
        except SpecError as e:
            return {"ok": False, "errors": e.errors, "features": e.feature_status}
        except Exception as e:  # noqa: BLE001 - a malformed request should come back as a readable error
            return {"ok": False, "errors": [f"could not compile: {e}"]}
        return {**package(result), "seconds": round(time.perf_counter() - t0, 3)}

    def package(result):
        """A compiled part for the browser: GLB inline, STL/STEP/spec behind short-lived links."""
        import trimesh, io  # noqa: E401 - local: only needed here
        glb = trimesh.load(io.BytesIO(result.stl), file_type="stl").export(file_type="glb")
        cid = secrets.token_urlsafe(8)
        compiles[cid] = {"part.stl": result.stl, "part.step": result.step,
                         "spec.json": json.dumps(result.spec.model_dump(), indent=2).encode()}
        while len(compiles) > 64:
            compiles.popitem(last=False)
        return {"ok": True, "compile_id": cid, "metrics": result.metrics, "warnings": result.warnings,
                "params": result.params, "glb_b64": base64.b64encode(glb).decode(),
                "files": {n: f"/compiles/{cid}/{n}" for n in compiles[cid]}}

    def model_budget(request):
        """None if the caller may spend now, else the JSONResponse to return."""
        if not config.MOCK_MODELS and spent_today() >= config.DAILY_CAP_USD:
            return busy("today's budget for live runs is used up")
        if not edit_limit.allow(client_ip(request)):
            return busy(f"you have made {config.EDITS_PER_IP_PER_HOUR} edits in the last hour")
        return None

    @app.post("/transcribe")
    async def transcribe(request: Request, audio: UploadFile = File(...)):
        data = await audio.read(config.MAX_AUDIO_BYTES + 1)
        if len(data) > config.MAX_AUDIO_BYTES:
            raise HTTPException(400, "the recording is too long; keep an edit under 30 s")
        try:
            voice.wav_info(data)
        except voice.VoiceError as e:
            raise HTTPException(400, str(e)) from None
        refused = model_budget(request)
        if refused:
            return refused
        if config.MOCK_MODELS:
            return {"transcript": mock.transcript(), "audio_ms": int(voice.wav_info(data)[0] * 1000), "seconds": 0.0,
                    "mock": True}
        os.environ["SPEND_SCRIPT"] = "server"
        try:
            out = await asyncio.to_thread(voice.transcribe, data, GuardedBudget(config.EDIT_CAP_USD), "transcribe")
        except (voice.VoiceError, meta_api.ModelError) as e:
            return JSONResponse({"error": "transcription failed", "message": str(e)[:300]}, status_code=502)
        log_line({"kind": "transcribe", "seconds": out["seconds"], "audio_ms": out["audio_ms"],
                  "cost_usd": out["cost_usd"]})
        return out

    @app.post("/edit")
    async def edit_part(request: Request):
        body = await request.json()
        spec, instruction = body.get("spec"), (body.get("instruction") or "").strip()
        if not isinstance(spec, dict) or not instruction:
            raise HTTPException(400, "send the current spec and an instruction")
        if len(instruction) > config.MAX_INSTRUCTION_CHARS:
            raise HTTPException(400, f"the instruction is too long (max {config.MAX_INSTRUCTION_CHARS} characters)")
        refused = model_budget(request)
        if refused:
            return refused
        settings = PRESETS[config.PRESET]
        if config.MOCK_MODELS:
            client = mock.MockEditClient(instruction)
        else:
            os.environ["SPEND_SCRIPT"] = "server"
            client = meta_api.Client(budget=GuardedBudget(config.EDIT_CAP_USD))
            client.tag = "edit"
        try:
            rec = await asyncio.to_thread(edits.edit_part, spec, instruction, client, settings.read_model,
                                          edits.EDIT_EFFORT, body.get("callouts"), body.get("base_spec"))
        except meta_api.ModelError as e:
            return JSONResponse({"error": "edit failed", "message": str(e)[:300]}, status_code=502)
        out = edits.public(rec)
        if rec["result"] is not None:
            out.update(await asyncio.to_thread(package, rec["result"]))
        log_line({"kind": "edit", "status": rec["status"], "seconds": rec["seconds"],
                  "model_seconds": round(sum(c["seconds"] for c in rec["calls"]), 1), "calls": len(rec["calls"]),
                  "cost_usd": rec["cost_usd"]})
        return out

    @app.get("/compiles/{cid}/{name}")
    def compiled_file(cid: str, name: str):
        files = compiles.get(cid)
        if not files or name not in files:
            raise HTTPException(404, "expired; recompile")
        media = {"part.stl": "model/stl", "part.step": "application/step", "spec.json": "application/json"}[name]
        return Response(files[name], media_type=media,
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    return app
