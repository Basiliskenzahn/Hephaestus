# Reading spike v2

Question: can Muse Spark fill in **part spec v1** from a dimensioned drawing, with geometry and parameters right?

## Run
```
python schema_probe.py [--strict]            # one call: is the v1 schema accepted as structured output?
python spike2.py --set clean --runs 3        # contributor model, medium effort, --max-usd 1.0 default
python spike2.py --set photo|rough|all --runs 3
python spike2.py --rescore                   # re-score saved outputs, no API calls
python score.py                              # sanity: true specs score perfect against themselves
python -m pytest test_score.py               # the scorer catches rotated slots, moved holes, missing chamfers
```
Images are the v1 spike's (`../reading-spike/sketches/`); ground truth is `examples/{plate,l_bracket,block}.json`
plus the written drawing dimensions in `ground_truth.json`. Prompt and few-shot: `prompt.py`, `fewshot_pillow_block.json`.

## Scoring (on compiled geometry, so equivalent specs count)
compiles · bbox within 0.5 mm per axis · volume IoU with the true solid ≥ 0.98 · every written drawing dimension measured
on the compiled part within 0.5 mm. `perfect` = all of these. Also logged: parameter count, expressions, and the
**x-param check**: widen the part by 20 mm through the parameter that drives overall X and compare with the true spec
widened the same way (IoU ≥ 0.98).

## Results (muse-spark-1.3-contributor, medium, Oct 2026)
| set | perfect | compiles | x-param ok | cost | median / max latency | median / max output tokens |
|---|---|---|---|---|---|---|
| clean | 9/9 | 9/9 | 6/9 | $0.008 | 20 s / 47 s | 2.5k / 5.4k |
| photo | 9/9 | 9/9 | 7/9 | $0.010 | 29 s / 79 s | 3.4k / 8.9k |
| rough | 8/9 | 9/9 | 6/9 | $0.008 | 19 s / 60 s | 2.4k / 6.7k |

- Schema: the exported v1 schema is accepted as non-strict `json_schema` and every output validated. `strict: true` is
  rejected (400: `required` must list every key in `properties`).
- Only failure: rough L-bracket run 1 rotated the base slot 90° (same miss as v1), despite the prompt line on slot direction.
- x-param misses are mostly the plate: the model keeps the drawn 64 / 34 hole pitches as independent parameters, so
  widening keeps the hole pattern fixed instead of keeping the 8 mm insets. Defensible intent, but not the one in `examples/`.
