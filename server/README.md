# Hephaestus backend

FastAPI app (`api.py`) deployed on Modal (`modal_app.py`). The frontend (`web/`) is static on Netlify.

## Local development (no API calls)
```
MOCK_MODELS=1 uvicorn server.api:create_app --factory --port 8000
cd web && python -m http.server 8080        # open http://localhost:8080/?api=http://localhost:8000
```
`MOCK_MODELS=1` replays recorded model outputs (`mock_data/`, built from real runs by `python -m server.build_gallery`)
through the real compile/check/loop code. `MOCK_DELAY` (seconds per model call) makes it feel like streaming.
Tests: `python -m pytest tests/test_server.py`.

## Deploy
Pushes to `main` that touch `server/` or `hephaestus/` deploy through GitHub Actions
(`.github/workflows/deploy-backend.yml`). One-time setup: add repository secrets `MODAL_TOKEN_ID` and
`MODAL_TOKEN_SECRET` (GitHub → Settings → Secrets and variables → Actions). From a machine with direct internet:
`modal deploy server/modal_app.py`.

The URL is `https://<modal-workspace>--hephaestus-web.modal.run`; put it in `web/config.js`.

**Judging period (Nov 4–9):** keep one container warm: Actions → Deploy backend → Run workflow with
`min_containers: 1` (or `HEPH_MIN_CONTAINERS=1 modal deploy server/modal_app.py`). Run it again with `0` afterwards.

## Limits and spend
- 5 runs per IP per hour, 8 recompiles per IP per second, $0.40 per run, **$5 per UTC day** across all users.
  When a limit trips, the API answers 429 with "busy, try an example".
- Every production model call is a line in `spend.jsonl` on the volume `hephaestus-data`:
  `modal volume get hephaestus-data spend.jsonl -`. `GET /status` shows today's total.
- Every run is a line in `runs.jsonl` (read seconds, effort, output and reasoning tokens, time to the first part and
  to the end, status, cost); `GET /status` shows the last 10 under `recent_runs`.
- Runs (uploads, events, files) are deleted after 24 h. Uploads are re-encoded: EXIF removed, at most 2000 px.

Environment (all optional): `DAILY_CAP_USD`, `RUN_CAP_USD`, `RUNS_PER_IP_PER_HOUR`, `COMPILES_PER_IP_PER_SECOND`,
`TALOS_PRESET` (default `live`), `DATA_DIR`, `MOCK_MODELS`, `MOCK_DELAY`.

## API
| route | |
|---|---|
| `POST /runs` | multipart `image` (JPEG/PNG/HEIC ≤ 10 MB) or `prompt` → `{run_id}` |
| `GET /runs/{id}/events` | Server-Sent Events (`event: talos`, `id` = index; reconnect resumes after `Last-Event-ID`, `event: end` when done) |
| `GET /runs/{id}/files/{path}` | `input.jpg`, `iter_<k>/part.glb`, `part.stl`, `part.step`, `sheet.png`, masks, `metrics.json` |
| `POST /compile` | `{spec, overrides}` → `{ok, metrics, glb_b64, files: {part.stl, part.step, spec.json}}` or `{ok: false, errors}` |
| `GET /status` | mock mode, today's spend, daily cap, busy |
