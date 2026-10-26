# Hephaestus — project context

Entry for the **Meta Global AI Developer Hackathon** (Indie track, solo), Oct 26 – Nov 4, 2026.
Submission due **Wed Nov 4, 9:00 PM ET (Thu Nov 5, 03:00 Zurich)**: public repo, 3-minute demo video, live product.

## What it is
Hephaestus turns a **sketch, a text prompt or a photo of a real object** into a **parametric CAD part** (editable parameters, STEP/STL export).
**Talos** is the agent inside it: generate → build → verify against the input → correct, until the geometry matches.

## Core design
- The model never writes free-form CAD code first. It outputs a **structured part spec (JSON)**; our own compiler turns the spec into CadQuery deterministically. Free-form CadQuery is only a fallback.
- All inputs converge on the same spec. Parameters/sliders and voice edits change spec values and recompile without model calls.
- Verification: render orthographic silhouettes and compare with the input masks (IoU), check measured dimensions against callouts, plus a model side-by-side check. Discrepancies go back to the model as feedback.

## Stack (planned)
- Models via **Meta Model API** (`https://api.meta.ai/v1`, OpenAI-compatible Responses API): Muse Spark 1.3 (vision + structured output), SAM 3.1 (segmentation), Muse Voice Transcribe (voice edits).
- Backend: Python, FastAPI, CadQuery in sandboxes (Modal).
- Frontend: Netlify, three.js viewer, parameter sliders, iteration timeline. Per-IP rate limit on the live site.

## Scope tiers
- **Must:** prompt + sketch input, spec compiler, verification loop, web app with sliders, STL/STEP download, benchmark, video.
- **Should:** photo of a real object (2.5D parts, reference card for scale), voice edits.
- **Could:** measuring the printed part, Muse Spark Open switch (open-weights bonus prize).

## Rules that matter
- Only Meta models in the product pipeline (judging stage 1 checks this). No Claude/OpenAI calls in the app.
- Everything shown must be built during the hackathon; commit small and often so the history proves it.
- Avoid looking like a "simple image analyzer" (banned category): lead with the generated part and the verification loop.
- Judging: Impact 30%, Use of Meta tooling 25%, Technical depth 20%, Demo 25%.

## Budget
$150 Meta API credits for the whole project. Default to `muse-spark-1.3-contributor` (≈12× cheaper) for development; use `muse-spark-1.3` only to compare or for the final demo. Keep `--max-usd` guards on scripts. Never commit API keys (`.env` is gitignored).

## Repo layout
- `hephaestus/` — spec v1 and the deterministic compiler (no model calls).
  - `spec.py`: pydantic models; `json_schema()` exports a ref-free schema for structured output (`python -m hephaestus.spec`).
  - `expr.py`: safe AST evaluator for parameter expressions.
  - `compile.py`: spec → CadQuery solid, STEP/STL, metrics, silhouettes; `SpecError` messages are written as model feedback. CLI: `python -m hephaestus.compile examples/plate.json --out out/ [--set name=value]`.
  - `render.py`: top/front/side binary masks rasterised from the STL triangles, STL writer, `count_holes`.
- `examples/` — v1 specs: plate, l_bracket, block (the three spike parts), bushing (revolve), enclosure (shell).
- `docs/spec-v1.md` — spec reference with one example per feature type.
- `tests/` — pytest (`python -m pytest tests`); analytic volumes, silhouettes, errors, schema export.
- `experiments/reading-spike/` — first test: can Muse Spark read a dimensioned sketch into a correct spec? Auto-scored against ground truth. Run `python spike.py`. Uses the older v0 box spec.
- `requirements.txt` — `pip install -r requirements.txt` (CadQuery 2.8 installs from pip).

## Conventions
- English everywhere in code and docs. mm units. Coordinates: origin at lower-left-front of the bounding box, X right, Y back, Z up.
- Owner prefers direct, concise communication and honest pushback.
