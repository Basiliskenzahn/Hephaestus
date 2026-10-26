# Talos reading spike

First experiment of the build. Question it answers: can Muse Spark read a dimensioned sketch into a correct part spec?

## Setup
```
pip install requests numpy matplotlib
export MODEL_API_KEY=...        # Windows PowerShell: $env:MODEL_API_KEY="..."
```

## Run
```
python spike.py                                      # 3 sketches, contributor model, medium reasoning
python spike.py --model muse-spark-1.3 --effort high # stronger setting, for comparison
python spike.py --runs 3                             # consistency check
```
Each result is saved in `results/<model>/` with the spec, token usage, cost and score.
`--max-usd` (default 1.0) stops a run before it spends more than that.

## Files
- `sketches/` – three test parts: plate with hole pattern, L-bracket (3 views, slot, holes in the upright), block with fillets and chamfer
- `ground_truth.json` – the correct spec for each sketch
- `schema.py` – spec v0 (JSON schema) and the system prompt
- `draw_sketches.py` – regenerates the sketches

## Reading the score
- `overall` – bounding box dims within ±0.5 mm
- `body_iou` – volume overlap of the predicted solid with the true solid (1.0 = identical shape)
- `features` – holes, slots, fillets and chamfers with correct position and size
- `extra` – invented features (should be 0)
- `perfect` – everything right

## Go / no-go
- 2 of 3 perfect or near-perfect → sketch input stays the core; the verification loop handles the rest.
- Mostly wrong body shape or hole positions → prompt + photo become the core, sketch becomes a stretch feature.
